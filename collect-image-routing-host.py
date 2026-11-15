#!/usr/bin/env python3
"""Export bounded installed-runtime source and standards, never credentials/config.

Run on Chiron as the Hermes owner, without sudo. The only write is --output.
No plugin is imported/executed, no network is used, and no service is restarted.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 3_000_000
SOURCE_PATTERNS = (
    "agent/plugin_llm*.py",
    "hermes_cli/plugins.py", "hermes_cli/plugins/**/*.py",
    "hermes_cli/plugin*.py", "hermes_cli/config*.py",
    "hermes_cli/runtime*.py", "hermes_cli/*llm*.py",
    "hermes_cli/*client*.py", "hermes_cli/*auxiliary*.py",
    "hermes_cli/*provider*.py", "hermes_cli/*credential*.py",
    "hermes_tools/*llm*.py", "hermes_tools/*client*.py",
    "tools/vision*.py", "tools/*auxiliary*.py", "tools/*llm*.py",
    "hermes_constants.py", "pyproject.toml",
)
STANDARD_FILES = ("AGENTS.md", "README.md", "CLAUDE.md", "pyproject.toml")


def collect(install, standards):
    install, standards = Path(install).resolve(), Path(standards).resolve()
    result = {
        "install": str(install), "install_exists": install.is_dir(),
        "sources": {}, "standards_root": str(standards), "standards": {},
        "warnings": [],
        "excluded": "No user configuration, environment, auth store or runtime secrets are read.",
    }
    budget = MAX_TOTAL_BYTES

    def add(path, root, target):
        nonlocal budget
        relative = str(path.relative_to(root))
        # Never follow symlinks, including directory symlinks, outside the root.
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            result["warnings"].append(f"Skipped symlink: {relative}")
            return
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES or size > budget:
                result["warnings"].append(f"Skipped by size limit: {relative}")
                return
            data = path.read_bytes()
            if len(data) > MAX_FILE_BYTES or len(data) > budget:
                result["warnings"].append(f"Skipped by size limit: {relative}")
                return
            target[relative] = data.decode("utf-8")
            budget -= len(data)
        except (OSError, UnicodeError) as exc:
            result["warnings"].append(f"Cannot read {relative}: {type(exc).__name__}")

    if not install.is_dir():
        result["warnings"].append("Hermes installation directory is unavailable; use --install.")
    candidates = set()
    for pattern in SOURCE_PATTERNS:
        candidates.update(install.glob(pattern))
    for path in sorted(candidates):
        if path.is_file():
            add(path, install, result["sources"])
    for name in STANDARD_FILES:
        path = standards / name
        if path.is_file():
            add(path, standards, result["standards"])
    if not result["standards"]:
        result["warnings"].append("Default Project standards unavailable; use --standards.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", type=Path, default=Path.home() / ".hermes/hermes-agent")
    parser.add_argument("--standards", type=Path, default=Path("/media/athena/code/Codex/default-project"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new filename rather than overwriting.")
    report = collect(args.install, args.standards)
    hermes = shutil.which("hermes")
    report["hermes_executable"] = hermes
    if hermes:
        try:
            proc = subprocess.run([hermes, "--version"], text=True, capture_output=True, timeout=20)
            report["hermes_version"] = proc.stdout.strip()[:4000]
            report["version_exit_code"] = proc.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            report["warnings"].append(f"Version check failed: {type(exc).__name__}")
    # Exclusive creation protects any pre-existing file; never write into a source tree.
    output = args.output.absolute()
    for source_root in (args.install.resolve(), args.standards.resolve()):
        if output.resolve().is_relative_to(source_root):
            parser.error("Output must be outside the installation and standards directories.")
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(f"Saved {len(report['sources'])} runtime source files and {len(report['standards'])} standards files to {output}")
    if not report["install_exists"] or not report["sources"]:
        print("Runtime not collected; check --install.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
