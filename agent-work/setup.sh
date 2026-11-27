#!/usr/bin/env bash
# Bootstrap for macOS / Linux / WSL. Safe to re-run.
set -e
cd "$(dirname "$0")"

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

mkdir -p tasks/todo tasks/doing tasks/done tasks/failed repos worktrees logs
mkdir -p "$HOME/.copilot/agents"
cp -f agents/*.agent.md "$HOME/.copilot/agents/"
echo "Installed custom agents into $HOME/.copilot/agents"

$PY doctor.py || { echo; echo "Setup incomplete: fix the FAIL items above (see SETUP.md), then re-run ./setup.sh"; exit 1; }
echo
echo "Ready. Next: put a task in tasks/todo/ and run:  $PY run.py once"
