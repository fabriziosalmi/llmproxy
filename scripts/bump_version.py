#!/usr/bin/env python3
import sys
import os

def bump():
    version_file = "VERSION"
    if not os.path.exists(version_file):
        with open(version_file, "w") as f: f.write("0.1.0")
    
    with open(version_file, "r") as f:
        v = f.read().strip().split('.')
    
    major, minor, patch = map(int, v)
    
    if "--major" in sys.argv:
        major += 1
        minor = 0
        patch = 0
    elif "--minor" in sys.argv:
        minor += 1
        patch = 0
    else:
        patch += 1
    
    new_version = f"{major}.{minor}.{patch}"
    with open(version_file, "w") as f:
        f.write(new_version)
    
    print(f"LLMPROXY VERSION: {new_version}")

if __name__ == "__main__":
    bump()
