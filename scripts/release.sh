#!/bin/bash

# Script to build, publish, and tag a release for the sandboxes project locally.

# Exit immediately if a command exits with a non-zero status.
set -e

# Check if a version argument is provided.
if [ -z "$1" ]; then
  echo "Error: No version specified."
  echo "Usage: $0 <version>"
  echo "Example: $0 0.1.0"
  exit 1
fi

VERSION=$1
TAG_NAME="v${VERSION}"
PYPROJECT_TOML="pyproject.toml"

# --- Pre-flight checks ---
# 1. Check if pyproject.toml exists
if [ ! -f "${PYPROJECT_TOML}" ]; then
    echo "Error: ${PYPROJECT_TOML} not found in the current directory."
    exit 1
fi

# 2. Check if version in pyproject.toml matches the provided version
PYPROJECT_VERSION=$(sed -n 's/^version[[:space:]]*=[[:space:]]*\"\([^"]*\)\".*/\1/p' "${PYPROJECT_TOML}")

if [ "${PYPROJECT_VERSION}" != "${VERSION}" ]; then
    echo "Error: Version mismatch!"
    echo "  Provided version: ${VERSION}"
    echo "  Version in ${PYPROJECT_TOML}: ${PYPROJECT_VERSION}"
    echo "Please update ${PYPROJECT_TOML} to version = \"${VERSION}\" before releasing."
    exit 1
fi

# 3. Check for uncommitted changes
if ! git diff-index --quiet HEAD --; then
    echo "Error: You have uncommitted changes."
    echo "Please commit or stash all changes before tagging a release."
    exit 1
fi

# 4. Check if working branch is main/master (optional, adjust as needed)
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "${CURRENT_BRANCH}" != "main" && "${CURRENT_BRANCH}" != "master" ]]; then
    echo "Warning: You are not on the main/master branch (current: ${CURRENT_BRANCH})."
    read -p "Continue anyway? (y/N): " confirm_branch
    if [[ "$confirm_branch" != [yY] ]]; then
        echo "Aborted by user."
        exit 1
    fi
fi

# 5. Check for required environment variables for PyPI upload
if [ -z "${TWINE_USERNAME}" ] || [ -z "${TWINE_PASSWORD}" ]; then
  echo "Error: TWINE_USERNAME and/or TWINE_PASSWORD environment variables are not set."
  echo "Please set them before running this script:"
  echo "  export TWINE_USERNAME=__token__"
  echo "  export TWINE_PASSWORD='your-pypi-api-token'"
  exit 1
fi

if [ "${TWINE_USERNAME}" != "__token__" ]; then
    echo "Warning: TWINE_USERNAME is not set to '__token__'. This is the required username for token-based authentication with PyPI."
    read -p "Continue anyway? (y/N): " confirm_twine_user
    if [[ "$confirm_twine_user" != [yY] ]]; then
        echo "Aborted by user."
        exit 1
    fi
fi

# 6. Check if build and twine are installed
echo "Checking for required build tools..."
if ! command -v python &> /dev/null; then
    echo "Error: Python command not found."
    echo "Please ensure Python is installed and in your PATH."
    exit 1
fi

# Check build package
if ! pip show build &> /dev/null; then
    echo "Error: 'build' package is not installed."
    echo "Please install it with: pip install build"
    exit 1
fi

# Check twine package
if ! pip show twine &> /dev/null; then
    echo "Error: 'twine' package is not installed."
    echo "Please install it with: pip install twine"
    exit 1
fi

echo "All required packages are installed."

# 7. Run tests before release
echo ""
echo "Running tests to ensure everything works..."
if command -v pytest &> /dev/null; then
    echo "Running pytest (excluding integration tests)..."
    if ! pytest tests/ -v --tb=short -m "not integration"; then
        echo "Error: Tests failed. Please fix failing tests before releasing."
        exit 1
    fi
else
    echo "Warning: pytest not installed. Skipping tests."
    read -p "Continue without running tests? (y/N): " confirm_no_tests
    if [[ "$confirm_no_tests" != [yY] ]]; then
        echo "Aborted by user."
        exit 1
    fi
fi

# 8. Check code quality
echo ""
echo "Running code quality checks..."
if [ -f "./scripts/format.sh" ]; then
    echo "Running format.sh..."
    if ! ./scripts/format.sh; then
        echo "Error: Code quality checks failed."
        echo "Please run './scripts/format.sh --fix' to fix issues, then commit changes."
        exit 1
    fi
else
    echo "Warning: format.sh not found. Skipping code quality checks."
fi

echo ""
echo "Release pre-flight checks passed for version ${VERSION}."
read -p "Proceed with build, PyPI publish, and git tagging? (y/N): " confirm_proceed
if [[ "$confirm_proceed" != [yY] ]]; then
    echo "Aborted by user."
    exit 1
fi

# --- Build Package ---
echo ""
echo "Building package..."
# Clean previous builds
rm -rf dist/
rm -rf build/
rm -rf *.egg-info/

# Check if we're in a virtual environment and temporarily deactivate it for build/twine
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "Temporarily deactivating virtual environment for build processes..."
    # Save the current VIRTUAL_ENV path
    SAVED_VIRTUAL_ENV="$VIRTUAL_ENV"
    # Deactivate the virtual environment
    PATH=$(echo "$PATH" | sed -e "s|$VIRTUAL_ENV/bin:||g")
    unset VIRTUAL_ENV
    # Set PYTHONPATH to ensure the package can still be found
    export PYTHONPATH="$(pwd)"
fi

# Run build with system Python
python -m build

# --- Verify Build ---
echo ""
echo "Verifying build artifacts..."
if [ ! -d "dist" ]; then
    echo "Error: dist/ directory not created. Build failed."
    exit 1
fi

WHEEL_FILE=$(ls dist/*.whl 2>/dev/null | head -n 1)
TAR_FILE=$(ls dist/*.tar.gz 2>/dev/null | head -n 1)

if [ -z "$WHEEL_FILE" ] || [ -z "$TAR_FILE" ]; then
    echo "Error: Expected build artifacts not found in dist/"
    echo "  Wheel: ${WHEEL_FILE:-NOT FOUND}"
    echo "  Tar.gz: ${TAR_FILE:-NOT FOUND}"
    exit 1
fi

echo "Build artifacts created:"
echo "  Wheel: $(basename $WHEEL_FILE)"
echo "  Tar.gz: $(basename $TAR_FILE)"

# --- Publish to PyPI ---
echo ""
echo "Publishing package to PyPI..."
echo "Uploading: cased-sandboxes version ${VERSION}"

# Use twine check first to verify the packages
python -m twine check dist/*

# Upload to PyPI
python -m twine upload dist/*

# Restore virtual environment if it was active
if [[ -n "$SAVED_VIRTUAL_ENV" ]]; then
    echo "Restoring virtual environment..."
    export VIRTUAL_ENV="$SAVED_VIRTUAL_ENV"
    export PATH="$VIRTUAL_ENV/bin:$PATH"
fi

# --- Update Changelog ---
echo ""
if [ -f "CHANGELOG.md" ]; then
    echo "Remember to update CHANGELOG.md with release notes for version ${VERSION}!"
    read -p "Have you updated the CHANGELOG.md? (y/N): " confirm_changelog
    if [[ "$confirm_changelog" != [yY] ]]; then
        echo "Please update CHANGELOG.md before pushing the tag."
        read -p "Continue anyway? (y/N): " confirm_no_changelog
        if [[ "$confirm_no_changelog" != [yY] ]]; then
            echo "Aborted by user. Tag not created."
            echo "The package has been published to PyPI, but no git tag was created."
            exit 1
        fi
    fi
fi

# --- Tagging and Pushing Git Tag ---
echo ""
echo "Creating git tag '${TAG_NAME}'..."
git tag -a "${TAG_NAME}" -m "Release version ${VERSION}"

echo "Pushing git tag '${TAG_NAME}' to origin..."
git push origin "${TAG_NAME}"

# --- (Optional) Create GitHub Release ---
if command -v gh &> /dev/null; then
    echo ""
    echo "GitHub CLI ('gh') found."
    read -p "Do you want to create a GitHub Release for tag ${TAG_NAME}? (y/N): " confirm_gh_release
    if [[ "$confirm_gh_release" == [yY] ]]; then
        echo "Creating GitHub Release for ${TAG_NAME}..."

        # Generate release notes
        RELEASE_NOTES="## 🚀 Release ${VERSION}

### ✨ Features
- Universal sandbox abstraction across E2B, Modal, Daytona, and Cloudflare
- Automatic provider failover for high availability
- Label-based sandbox reuse
- Connection pooling and circuit breaker patterns

### 📦 Installation
\`\`\`bash
pip install cased-sandboxes==${VERSION}
\`\`\`

### 🔧 Quick Start
\`\`\`python
from sandboxes import run

# Simple execution with auto-detection
result = await run('echo Hello World')
print(result.stdout)
\`\`\`

See [README](https://github.com/cased/sandboxes) for full documentation."

        # Create the release with generated notes
        if echo "${RELEASE_NOTES}" | gh release create "${TAG_NAME}" \
            --title "Release ${VERSION}" \
            --notes-file - \
            dist/*.whl dist/*.tar.gz; then
            echo "Successfully created GitHub Release for ${TAG_NAME} with artifacts."
        else
            echo "Warning: Failed to create GitHub Release. Exit code: $?"
            echo "Please check 'gh' CLI output/authentication or create the release manually on GitHub."
        fi
    else
        echo "Skipping GitHub Release creation."
    fi
else
    echo ""
    echo "GitHub CLI ('gh') not found. Skipping GitHub Release creation."
    echo "To enable automatic GitHub Release creation, install the GitHub CLI: https://cli.github.com/"
fi

# --- Success Summary ---
echo ""
echo "========================================="
echo "🎉 Release ${VERSION} completed successfully!"
echo "========================================="
echo ""
echo "✅ Package built and uploaded to PyPI"
echo "✅ Git tag ${TAG_NAME} created and pushed"
if [[ "$confirm_gh_release" == [yY] ]]; then
    echo "✅ GitHub Release created"
fi
echo ""
echo "Next steps:"
echo "1. Verify the package on PyPI: https://pypi.org/project/cased-sandboxes/${VERSION}/"
echo "2. Test installation: pip install cased-sandboxes==${VERSION}"
echo "3. Update documentation if needed"
echo "4. Announce the release to the team"
echo ""
echo "Package can be installed with:"
echo "  pip install cased-sandboxes==${VERSION}"
echo ""

exit 0