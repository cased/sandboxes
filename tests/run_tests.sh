#!/bin/bash

# Test runner for sandboxes library
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "🧪 Sandboxes Test Runner"
echo "========================"

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo -e "${RED}Error: uv is not installed${NC}"
    echo "Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Install dependencies
echo -e "\n${YELLOW}Installing dependencies...${NC}"
uv pip install -e ".[dev]"

# Check for provider API keys
echo -e "\n${YELLOW}Checking API keys...${NC}"
if [ -n "$E2B_API_KEY" ]; then
    echo -e "${GREEN}✓ E2B_API_KEY found${NC}"
    E2B_AVAILABLE=1
else
    echo -e "${YELLOW}⚠ E2B_API_KEY not set - E2B tests will be skipped${NC}"
    E2B_AVAILABLE=0
fi

if [ -n "$DAYTONA_API_KEY" ]; then
    echo -e "${GREEN}✓ DAYTONA_API_KEY found${NC}"
    DAYTONA_AVAILABLE=1
else
    echo -e "${YELLOW}⚠ DAYTONA_API_KEY not set - Daytona tests will be skipped${NC}"
    DAYTONA_AVAILABLE=0
fi

if [ -n "$MODAL_TOKEN" ]; then
    echo -e "${GREEN}✓ MODAL_TOKEN found${NC}"
    MODAL_AVAILABLE=1
else
    echo -e "${YELLOW}⚠ MODAL_TOKEN not set - Modal tests will be skipped${NC}"
    MODAL_AVAILABLE=0
fi

# Parse command line arguments
TEST_TYPE=${1:-all}
VERBOSE=${2:-}

case $TEST_TYPE in
    unit)
        echo -e "\n${YELLOW}Running unit tests only...${NC}"
        PYTEST_ARGS="-m unit"
        ;;
    integration)
        echo -e "\n${YELLOW}Running integration tests only...${NC}"
        PYTEST_ARGS="-m integration"
        ;;
    e2b)
        echo -e "\n${YELLOW}Running E2B tests only...${NC}"
        PYTEST_ARGS="-m e2b"
        ;;
    daytona)
        echo -e "\n${YELLOW}Running Daytona tests only...${NC}"
        PYTEST_ARGS="-m daytona"
        ;;
    quick)
        echo -e "\n${YELLOW}Running quick tests (no slow tests)...${NC}"
        PYTEST_ARGS="-m 'not slow'"
        ;;
    all)
        echo -e "\n${YELLOW}Running all tests...${NC}"
        PYTEST_ARGS=""
        ;;
    *)
        echo -e "${RED}Unknown test type: $TEST_TYPE${NC}"
        echo "Usage: $0 [unit|integration|e2b|daytona|quick|all] [-v]"
        exit 1
        ;;
esac

# Add verbose flag if requested
if [ "$VERBOSE" = "-v" ]; then
    PYTEST_ARGS="$PYTEST_ARGS -v"
fi

# Run tests
echo -e "\n${GREEN}Running pytest with args: $PYTEST_ARGS${NC}\n"
uv run pytest tests/ $PYTEST_ARGS

# Check exit code
if [ $? -eq 0 ]; then
    echo -e "\n${GREEN}✅ All tests passed!${NC}"
else
    echo -e "\n${RED}❌ Some tests failed${NC}"
    exit 1
fi