# Contributing to Cased Sandboxes

We welcome contributions! This document provides guidelines for contributing to the project.

## Development Setup

1. Clone the repository:
```bash
git clone https://github.com/cased/sandboxes.git
cd sandboxes
```

2. Install with development dependencies:
```bash
uv pip install -e ".[dev,all]"
```

3. Set up pre-commit hooks:
```bash
pre-commit install
```

## Running Tests

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=sandboxes --cov-report=html

# Run specific test file
pytest tests/test_base.py

# Run integration tests (requires API keys)
pytest tests/ -m integration
```

## Code Style

We use:
- **ruff** for linting
- **black** for formatting
- **mypy** for type checking

Run all checks:
```bash
ruff check .
black .
mypy sandboxes/
```

## Adding a New Provider

1. Create `sandboxes/providers/your_provider.py`
2. Inherit from `SandboxProvider` base class
3. Implement required methods:
   - `create_sandbox()`
   - `execute_command()`
   - `destroy_sandbox()`
   - `list_sandboxes()`
4. Add tests in `tests/test_integration_your_provider.py`
5. Update CLI in `sandboxes/cli.py`
6. Add to documentation

Example provider structure:
```python
from sandboxes.base import SandboxProvider, Sandbox, SandboxConfig, ExecutionResult

class YourProvider(SandboxProvider):
    @property
    def name(self) -> str:
        return "your_provider"

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        # Implementation
        pass

    async def execute_command(self, sandbox_id: str, command: str, **kwargs) -> ExecutionResult:
        # Implementation
        pass

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        # Implementation
        pass
```

## Commit Messages

Follow conventional commits:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `test:` Test changes
- `perf:` Performance improvements
- `refactor:` Code refactoring
- `chore:` Maintenance tasks

## Pull Request Process

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make your changes
4. Run tests and linting
5. Commit with descriptive message
6. Push to your fork
7. Open a pull request

## Reporting Issues

Please use GitHub Issues to report bugs or request features. Include:
- Clear description
- Steps to reproduce (for bugs)
- Expected behavior
- Actual behavior
- Environment details (Python version, OS, etc.)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.