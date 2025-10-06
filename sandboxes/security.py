"""Security utilities for sandboxes library."""

import os
from pathlib import Path

from .exceptions import SandboxError


def validate_local_path(
    path: str,
    allowed_base_dirs: list[str] | None = None,
    must_exist: bool = False,
    operation: str = "access",
) -> Path:
    """
    Validate that a local file path is safe to access.

    Prevents path traversal attacks by ensuring the path:
    1. Does not contain .. components
    2. Resolves to an absolute path within allowed directories
    3. Does not escape allowed base directories

    Args:
        path: The file path to validate
        allowed_base_dirs: List of allowed base directories. If None, any path is allowed.
        must_exist: Whether the path must already exist
        operation: Description of the operation (for error messages)

    Returns:
        Validated absolute Path object

    Raises:
        SandboxError: If path is invalid or outside allowed directories

    Examples:
        >>> validate_local_path("/tmp/safe.txt", ["/tmp"])
        PosixPath('/tmp/safe.txt')

        >>> validate_local_path("../../etc/passwd", ["/tmp"])
        SandboxError: Path traversal detected
    """
    if not path:
        raise SandboxError(f"Empty path provided for {operation}")

    # Convert to Path object
    path_obj = Path(path)

    # Check for .. components before resolving
    if ".." in path_obj.parts:
        raise SandboxError(
            f"Path traversal detected in {operation}: '{path}' contains '..' component"
        )

    # Resolve to absolute path (follows symlinks)
    try:
        abs_path = path_obj.resolve()
    except (OSError, RuntimeError) as e:
        raise SandboxError(f"Invalid path for {operation}: {e}") from e

    # If path must exist, check it
    if must_exist and not abs_path.exists():
        raise SandboxError(f"Path does not exist for {operation}: '{path}'")

    # If no allowed directories specified, skip directory validation
    if allowed_base_dirs is None:
        return abs_path

    # Convert allowed base dirs to resolved absolute paths
    allowed_bases = []
    for base_dir in allowed_base_dirs:
        try:
            allowed_bases.append(Path(base_dir).resolve())
        except (OSError, RuntimeError) as e:
            raise SandboxError(f"Invalid base directory '{base_dir}': {e}") from e

    # Check if path is within at least one allowed base directory
    is_within_allowed = False
    for base in allowed_bases:
        try:
            # This will raise ValueError if abs_path is not relative to base
            abs_path.relative_to(base)
            is_within_allowed = True
            break
        except ValueError:
            continue

    if not is_within_allowed:
        allowed_str = ", ".join(str(b) for b in allowed_bases)
        raise SandboxError(
            f"Path outside allowed directories for {operation}: "
            f"'{abs_path}' is not within [{allowed_str}]"
        )

    return abs_path


def validate_upload_path(local_path: str, allowed_dirs: list[str] | None = None) -> Path:
    """
    Validate a local path for file upload (must exist and be readable).

    Args:
        local_path: Path to file on host to upload
        allowed_dirs: Allowed base directories for uploads

    Returns:
        Validated absolute Path object

    Raises:
        SandboxError: If path is invalid or inaccessible
    """
    validated = validate_local_path(
        local_path, allowed_base_dirs=allowed_dirs, must_exist=True, operation="upload"
    )

    # Additional check: must be a file
    if not validated.is_file():
        raise SandboxError(f"Upload path is not a file: '{local_path}'")

    # Check if readable
    if not os.access(validated, os.R_OK):
        raise SandboxError(f"Upload path is not readable: '{local_path}'")

    return validated


def validate_download_path(local_path: str, allowed_dirs: list[str] | None = None) -> Path:
    """
    Validate a local path for file download (parent dir must exist and be writable).

    Args:
        local_path: Path to file on host where download will be saved
        allowed_dirs: Allowed base directories for downloads

    Returns:
        Validated absolute Path object

    Raises:
        SandboxError: If path is invalid or parent directory is not writable
    """
    validated = validate_local_path(
        local_path, allowed_base_dirs=allowed_dirs, must_exist=False, operation="download"
    )

    # Parent directory must exist
    parent = validated.parent
    if not parent.exists():
        raise SandboxError(f"Download parent directory does not exist: '{parent}'")

    # Parent must be a directory
    if not parent.is_dir():
        raise SandboxError(f"Download parent is not a directory: '{parent}'")

    # Parent must be writable
    if not os.access(parent, os.W_OK):
        raise SandboxError(f"Download parent directory is not writable: '{parent}'")

    return validated
