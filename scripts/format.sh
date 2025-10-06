#!/bin/bash

# Format and lint the codebase
# Usage: ./scripts/format.sh [--fix]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Parse arguments
FIX_MODE=false
if [[ "$1" == "--fix" ]]; then
    FIX_MODE=true
fi

echo -e "${GREEN}Running code quality checks...${NC}"
echo ""

# Function to run command and capture result
run_check() {
    local name=$1
    local cmd=$2
    local fix_cmd=$3

    echo -e "${YELLOW}Running $name...${NC}"

    if $FIX_MODE && [ -n "$fix_cmd" ]; then
        # Run fix command
        if eval "$fix_cmd"; then
            echo -e "${GREEN}✓ $name fixed${NC}"
        else
            echo -e "${RED}✗ $name had errors (some may be unfixable)${NC}"
            return 1
        fi
    else
        # Run check command
        if eval "$cmd"; then
            echo -e "${GREEN}✓ $name passed${NC}"
        else
            echo -e "${RED}✗ $name failed${NC}"
            if [ -n "$fix_cmd" ]; then
                echo -e "  Run with --fix to auto-fix"
            fi
            return 1
        fi
    fi
    echo ""
}

# Track overall status
FAILED=false

# Run Black formatter
run_check "Black (formatter)" \
    "black --check ." \
    "black ." || FAILED=true

# Run Ruff linter
if $FIX_MODE; then
    # Fix with unsafe fixes for type hints
    run_check "Ruff (linter)" \
        "ruff check ." \
        "ruff check . --fix --unsafe-fixes" || FAILED=true
else
    run_check "Ruff (linter)" \
        "ruff check ." \
        "ruff check . --fix" || FAILED=true
fi

# Run MyPy type checker (no auto-fix available)
echo -e "${YELLOW}Running MyPy (type checker)...${NC}"
if mypy . --python-version 3.9; then
    echo -e "${GREEN}✓ MyPy passed${NC}"
else
    echo -e "${YELLOW}⚠ MyPy had warnings (manual fixes needed)${NC}"
fi
echo ""

# Summary
if $FAILED; then
    if $FIX_MODE; then
        echo -e "${YELLOW}Some issues were fixed, but manual intervention may still be needed.${NC}"
        echo -e "Run without --fix to see remaining issues."
    else
        echo -e "${RED}Issues found! Run with --fix to auto-fix formatting and linting issues.${NC}"
    fi
    exit 1
else
    if $FIX_MODE; then
        echo -e "${GREEN}All auto-fixable issues have been resolved!${NC}"
    else
        echo -e "${GREEN}All checks passed!${NC}"
    fi
fi