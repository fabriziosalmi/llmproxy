#!/usr/bin/env python3
"""
Supply Chain Integrity Verifier

Defends the install against known supply-chain attack patterns.

Inspired by the litellm 1.82.8 incident (2026-03-24) where a compromised
PyPI release stole credentials and spread via a malicious .pth file.

Defense layers:
  1. .pth file content scan (malicious auto-exec on interpreter startup)
  2. Blocked-package allow-list (typosquats + known-compromised names)

Deliberately NOT checking pinned versions — requirements.txt already pins
them and the pip-audit job in CI catches published CVEs. Maintaining a
second source-of-truth for expected versions just created a hard coupling
with Dependabot (every bump required a matching edit here) without adding
any real security signal: attackers can publish tampered wheels at any
version, and version numbers aren't a tamper indicator.

Usage:
  python scripts/verify_deps.py          # scan + warn
  python scripts/verify_deps.py --strict # exit 1 on any finding
"""

import sys
import importlib.metadata
import pathlib
import logging

logger = logging.getLogger("llmproxy.verify_deps")

# Packages that MUST NOT be installed (known-compromised or dangerous in proxy context)
BLOCKED_PACKAGES = {
    "litellm",      # Supply chain attack 2026-03-24
    "openai-proxy",  # Typosquat risk
    "llm-proxy",     # Typosquat risk
}


def check_blocked_packages() -> list[str]:
    """Detect known-compromised or dangerous packages."""
    issues = []
    for pkg in BLOCKED_PACKAGES:
        try:
            version = importlib.metadata.version(pkg)
            issues.append(
                f"BLOCKED PACKAGE INSTALLED: {pkg}=={version} — "
                f"this package is blocked for security reasons"
            )
        except importlib.metadata.PackageNotFoundError:
            pass  # Good — not installed
    return issues


def scan_pth_files() -> list[str]:
    """
    Scan for suspicious .pth files in site-packages.

    .pth files execute Python code on every interpreter startup.
    The litellm attack used litellm_init.pth to spawn a credential
    stealer on every Python process.

    Legitimate .pth files: setuptools, pip, editable installs.
    Suspicious: anything with import statements or exec() calls.
    """
    issues = []
    legitimate_prefixes = {
        "distutils-precedence",  # setuptools
        "easy-install",          # setuptools legacy
        "_virtualenv",           # virtualenv
        "__editable__",          # PEP 660 editable installs
        "a1_coverage",           # coverage.py subprocess measurement
        "coverage",              # coverage.py
    }

    for site_dir in sys.path:
        site_path = pathlib.Path(site_dir)
        if not site_path.is_dir():
            continue

        for pth_file in site_path.glob("*.pth"):
            name = pth_file.stem

            # Skip known-legitimate .pth files
            if any(name.startswith(prefix) for prefix in legitimate_prefixes):
                continue

            # Read and check contents
            try:
                content = pth_file.read_text(encoding="utf-8", errors="ignore")
                suspicious = False
                reasons = []

                # Pattern match against known malware signatures
                # Based on litellm 1.82.8 payload analysis (2026-03-24):
                #   - .pth spawns child via subprocess.Popen
                #   - Harvests SSH keys, .env, cloud creds, crypto wallets
                #   - Exfiltrates via RSA-encrypted POST to lookalike domain
                #   - Creates persistence via systemd user service
                EXEC_PATTERNS = [
                    "exec(", "eval(", "compile(", "subprocess",
                    "__import__", "importlib",
                ]
                NETWORK_PATTERNS = [
                    "urllib", "requests", "http.client", "socket",
                    "urlopen", ".post(", ".get(",
                ]
                PERSISTENCE_PATTERNS = [
                    "systemd", "crontab", "sysmon",
                    "/root/", "/.config/", "launchd",
                ]
                EXFIL_PATTERNS = [
                    "base64", ".ssh/", ".env", ".aws/",
                    "credentials", "wallet", ".kube/config",
                    "IMDS", "metadata.google", "169.254.169.254",
                ]
                SPAWN_PATTERNS = [
                    "Popen", "os.system", "os.popen",
                    "fork(", "spawn",
                ]

                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # Any import statement in a .pth is suspicious
                    if line.startswith("import ") or line.startswith("import\t"):
                        suspicious = True
                        reasons.append(f"import statement: {line[:80]}")
                    # Code execution
                    for pat in EXEC_PATTERNS:
                        if pat in line:
                            suspicious = True
                            reasons.append(f"code execution ({pat}): {line[:80]}")
                            break
                    # Network access (exfiltration vector)
                    for pat in NETWORK_PATTERNS:
                        if pat in line:
                            suspicious = True
                            reasons.append(f"network access ({pat}): {line[:80]}")
                            break
                    # Persistence mechanisms
                    for pat in PERSISTENCE_PATTERNS:
                        if pat in line:
                            suspicious = True
                            reasons.append(f"persistence ({pat}): {line[:80]}")
                            break
                    # Credential/data exfiltration targets
                    for pat in EXFIL_PATTERNS:
                        if pat in line:
                            suspicious = True
                            reasons.append(f"exfil target ({pat}): {line[:80]}")
                            break
                    # Process spawning
                    for pat in SPAWN_PATTERNS:
                        if pat in line:
                            suspicious = True
                            reasons.append(f"process spawn ({pat}): {line[:80]}")
                            break

                if suspicious:
                    issues.append(
                        f"SUSPICIOUS .pth FILE: {pth_file} — {'; '.join(reasons)}"
                    )
            except Exception as e:
                issues.append(f"UNREADABLE .pth FILE: {pth_file} — {e}")

    return issues


def verify_all(strict: bool = False) -> bool:
    """Run all supply chain checks. Returns True if clean."""
    all_issues = []

    logger.info("Supply chain integrity check starting...")

    # 1. Blocked packages
    blocked_issues = check_blocked_packages()
    all_issues.extend(blocked_issues)

    # 2. .pth file scan
    pth_issues = scan_pth_files()
    all_issues.extend(pth_issues)

    if all_issues:
        for issue in all_issues:
            logger.warning(f"[SUPPLY CHAIN] {issue}")

        # Blocked packages and suspicious .pth files are always fatal
        critical = [i for i in all_issues if "BLOCKED" in i or "SUSPICIOUS .pth" in i]
        if critical:
            logger.error(
                f"CRITICAL: {len(critical)} supply chain threat(s) detected. "
                "Aborting startup."
            )
            return False

        if strict:
            logger.error(
                f"STRICT MODE: {len(all_issues)} issue(s) found. Aborting."
            )
            return False

        logger.warning(
            f"Supply chain check: {len(all_issues)} warning(s), "
            "0 critical. Proceeding."
        )
        return True
    else:
        logger.info("Supply chain integrity check: ALL CLEAN")
        return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    strict = "--strict" in sys.argv
    clean = verify_all(strict=strict)
    sys.exit(0 if clean else 1)
