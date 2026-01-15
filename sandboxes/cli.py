#!/usr/bin/env python
"""CLI for cased-sandboxes."""

import asyncio
import json
import os
import sys

import click


def get_provider(name: str):
    """Get a provider instance by name."""
    from sandboxes.providers.cloudflare import CloudflareProvider
    from sandboxes.providers.daytona import DaytonaProvider
    from sandboxes.providers.e2b import E2BProvider
    from sandboxes.providers.modal import ModalProvider
    from sandboxes.providers.sprites import SpritesProvider

    providers = {
        "e2b": E2BProvider,
        "modal": ModalProvider,
        "daytona": DaytonaProvider,
        "sprites": SpritesProvider,
        "cloudflare": CloudflareProvider,
    }

    if name not in providers:
        click.echo(f"❌ Unknown provider: {name}", err=True)
        click.echo(f"Available providers: {', '.join(providers.keys())}", err=True)
        sys.exit(1)

    try:
        return providers[name]()
    except Exception as e:
        click.echo(f"❌ Failed to initialize {name}: {e}", err=True)
        sys.exit(1)


@click.group()
@click.version_option(version="0.2.3", prog_name="cased-sandboxes")
def cli():
    """Universal AI code execution sandboxes."""
    pass


@cli.command()
@click.argument("command", required=False)
@click.option("--file", "-f", type=click.Path(exists=True), help="Execute code from a file")
@click.option("--language", "--lang", help="Language/runtime (python, node, go, etc.)")
@click.option("--provider", "-p", default="daytona", help="Provider to use (daytona, e2b, modal)")
@click.option("--image", "-i", help="Docker image or template")
@click.option("--env", "-e", multiple=True, help="Environment variables (KEY=VALUE)")
@click.option("--label", "-l", multiple=True, help="Labels (KEY=VALUE)")
@click.option("--timeout", "-t", default=120, help="Timeout in seconds")
@click.option("--reuse/--no-reuse", default=True, help="Reuse existing sandbox with same labels")
@click.option("--keep/--no-keep", default=False, help="Keep sandbox after execution")
@click.option("--deps/--no-deps", default=False, help="Auto-install dependencies (go.mod)")
def run(command, file, language, provider, image, env, label, timeout, reuse, keep, deps):
    """Run a command in a sandbox.

    Examples:
        # Direct command
        sandboxes run "python3 --version"

        # From file
        sandboxes run --file script.py --language python
        sandboxes run -f main.go --lang go

        # From stdin/pipe
        echo 'console.log("Hello!")' | sandboxes run --lang node
        cat script.ts | sandboxes run --lang typescript

        # With auto-dependency installation (Go)
        sandboxes run --file main.go --deps

        # With options
        sandboxes run "npm install express" -p e2b
        sandboxes run "echo $MY_VAR" -e MY_VAR=hello
    """

    async def _run():
        # Determine language early
        lang = language
        if not lang and file:
            # Infer from file extension
            ext = os.path.splitext(file)[1].lower()
            lang_map = {
                ".py": "python",
                ".js": "node",
                ".ts": "typescript",
                ".go": "go",
                ".rs": "rust",
                ".rb": "ruby",
                ".java": "java",
                ".sh": "bash",
            }
            lang = lang_map.get(ext)

        # Determine what to execute
        code_to_execute = None

        if file:
            # Read from file
            with open(file) as f:
                code_to_execute = f.read()
        elif command:
            # Use command directly
            code_to_execute = command
        elif not sys.stdin.isatty():
            # Read from stdin/pipe
            code_to_execute = sys.stdin.read()
        else:
            click.echo(
                "❌ No command provided. Use:\n"
                "  - sandboxes run 'command'\n"
                "  - sandboxes run --file script.py\n"
                "  - echo 'code' | sandboxes run --lang python",
                err=True,
            )
            sys.exit(1)

        # If we have code but no command wrapper, build execution command
        if code_to_execute and code_to_execute != command:
            # Build execution command based on language
            if lang == "python":
                code_to_execute = f"python3 -c '''{code_to_execute}'''"
            elif lang == "node" or lang == "javascript":
                # Escape single quotes and use node -e
                escaped = code_to_execute.replace("'", "'\\''")
                code_to_execute = f"node -e '{escaped}'"
            elif lang == "typescript":
                # Write to temp file and use ts-node
                code_to_execute = f"cat > /tmp/script.ts << 'EOF'\n{code_to_execute}\nEOF\nnpx -y ts-node /tmp/script.ts"
            elif lang == "go":
                # Write to temp file and use go run
                if deps:
                    # With deps, we'll init a module and download dependencies
                    code_to_execute = (
                        f"mkdir -p /tmp/goapp && cd /tmp/goapp && "
                        f"cat > main.go << 'EOF'\n{code_to_execute}\nEOF\n"
                        f"go mod init app 2>/dev/null || true && "
                        f"go mod tidy && go mod download && go run main.go"
                    )
                else:
                    code_to_execute = (
                        f"cat > /tmp/main.go << 'EOF'\n{code_to_execute}\nEOF\ngo run /tmp/main.go"
                    )
            elif lang == "rust":
                # Write, compile, and run
                code_to_execute = f"cat > /tmp/main.rs << 'EOF'\n{code_to_execute}\nEOF\nrustc /tmp/main.rs -o /tmp/app && /tmp/app"
            elif lang == "bash" or lang == "sh":
                # Execute directly
                pass
            elif lang:
                click.echo(f"⚠️  Unknown language '{lang}', executing code as-is", err=True)

        # Parse environment variables
        env_vars = {}
        for e in env:
            if "=" in e:
                key, value = e.split("=", 1)
                env_vars[key] = value

        # Parse labels
        labels = {}
        for lbl in label:
            if "=" in lbl:
                key, value = lbl.split("=", 1)
                labels[key] = value

        from sandboxes import SandboxConfig

        config = SandboxConfig(
            timeout_seconds=timeout,
            env_vars=env_vars,
            labels=labels or {"cli": "true"},
            image=image,  # Set image directly on config
        )

        # Get provider
        p = get_provider(provider)

        # Find or create sandbox
        sandbox = None
        if reuse and labels:
            sandbox = await p.find_sandbox(labels)

        if not sandbox:
            sandbox = await p.create_sandbox(config)

        # Handle dependency installation for Go
        if deps and lang == "go":
            # Look for go.mod in current directory or file's directory
            gomod_path = None
            if file:
                # Check same directory as the file
                file_dir = os.path.dirname(os.path.abspath(file))
                candidate = os.path.join(file_dir, "go.mod")
                if os.path.exists(candidate):
                    gomod_path = candidate
            else:
                # Check current working directory
                candidate = os.path.join(os.getcwd(), "go.mod")
                if os.path.exists(candidate):
                    gomod_path = candidate

            # If go.mod found, upload it and also check for go.sum
            if gomod_path:
                await p.upload_file(sandbox.id, gomod_path, "/tmp/goapp/go.mod")
                # Also upload go.sum if it exists
                gosum_path = gomod_path.replace("go.mod", "go.sum")
                if os.path.exists(gosum_path):
                    await p.upload_file(sandbox.id, gosum_path, "/tmp/goapp/go.sum")

        # Execute command
        result = await p.execute_command(sandbox.id, code_to_execute, env_vars=env_vars)

        # Output results (just stdout/stderr, nothing else)
        if result.stdout:
            click.echo(result.stdout, nl=False)

        if result.stderr:
            click.echo(result.stderr, err=True, nl=False)

        # Cleanup
        if not keep:
            await p.destroy_sandbox(sandbox.id)

        return result.exit_code

    exit_code = asyncio.run(_run())
    sys.exit(exit_code)


@cli.command()
@click.option("--provider", "-p", help="Filter by provider")
@click.option("--label", "-l", multiple=True, help="Filter by labels (KEY=VALUE)")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
def list(provider, label, output_json):
    """List all sandboxes.

    Examples:
        cased-sandboxes list
        cased-sandboxes list -p modal
        cased-sandboxes list -l env=prod
    """

    async def _list():
        # Parse labels
        labels = {}
        for lbl in label:
            if "=" in lbl:
                key, value = lbl.split("=", 1)
                labels[key] = value

        all_sandboxes = []

        # Get sandboxes from each provider
        providers_to_check = [provider] if provider else ["e2b", "modal", "daytona"]

        for p_name in providers_to_check:
            try:
                p = get_provider(p_name)
                sandboxes = await p.list_sandboxes(labels=labels if labels else None)
                for s in sandboxes:
                    all_sandboxes.append(
                        {
                            "id": s.id,
                            "provider": s.provider,
                            "state": s.state.value if hasattr(s.state, "value") else str(s.state),
                            "labels": json.dumps(s.labels) if s.labels else "",
                        }
                    )
            except Exception as e:
                if not output_json:
                    import traceback

                    click.echo(f"⚠️  {p_name}: {e}", err=True)
                    # For debugging
                    traceback.print_exc()

        if output_json:
            click.echo(json.dumps(all_sandboxes, indent=2))
        else:
            if all_sandboxes:
                from tabulate import tabulate

                headers = ["ID", "Provider", "State", "Labels"]
                rows = [
                    [s["id"][:20], s["provider"], s["state"], s["labels"][:30]]
                    for s in all_sandboxes
                ]
                click.echo(tabulate(rows, headers=headers, tablefmt="simple"))
                click.echo(f"\n📦 Total: {len(all_sandboxes)} sandboxes")
            else:
                click.echo("No sandboxes found")

    asyncio.run(_list())


@cli.command()
@click.argument("sandbox_id")
@click.option("--provider", "-p", required=True, help="Provider (e2b, modal, daytona)")
def destroy(sandbox_id, provider):
    """Destroy a sandbox.

    Examples:
        cased-sandboxes destroy sb-abc123 -p modal
    """

    async def _destroy():
        p = get_provider(provider)
        destroyed = await p.destroy_sandbox(sandbox_id)
        if not destroyed:
            sys.exit(1)

    asyncio.run(_destroy())


@cli.command()
@click.argument("sandbox_id")
@click.argument("command")
@click.option("--provider", "-p", required=True, help="Provider (e2b, modal, daytona)")
@click.option("--env", "-e", multiple=True, help="Environment variables (KEY=VALUE)")
def exec(sandbox_id, command, provider, env):
    """Execute a command in an existing sandbox.

    Examples:
        cased-sandboxes exec sb-abc123 "ls -la" -p modal
        cased-sandboxes exec sb-xyz "python script.py" -p e2b -e DEBUG=1
    """

    async def _exec():
        # Parse environment variables
        env_vars = {}
        for e in env:
            if "=" in e:
                key, value = e.split("=", 1)
                env_vars[key] = value

        p = get_provider(provider)

        result = await p.execute_command(sandbox_id, command, env_vars=env_vars)

        # Output results
        if result.stdout:
            click.echo(result.stdout, nl=False)

        if result.stderr:
            click.echo(result.stderr, err=True, nl=False)

        if result.exit_code != 0:
            sys.exit(result.exit_code)

    asyncio.run(_exec())


@cli.command()
@click.option("--provider", "-p", help="Test specific provider")
def test(provider):
    """Test provider connectivity and functionality.

    Examples:
        cased-sandboxes test
        cased-sandboxes test -p e2b
    """

    async def _test():
        providers_to_test = [provider] if provider else ["e2b", "modal", "daytona"]

        results = []
        for p_name in providers_to_test:
            click.echo(f"\n🔬 Testing {p_name}...")

            try:
                p = get_provider(p_name)

                # Test create
                click.echo("  Creating sandbox...")
                from sandboxes import SandboxConfig

                config = SandboxConfig(labels={"test": "cli"})
                if p_name == "modal":
                    config.provider_config = {"image": "python:3.11-slim"}

                sandbox = await p.create_sandbox(config)
                click.echo(f"  ✅ Created: {sandbox.id}")

                # Test execute
                click.echo("  Executing command...")
                result = await p.execute_command(sandbox.id, "echo 'Hello from CLI test'")
                if result.success and "Hello from CLI test" in result.stdout:
                    click.echo("  ✅ Execution successful")
                else:
                    click.echo("  ❌ Execution failed", err=True)

                # Test destroy
                click.echo("  Destroying sandbox...")
                await p.destroy_sandbox(sandbox.id)
                click.echo("  ✅ Destroyed")

                results.append((p_name, "✅ Working"))

            except Exception as e:
                click.echo(f"  ❌ Error: {e}", err=True)
                results.append((p_name, f"❌ Failed: {str(e)[:50]}"))

        # Summary
        click.echo("\n" + "=" * 50)
        click.echo("📊 Test Results")
        click.echo("=" * 50)
        for p_name, status in results:
            click.echo(f"{p_name:10} {status}")

    asyncio.run(_test())


@cli.command()
def providers():
    """List available providers and their status."""
    click.echo("\nAvailable Providers")
    click.echo("=" * 50)

    import shutil

    providers = [
        ("e2b", "E2B_API_KEY", "E2B cloud sandboxes", False),
        ("modal", "~/.modal.toml", "Modal serverless containers", False),
        ("daytona", "DAYTONA_API_KEY", "Daytona development environments", False),
        (
            "sprites",
            "SPRITES_TOKEN or sprite CLI",
            "Fly.io Sprites (Claude Code pre-installed)",
            False,
        ),
        (
            "cloudflare",
            "CLOUDFLARE_API_TOKEN",
            "Cloudflare Workers + Containers (⚠️  experimental)",
            True,
        ),
    ]

    for name, auth, description, is_experimental in providers:
        # Check if configured
        if name == "modal":
            configured = os.path.exists(os.path.expanduser("~/.modal.toml"))
        elif name == "e2b":
            configured = bool(os.getenv("E2B_API_KEY"))
        elif name == "daytona":
            configured = bool(os.getenv("DAYTONA_API_KEY"))
        elif name == "sprites":
            configured = bool(os.getenv("SPRITES_TOKEN")) or shutil.which("sprite") is not None
        elif name == "cloudflare":
            configured = bool(os.getenv("CLOUDFLARE_API_TOKEN") or os.getenv("CLOUDFLARE_API_KEY"))
        else:
            configured = False

        status = "✅ Configured" if configured else "❌ Not configured"

        click.echo(f"\n{name}")
        click.echo(f"  Status: {status}")
        click.echo(f"  Auth: {auth}")
        click.echo(f"  Description: {description}")
        if is_experimental:
            click.echo("  Note: Requires self-hosted Worker deployment")

    click.echo("\n💡 To configure a provider:")
    click.echo("  E2B: export E2B_API_KEY=your_key")
    click.echo("  Modal: modal token set")
    click.echo("  Daytona: export DAYTONA_API_KEY=your_key")
    click.echo("  Sprites: sprite login (or export SPRITES_TOKEN=your_token)")
    click.echo(
        "  Cloudflare (experimental): Deploy Worker from https://github.com/cloudflare/sandbox-sdk"
    )


def _run_claude_sprites(name: str | None, keep: bool, list_sandboxes: bool):
    """Run Claude Code using Sprites provider."""
    import shutil
    import subprocess

    if not shutil.which("sprite"):
        click.echo("❌ sprite CLI not found. Install with:", err=True)
        click.echo("   curl https://sprites.dev/install.sh | bash", err=True)
        click.echo("\nThen run: sprite login", err=True)
        sys.exit(1)

    # List existing sandboxes
    if list_sandboxes:
        result = subprocess.run(["sprite", "list"], capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            claude_sandboxes = [
                line for line in lines if "claude-" in line or (name and name in line)
            ]
            if claude_sandboxes:
                click.echo("Existing Claude sandboxes:")
                for line in claude_sandboxes:
                    click.echo(f"  {line}")
                click.echo("\nResume with: sandboxes claude -n <name>")
            else:
                click.echo("No Claude sandboxes found.")
                click.echo("Start one with: sandboxes claude")
        else:
            click.echo(result.stdout)
        return

    # Generate or use provided name
    if not name:
        import uuid

        name = f"claude-{uuid.uuid4().hex[:8]}"
        click.echo(f"Creating sandbox: {name}")

        result = subprocess.run(["sprite", "create", name], capture_output=True, text=True)
        if result.returncode != 0:
            click.echo(f"❌ Failed to create sandbox: {result.stderr}", err=True)
            sys.exit(1)
        click.echo(f"✓ Created {name}")
        created_new = True
    else:
        # Check if sandbox exists, create if not
        result = subprocess.run(["sprite", "list"], capture_output=True, text=True)
        # Parse output to find exact name match (avoid "claude" matching "claude-123")
        existing_names = {
            line.split()[0] for line in result.stdout.strip().split("\n") if line.strip()
        }
        if name in existing_names:
            click.echo(f"Resuming sandbox: {name}")
            created_new = False
            keep = True  # Named sandboxes are kept by default
        else:
            click.echo(f"Creating sandbox: {name}")
            result = subprocess.run(["sprite", "create", name], capture_output=True, text=True)
            if result.returncode != 0:
                click.echo(f"❌ Failed to create sandbox: {result.stderr}", err=True)
                sys.exit(1)
            click.echo(f"✓ Created {name}")
            created_new = True
            keep = True  # Named sandboxes are kept by default

    click.echo("\n🚀 Starting Claude Code...\n")

    try:
        # Run claude directly in the sprite with TTY allocation
        subprocess.run(["sprite", "exec", "-s", name, "-tty", "claude"])
    except KeyboardInterrupt:
        click.echo("\n")
    finally:
        if not keep and created_new:
            click.echo(f"\n🗑️  Destroying sandbox {name}...")
            subprocess.run(
                ["sprite", "destroy", "-s", name, "-force"],
                capture_output=True,
            )
            click.echo("✓ Destroyed")
        else:
            click.echo(f"\n💡 Resume anytime: sandboxes claude -n {name}")


def _run_claude_e2b():
    """Run Claude Code using E2B - SDK to create, CLI to connect."""
    import shutil
    import subprocess

    # Check for E2B CLI
    if not shutil.which("e2b"):
        click.echo("❌ E2B CLI not found. Install with:", err=True)
        click.echo("   npm install -g @e2b/cli", err=True)
        sys.exit(1)

    try:
        from e2b import Sandbox
    except ImportError:
        click.echo("❌ E2B SDK not installed. Install with:", err=True)
        click.echo("   uv pip install e2b", err=True)
        sys.exit(1)

    if not os.getenv("E2B_API_KEY"):
        click.echo("❌ E2B_API_KEY not set", err=True)
        sys.exit(1)

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        click.echo("❌ ANTHROPIC_API_KEY not set", err=True)
        sys.exit(1)

    click.echo("Creating E2B sandbox with Claude Code...")

    # Create sandbox with SDK to pass env vars
    sbx = Sandbox.create(
        template="anthropic-claude-code",
        timeout=3600,
        envs={"ANTHROPIC_API_KEY": api_key},
    )
    sandbox_id = sbx.sandbox_id
    click.echo(f"✓ Created sandbox: {sandbox_id}")

    # Set up a wrapper script to run claude on connect (avoid overwriting .bashrc)
    sbx.files.write("/tmp/start-claude.sh", "#!/bin/bash\nexec claude\n")
    sbx.commands.run("chmod +x /tmp/start-claude.sh")
    # Append to .bashrc to run our script
    sbx.commands.run("echo 'exec /tmp/start-claude.sh' >> /home/user/.bashrc")

    click.echo("\n🚀 Starting Claude Code...\n")

    # Connect with CLI for proper TTY handling
    try:
        subprocess.run(["e2b", "sandbox", "connect", sandbox_id])
    except KeyboardInterrupt:
        pass
    finally:
        click.echo("\n🗑️  Destroying sandbox...")
        try:
            sbx.kill()
            click.echo("✓ Destroyed")
        except Exception:
            click.echo("(sandbox may have already timed out)")


@cli.command()
@click.option("-n", "--name", default=None, help="Sandbox name (reuse existing, Sprites only)")
@click.option(
    "-p", "--provider", default="sprites", type=click.Choice(["sprites", "e2b"]), help="Provider"
)
@click.option("--keep", is_flag=True, help="Keep sandbox after exit (Sprites only)")
@click.option(
    "--list", "list_sandboxes", is_flag=True, help="List existing Claude sandboxes (Sprites only)"
)
def claude(name: str | None, provider: str, keep: bool, list_sandboxes: bool):
    """Start an interactive Claude Code session in a sandbox.

    This is the easiest way to use Claude Code safely:

        sandboxes claude

    Using E2B instead of Sprites:

        sandboxes claude -p e2b

    For a persistent dev environment (Sprites only):

        sandboxes claude -n myproject
        # Exit and come back later:
        sandboxes claude -n myproject

    To see existing sandboxes:

        sandboxes claude --list
    """
    if provider == "e2b":
        if name or list_sandboxes:
            click.echo("⚠️  Named sandboxes and --list only work with Sprites provider", err=True)
        _run_claude_e2b()
    else:
        _run_claude_sprites(name, keep, list_sandboxes)


@cli.command()
@click.option("-p", "--provider", default="sprites", help="Provider (default: sprites)")
@click.option("-n", "--name", default=None, help="Sandbox name (reuse existing)")
@click.option("--keep", is_flag=True, help="Keep sandbox after exit")
def shell(provider: str, name: str | None, keep: bool):
    """Open an interactive shell in a sandbox.

    For a raw shell (not Claude Code):

        sandboxes shell -n my-dev --keep
    """
    import shutil
    import subprocess

    if provider != "sprites":
        click.echo("❌ Interactive shell only supported for sprites provider", err=True)
        sys.exit(1)

    if not shutil.which("sprite"):
        click.echo("❌ sprite CLI not found. Install with:", err=True)
        click.echo("   curl https://sprites.dev/install.sh | bash", err=True)
        sys.exit(1)

    if not name:
        import uuid

        name = f"sandbox-{uuid.uuid4().hex[:8]}"
        click.echo(f"Creating sandbox: {name}")

        result = subprocess.run(["sprite", "create", name], capture_output=True, text=True)
        if result.returncode != 0:
            click.echo(f"❌ Failed to create sandbox: {result.stderr}", err=True)
            sys.exit(1)
        click.echo(f"✓ Created {name}")
        created_new = True
    else:
        click.echo(f"Using sandbox: {name}")
        created_new = False

    click.echo("\n🚀 Opening shell...\n")

    try:
        subprocess.run(["sprite", "console", "-s", name])
    except KeyboardInterrupt:
        click.echo("\n")
    finally:
        if not keep and created_new:
            click.echo(f"\n🗑️  Destroying sandbox {name}...")
            subprocess.run(
                ["sprite", "destroy", "-s", name, "-force"],
                capture_output=True,
            )
            click.echo("✓ Destroyed")
        elif keep or not created_new:
            click.echo(f"\n💡 Reconnect: sandboxes shell -n {name}")


if __name__ == "__main__":
    cli()
