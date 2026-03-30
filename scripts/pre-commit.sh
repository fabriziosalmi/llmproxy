#!/bin/bash
# Pre-commit hook for llmproxy
# Install: ln -sf ../../scripts/pre-commit.sh .git/hooks/pre-commit

set -e

echo "llmproxy pre-commit: lint + typecheck"

# Lint with ruff (fast — ~200ms)
if command -v ruff &> /dev/null; then
    ruff check core/ proxy/ store/ plugins/ --fix --quiet
else
    echo "Warning: ruff not installed, skipping lint"
fi

# Type check with mypy (core + proxy only, ~2s)
if command -v mypy &> /dev/null; then
    mypy core/ proxy/ --ignore-missing-imports \
        --disable-error-code=misc \
        --disable-error-code=assignment \
        --disable-error-code=no-any-return \
        --no-error-summary --quiet 2>/dev/null || {
        echo "mypy found type errors — fix before committing"
        exit 1
    }
else
    echo "Warning: mypy not installed, skipping typecheck"
fi

echo "pre-commit checks passed"
