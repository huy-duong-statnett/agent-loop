#!/usr/bin/env python3
"""OPTIONAL: turn Jira issues into task files in tasks/todo/.

Usage:
    python jira_import.py
    python jira_import.py PROJ-123 [PROJ-124 ...] [--repos repo-name,...]

Environment variables (never put these in files you commit):
    JIRA_BASE_URL   e.g. https://yourcompany.atlassian.net
    JIRA_EMAIL      your Atlassian email          (Jira Cloud)
    JIRA_API_TOKEN  API token                     (Jira Cloud)
    JIRA_PAT        personal access token         (Jira Server/Data Center, instead of the two above)

Which repos does the task touch? In order of priority:
    1. --repos on the command line
    2. agent-ramp-backend / agent-ramp-frontend and repo-<name> Jira labels
    3. Jira components whose name matches a [repos.<name>] in config.toml

Without issue keys, searches the configured JQL for assigned, agent-ready tickets.
Check your company policy before connecting Jira content to automated tooling.
"""
import argparse
import base64
import json
import os
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import sources

ROOT = Path(__file__).resolve().parent
CFG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
TODO = ROOT / CFG["paths"].get("todo", "tasks/todo")
RAMP_REPOS = {
    "agent-ramp-backend": "structure-data-market-participant-registry-service",
    "agent-ramp-frontend": "structure-data-market-participant-registry-frontend",
}


def auth_header():
    pat = os.environ.get("JIRA_PAT")
    if pat:
        return {"Authorization": f"Bearer {pat}"}
    email, token = os.environ.get("JIRA_EMAIL"), os.environ.get("JIRA_API_TOKEN")
    if not (email and token):
        sys.exit("Set JIRA_EMAIL + JIRA_API_TOKEN (Cloud) or JIRA_PAT (Server/DC).")
    raw = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def fetch(key: str) -> dict:
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    if not base:
        sys.exit("Set JIRA_BASE_URL, e.g. https://yourcompany.atlassian.net")
    url = f"{base}/rest/api/2/issue/{key}?fields=summary,description,labels,components"
    req = urllib.request.Request(url, headers={**auth_header(), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Jira returned HTTP {e.code} for {key}: {e.read()[:300]!r}")


def search_keys() -> list[str]:
    source_cfg = CFG.get("sources", {})
    trigger = source_cfg.get("trigger_label", "agent-ready")
    jql = source_cfg.get("jira", {}).get("jql") or (
        f'assignee = currentUser() AND labels = "{trigger}" AND statusCategory != Done ORDER BY created ASC'
    )
    params = {"jql": jql, "fields": "summary", "maxResults": 100}
    keys = []
    token = None
    seen_tokens = set()
    try:
        while True:
            if token:
                params["nextPageToken"] = token
            page = sources._jira("GET", "/rest/api/3/search/jql?" + urllib.parse.urlencode(params))
            keys.extend(issue["key"] for issue in page["issues"])
            token = page.get("nextPageToken")
            if not token:
                if page.get("isLast") is False:
                    raise RuntimeError("Jira search returned an incomplete page without a next page token")
                return keys
            if token in seen_tokens:
                raise RuntimeError("Jira search returned a repeated page token")
            seen_tokens.add(token)
    except urllib.error.HTTPError as e:
        if e.code not in (404, 410) or token:
            raise

    start = 0
    while True:
        params.pop("nextPageToken", None)
        params["startAt"] = start
        page = sources._jira("GET", "/rest/api/2/search?" + urllib.parse.urlencode(params))
        issues = page["issues"]
        keys.extend(issue["key"] for issue in issues)
        start += len(issues)
        if start >= page["total"]:
            return keys
        if not issues:
            raise RuntimeError("Jira search returned an empty page before all results were retrieved")


def pick_repos(fields: dict, cli_repos: list[str] | None) -> list[str]:
    known = set(CFG.get("repos", {}))
    if cli_repos:
        return cli_repos
    labels = fields.get("labels", [])
    repos = [RAMP_REPOS[l] for l in labels if l in RAMP_REPOS]
    missing = [repo for repo in repos if repo not in known]
    if missing:
        raise ValueError(f"mapped repositories not configured: {', '.join(missing)}")
    repos += [l[5:] for l in labels if l.startswith("repo-")]
    repos += [c["name"] for c in fields.get("components", []) if c["name"] in known]
    seen = []
    for r in repos:
        if r in known and r not in seen:
            seen.append(r)
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*", help="issue keys (default: search assigned, ready tickets)")
    ap.add_argument("--repos", help="comma-separated repo names")
    a = ap.parse_args()
    cli_repos = [r.strip() for r in a.repos.split(",")] if a.repos else None

    keys = a.keys or search_keys()
    if not keys:
        print("No matching Jira tickets found.")
    for key in keys:
        issue = fetch(key)
        f = issue["fields"]
        repos = pick_repos(f, cli_repos)
        unknown = [repo for repo in repos if repo not in CFG.get("repos", {})]
        if unknown:
            print(f"{key}: repositories not configured: {', '.join(unknown)}. Skipped.")
            continue
        if not repos:
            print(f"{key}: no repos found. Add agent-ramp-backend or agent-ramp-frontend. Skipped.")
            continue
        out = TODO / f"{key}.md"
        paths = CFG.get("paths", {})
        existing = [ROOT / paths.get(state, f"tasks/{state}") / out.name
                    for state in ("todo", "doing", "done", "failed")]
        if any(path.exists() for path in existing):
            print(f"{key}: task already exists in todo, doing, done or failed; skipped.")
            continue
        desc = (f.get("description") or "").strip() or "(No description in Jira. Add one before running!)"
        out.write_text(
            f"---\nid: {key}\ntitle: {f['summary']}\nrepos: [{', '.join(repos)}]\n---\n\n"
            f"# {f['summary']}\n\n"
            f"## Description (imported from Jira {key})\n{desc}\n\n"
            "## Notes for the agent\n"
            "- Treat the acceptance criteria above as the definition of done.\n"
            "- If the description defines an API contract, match it exactly across repos.\n",
            encoding="utf-8",
        )
        print(f"{key}: wrote {out}  (repos: {', '.join(repos)}). Review and edit it before running.")


if __name__ == "__main__":
    main()
