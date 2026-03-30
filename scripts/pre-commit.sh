#!/bin/bash
# Pre-commit hook to ensure versioning is rigorous
# To install: ln -s ../../scripts/pre-commit.sh .git/hooks/pre-commit

echo "🔐 LLMPROXY RIGOR: Checking Semantic Versioning..."

# We don't auto-bump on every commit, but we ensure VERSION is tracked
if [ -f VERSION ]; then
    VERSION=$(cat VERSION)
    echo "Current Version: v$VERSION"
else
    echo "0.1.0" > VERSION
    git add VERSION
fi

# Example of a stricter hook:
# python3 scripts/bump_version.py 
# git add VERSION
