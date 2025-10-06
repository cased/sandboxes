# Utility Scripts

This directory contains utility scripts for testing and validating the cased-sandboxes installation.

## Available Scripts

### validate_installation.py

Comprehensive validation of your installation:
- Checks Python version and required packages
- Verifies provider authentication
- Tests basic sandbox operations
- Provides actionable feedback for any issues

```bash
python scripts/validate_installation.py
```

Run this after installation to ensure everything is configured correctly.

### test_providers.py

Comprehensive testing of sandbox providers:
- Tests all standard operations (create, execute, destroy)
- Runs 10+ test commands per provider
- Compares results across providers
- Provides detailed success/failure reports

```bash
# Test all configured providers
python scripts/test_providers.py

# Test a specific provider
python scripts/test_providers.py modal
python scripts/test_providers.py e2b
python scripts/test_providers.py daytona

# Get help
python scripts/test_providers.py --help
```

## When to Use These Scripts

1. **After Installation**: Run `validate_installation.py` to ensure everything is set up correctly
2. **Before Production**: Run `test_providers.py` to verify all operations work
3. **Debugging Issues**: Use these scripts to isolate provider-specific problems
4. **Performance Testing**: The test_providers script shows relative performance

## Requirements

These scripts require the cased-sandboxes package to be installed:

```bash
# Install the package
uv pip install -e .

# Or with all providers
uv pip install -e ".[all]"
```