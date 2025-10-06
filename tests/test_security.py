"""Security tests for path validation and other security features."""

import os
import tempfile
from pathlib import Path

import pytest

from sandboxes.exceptions import SandboxError
from sandboxes.security import (
    validate_download_path,
    validate_local_path,
    validate_upload_path,
)


class TestPathValidation:
    """Test path validation security functions."""

    def test_validate_local_path_simple(self, tmp_path):
        """Test valid simple path."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")

        result = validate_local_path(str(test_file), [str(tmp_path)])
        assert result == test_file.resolve()

    def test_validate_local_path_traversal_dots(self, tmp_path):
        """Test path with .. is rejected."""
        malicious_path = str(tmp_path / ".." / "etc" / "passwd")

        with pytest.raises(SandboxError, match="Path traversal detected"):
            validate_local_path(malicious_path, [str(tmp_path)])

    def test_validate_local_path_traversal_outside(self, tmp_path):
        """Test path outside allowed directory is rejected."""
        # Create a directory outside tmp_path
        with tempfile.TemporaryDirectory() as other_dir:
            other_file = Path(other_dir) / "test.txt"
            other_file.write_text("test")

            with pytest.raises(SandboxError, match="Path outside allowed directories"):
                validate_local_path(str(other_file), [str(tmp_path)])

    def test_validate_local_path_symlink_escape(self, tmp_path):
        """Test symlink that escapes allowed directory."""
        # Create file outside allowed dir
        with tempfile.TemporaryDirectory() as other_dir:
            target = Path(other_dir) / "secret.txt"
            target.write_text("secret")

            # Create symlink inside allowed dir pointing outside
            link = tmp_path / "link.txt"
            link.symlink_to(target)

            # Should be rejected because resolved path is outside
            with pytest.raises(SandboxError, match="Path outside allowed directories"):
                validate_local_path(str(link), [str(tmp_path)])

    def test_validate_local_path_multiple_allowed_dirs(self, tmp_path):
        """Test with multiple allowed directories."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        file1 = dir1 / "test1.txt"
        file2 = dir2 / "test2.txt"
        file1.write_text("test1")
        file2.write_text("test2")

        # Both should be valid
        result1 = validate_local_path(str(file1), [str(dir1), str(dir2)])
        result2 = validate_local_path(str(file2), [str(dir1), str(dir2)])

        assert result1 == file1.resolve()
        assert result2 == file2.resolve()

    def test_validate_local_path_empty_path(self):
        """Test empty path is rejected."""
        with pytest.raises(SandboxError, match="Empty path"):
            validate_local_path("", ["/tmp"])

    def test_validate_local_path_must_exist(self, tmp_path):
        """Test must_exist parameter."""
        nonexistent = tmp_path / "nonexistent.txt"

        # Should fail when must_exist=True
        with pytest.raises(SandboxError, match="Path does not exist"):
            validate_local_path(str(nonexistent), [str(tmp_path)], must_exist=True)

        # Should pass when must_exist=False
        result = validate_local_path(str(nonexistent), [str(tmp_path)], must_exist=False)
        assert result == nonexistent.resolve()

    def test_validate_local_path_default_cwd(self, tmp_path):
        """Test default to current working directory."""
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            test_file = tmp_path / "test.txt"
            test_file.write_text("test")

            # Should use cwd when allowed_base_dirs is None
            result = validate_local_path("test.txt", allowed_base_dirs=None)
            assert result == test_file.resolve()
        finally:
            os.chdir(original_cwd)


class TestUploadPathValidation:
    """Test upload-specific path validation."""

    def test_validate_upload_path_valid_file(self, tmp_path):
        """Test valid file for upload."""
        test_file = tmp_path / "upload.txt"
        test_file.write_text("upload content")

        result = validate_upload_path(str(test_file), [str(tmp_path)])
        assert result == test_file.resolve()

    def test_validate_upload_path_must_exist(self, tmp_path):
        """Test upload path must exist."""
        nonexistent = tmp_path / "nonexistent.txt"

        with pytest.raises(SandboxError, match="Path does not exist"):
            validate_upload_path(str(nonexistent), [str(tmp_path)])

    def test_validate_upload_path_must_be_file(self, tmp_path):
        """Test upload path must be a file, not directory."""
        test_dir = tmp_path / "directory"
        test_dir.mkdir()

        with pytest.raises(SandboxError, match="not a file"):
            validate_upload_path(str(test_dir), [str(tmp_path)])

    def test_validate_upload_path_traversal(self, tmp_path):
        """Test upload path with traversal is rejected."""
        with pytest.raises(SandboxError, match="Path traversal detected"):
            validate_upload_path("../../etc/passwd", [str(tmp_path)])

    def test_validate_upload_path_unreadable(self, tmp_path):
        """Test upload path must be readable."""
        test_file = tmp_path / "unreadable.txt"
        test_file.write_text("content")

        # Make unreadable (on Unix)
        if os.name != "nt":  # Skip on Windows
            test_file.chmod(0o000)
            try:
                with pytest.raises(SandboxError, match="not readable"):
                    validate_upload_path(str(test_file), [str(tmp_path)])
            finally:
                test_file.chmod(0o644)  # Restore permissions for cleanup


class TestDownloadPathValidation:
    """Test download-specific path validation."""

    def test_validate_download_path_valid(self, tmp_path):
        """Test valid download path."""
        download_path = tmp_path / "download.txt"

        result = validate_download_path(str(download_path), [str(tmp_path)])
        assert result == download_path.resolve()

    def test_validate_download_path_can_be_nonexistent(self, tmp_path):
        """Test download path doesn't need to exist."""
        download_path = tmp_path / "newfile.txt"

        # Should succeed even if file doesn't exist
        result = validate_download_path(str(download_path), [str(tmp_path)])
        assert result == download_path.resolve()

    def test_validate_download_path_parent_must_exist(self, tmp_path):
        """Test download path parent directory must exist."""
        nonexistent_parent = tmp_path / "nonexistent" / "download.txt"

        with pytest.raises(SandboxError, match="parent directory does not exist"):
            validate_download_path(str(nonexistent_parent), [str(tmp_path)])

    def test_validate_download_path_parent_must_be_writable(self, tmp_path):
        """Test download path parent must be writable."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        download_path = subdir / "download.txt"

        # Make parent non-writable (on Unix)
        if os.name != "nt":  # Skip on Windows
            subdir.chmod(0o444)
            try:
                with pytest.raises(SandboxError, match="not writable"):
                    validate_download_path(str(download_path), [str(tmp_path)])
            finally:
                subdir.chmod(0o755)  # Restore permissions for cleanup

    def test_validate_download_path_traversal(self, tmp_path):
        """Test download path with traversal is rejected."""
        with pytest.raises(SandboxError, match="Path traversal detected"):
            validate_download_path("../../tmp/evil.txt", [str(tmp_path)])

    def test_validate_download_path_outside_allowed(self, tmp_path):
        """Test download path outside allowed directory is rejected."""
        with tempfile.TemporaryDirectory() as other_dir:
            other_file = Path(other_dir) / "download.txt"

            with pytest.raises(SandboxError, match="Path outside allowed directories"):
                validate_download_path(str(other_file), [str(tmp_path)])


class TestPathValidationEdgeCases:
    """Test edge cases in path validation."""

    def test_absolute_vs_relative_paths(self, tmp_path):
        """Test both absolute and relative paths work."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")

        # Absolute path
        result1 = validate_local_path(str(test_file.absolute()), [str(tmp_path)])
        assert result1 == test_file.resolve()

        # Relative path (when cwd is tmp_path)
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result2 = validate_local_path("test.txt", [str(tmp_path)])
            assert result2 == test_file.resolve()
        finally:
            os.chdir(original_cwd)

    def test_unicode_paths(self, tmp_path):
        """Test paths with unicode characters."""
        unicode_file = tmp_path / "test_文件.txt"
        unicode_file.write_text("unicode content")

        result = validate_local_path(str(unicode_file), [str(tmp_path)])
        assert result == unicode_file.resolve()

    def test_spaces_in_paths(self, tmp_path):
        """Test paths with spaces."""
        spaced_file = tmp_path / "test file with spaces.txt"
        spaced_file.write_text("spaced content")

        result = validate_local_path(str(spaced_file), [str(tmp_path)])
        assert result == spaced_file.resolve()

    def test_very_long_paths(self, tmp_path):
        """Test very long paths."""
        # Create nested directories
        long_path = tmp_path
        for i in range(10):
            long_path = long_path / f"dir{i}"
        long_path.mkdir(parents=True)

        test_file = long_path / "test.txt"
        test_file.write_text("test")

        result = validate_local_path(str(test_file), [str(tmp_path)])
        assert result == test_file.resolve()
