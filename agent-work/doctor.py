#!/usr/bin/env python3
"""Checks that your machine and config are ready for the agent loop.

Run:  python doctor.py
Exit code is 1 if any required check fails.
"""
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
failures = 0
warnings = 0


def ok(msg, hint=""):
    print(f"  [ OK ] {msg}")


def fail(msg, hint=""):
    global failures
    failures += 1
    print(f"  [FAIL] {msg}")
    if hint:
        print(f"         -> {hint}")


def warn(msg, hint=""):
    global warnings
    warnings += 1
    print(f"  [WARN] {msg}")
    if hint:
        print(f"         -> {hint}")


def sh(cmd, cwd=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 1, str(e)


print("\n== Tools ==")
if sys.version_info >= (3, 11):
    ok(f"Python {sys.version.split()[0]}")
else:
    fail(f"Python {sys.version.split()[0]} is too old", "Install Python 3.11 or newer")

if shutil.which("git"):
    ok(sh(["git", "--version"])[1])
else:
    fail("git not found", "Install Git (see SETUP.md step 2)")

if shutil.which("gh"):
    rc, out = sh(["gh", "auth", "status"])
    (ok if rc == 0 else fail)("gh installed and logged in" if rc == 0 else "gh not logged in",
                              "" if rc == 0 else "Run: gh auth login")
else:
    fail("GitHub CLI (gh) not found", "Install it (see SETUP.md step 2)")

if shutil.which("copilot"):
    rc, out = sh(["copilot", "--version"])
    ok(f"Copilot CLI: {out.splitlines()[0] if out else 'installed'}")
else:
    fail("Copilot CLI ('copilot') not found", "npm install -g @github/copilot  (needs Node.js)")

print("\n== Config ==")
cfg = None
try:
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    ok("config.toml parses")
except Exception as e:
    fail(f"config.toml problem: {e}")

if cfg:
    git_cfg = cfg.get("git", {})
    print(f"  push={git_cfg.get('push')}  create_pr={git_cfg.get('create_pr')}"
          "   (keep both false until your first local run looks good)")
    repos_dir = ROOT / cfg["paths"].get("repos", "repos")
    repos = cfg.get("repos", {})
    if not repos:
        fail("No [repos.*] sections in config.toml")
    for name, rc_ in repos.items():
        print(f"\n== Repo: {name} ==")
        path = repos_dir / name
        if not (path / ".git").exists():
            fail(f"{path} is not a git clone", f"git clone <url> {path}")
            continue
        ok(f"clone found at {path}")
        rc, out = sh(["git", "-C", str(path), "remote", "get-url", "origin"])
        (ok if rc == 0 else fail)(f"origin = {out}" if rc == 0 else "no 'origin' remote")
        base = rc_.get("base", "main")
        sh(["git", "-C", str(path), "fetch", "origin"])
        rc, _ = sh(["git", "-C", str(path), "rev-parse", "--verify", f"origin/{base}"])
        (ok if rc == 0 else fail)(f"base branch origin/{base} exists" if rc == 0 else
                                  f"origin/{base} not found",
                                  "" if rc == 0 else f"Fix 'base' in [repos.{name}] of config.toml")
        if (path / ".github" / "copilot-instructions.md").exists():
            ok("has .github/copilot-instructions.md")
        else:
            warn("no .github/copilot-instructions.md",
                 "Copy templates/copilot-instructions.md into the repo and fill it in")
        verifies = rc_.get("verify", [])
        if not verifies:
            warn("no verify commands set; the agent output will not be checked")
        for v in verifies:
            try:
                exe = shlex.split(v, posix=(os.name != "nt"))[0]
            except ValueError:
                exe = v.split()[0]
            if shutil.which(exe):
                ok(f"verify tool '{exe}' found")
            else:
                warn(f"verify tool '{exe}' not on PATH (needed for: {v})")

print("\n== Agents ==")
agents_home = Path.home() / ".copilot" / "agents"
for a in ("task-coder.agent.md", "task-reviewer.agent.md"):
    if (agents_home / a).exists():
        ok(f"{a} installed in {agents_home}")
    else:
        warn(f"{a} not installed", "Run setup.sh / setup.ps1, or copy agents/*.agent.md there")

print("\n== Ticket sources ==")
if cfg:
    src = cfg.get("sources", {})
    if src.get("github", {}).get("enabled"):
        ok("GitHub Issues source enabled (uses gh)") if shutil.which("gh") else fail("GitHub source enabled but gh missing")
    if src.get("jira", {}).get("enabled"):
        if os.environ.get("JIRA_BASE_URL") and (os.environ.get("JIRA_PAT") or (os.environ.get("JIRA_EMAIL") and os.environ.get("JIRA_API_TOKEN"))):
            ok("Jira source enabled and env vars set")
        else:
            fail("Jira source enabled but JIRA_BASE_URL / credentials env vars are missing")
    if not (src.get("github", {}).get("enabled") or src.get("jira", {}).get("enabled")):
        print("  (no ticket source enabled; tasks come from files in tasks/todo/)")

print("\n== Optional: Jira ==")
if os.environ.get("JIRA_BASE_URL") and (os.environ.get("JIRA_API_TOKEN") or os.environ.get("JIRA_PAT")):
    ok("Jira environment variables set")
else:
    print("  (not configured; only needed for jira_import.py)")

print(f"\nResult: {failures} failure(s), {warnings} warning(s)")
if failures:
    print("Fix the FAIL items first, then run doctor.py again.")
sys.exit(1 if failures else 0)
