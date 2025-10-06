#!/bin/bash

# Script to prepare for a release - updates version numbers and runs checks

set -e

if [ -z "$1" ]; then
  echo "Usage: $0 <version>"
  echo "Example: $0 0.1.1"
  echo ""
  echo "This script will:"
  echo "  - Update version in pyproject.toml"
  echo "  - Run tests"
  echo "  - Run format checks"
  echo "  - Update CHANGELOG.md date"
  exit 1
fi

NEW_VERSION=$1
TODAY=$(date +%Y-%m-%d)

echo "Preparing release for version ${NEW_VERSION}"
echo "==========================================="

# Update version in pyproject.toml
echo "Updating pyproject.toml..."
sed -i.bak "s/^version = \".*\"/version = \"${NEW_VERSION}\"/" pyproject.toml
rm -f pyproject.toml.bak

# Update CHANGELOG.md - add date to unreleased version
echo "Updating CHANGELOG.md..."
if grep -q "\[Unreleased\]" CHANGELOG.md; then
    # Check if there are any changes under [Unreleased]
    if grep -A 5 "\[Unreleased\]" CHANGELOG.md | grep -q "###"; then
        echo "Found unreleased changes. Adding new version section..."
        # Add new version section after [Unreleased]
        sed -i.bak "/## \[Unreleased\]/a\\
\\
## [${NEW_VERSION}] - ${TODAY}" CHANGELOG.md
        rm -f CHANGELOG.md.bak
    else
        echo "No unreleased changes found in CHANGELOG.md"
    fi
fi

# Run tests
echo ""
echo "Running tests..."
if command -v pytest &> /dev/null; then
    pytest tests/ -v --tb=short || {
        echo "Tests failed! Please fix before releasing."
        exit 1
    }
else
    echo "Warning: pytest not installed, skipping tests"
fi

# Run format check
echo ""
echo "Running format checks..."
if [ -f "./scripts/format.sh" ]; then
    ./scripts/format.sh || {
        echo "Format checks failed! Run './scripts/format.sh --fix' to fix."
        exit 1
    }
fi

# Show what changed
echo ""
echo "Changes made:"
echo "============="
echo "1. Version updated to ${NEW_VERSION} in pyproject.toml"
echo "2. CHANGELOG.md updated with date ${TODAY}"

# Show git status
echo ""
echo "Git status:"
git status --short

echo ""
echo "✅ Release preparation complete!"
echo ""
echo "Next steps:"
echo "1. Review the changes above"
echo "2. Commit: git add -A && git commit -m 'Prepare release ${NEW_VERSION}'"
echo "3. Push: git push origin main"
echo "4. Run release: ./scripts/release.sh ${NEW_VERSION}"
echo ""
echo "Make sure you have set:"
echo "  export TWINE_USERNAME=__token__"
echo "  export TWINE_PASSWORD='your-pypi-api-token'"