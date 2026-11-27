#!/usr/bin/env python3
"""Ticket sources: pull tickets ASSIGNED TO YOU into tasks/todo, and write results back.

Safety design:
  * Only tickets assigned to you AND carrying the trigger label (default `agent-ready`)
    are picked up. Assigning a ticket alone does nothing; you opt in per ticket.
  * Thin tickets (too short, or no repo mapping) are NOT run; the ticket gets a comment
    and an `agent-needs-info` label instead.
  * On pickup the trigger label is swapped for `agent-in-progress`, so a ticket is
    never picked up twice. State is also kept in state/sources.json.
  * On finish, a comment with PR links (or the failure reason) is posted, and the label
    becomes `agent-pr-ready` or `agent-failed`.

Jira env vars:  JIRA_BASE_URL, and either JIRA_EMAIL + JIRA_API_TOKEN (Cloud) or JIRA_PAT (Server/DC)
GitHub:         uses the `gh` CLI you already logged in with.
"""
import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "sources.json"


# ------------------------------------------------------------------ shared
def _s(cfg):
    return cfg.get("sources", {})


def _lbl(cfg, key, default):
    return _s(cfg).get(key, default)


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"imported": []}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def enabled(cfg) -> bool:
    s = _s(cfg)
    return bool(s.get("jira", {}).get("enabled") or s.get("github", {}).get("enabled"))


def write_task(todo_dir: Path, task_id, title, repos, source, body):
    title = " ".join(title.split())
    path = todo_dir / f"{task_id}.md"
    path.write_text(
        f"---\nid: {task_id}\ntitle: {title}\nrepos: [{', '.join(repos)}]\nsource: {source}\n---\n\n"
        f"# {title}\n\n## Ticket description\n{body.strip()}\n\n"
        "## Notes for the agent\n"
        "- The ticket description above is the requirement; treat its acceptance criteria as the definition of done.\n"
        "- If it defines an API contract, match it exactly across repos.\n"
        "- Keep the change minimal and add tests.\n",
        encoding="utf-8",
    )
    return path


def known_repos(cfg):
    return set(cfg.get("repos", {}))


def _repos_from_labels(cfg, labels, components=()):
    known = known_repos(cfg)
    found = [l[5:] for l in labels if l.startswith("repo-")] + list(components)
    out = []
    for r in found:
        if r in known and r not in out:
            out.append(r)
    return out


# ------------------------------------------------------------------ Jira
def _jira_headers():
    pat = os.environ.get("JIRA_PAT")
    if pat:
        auth = f"Bearer {pat}"
    else:
        email, tok = os.environ.get("JIRA_EMAIL"), os.environ.get("JIRA_API_TOKEN")
        if not (email and tok):
            raise RuntimeError("Set JIRA_EMAIL + JIRA_API_TOKEN (Cloud) or JIRA_PAT (Server/DC)")
        auth = "Basic " + base64.b64encode(f"{email}:{tok}".encode()).decode()
    return {"Authorization": auth, "Accept": "application/json", "Content-Type": "application/json"}


def _jira(method, path, body=None):
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("Set JIRA_BASE_URL, e.g. https://yourcompany.atlassian.net")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers=_jira_headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def jira_search_keys(jql):
    q = urllib.parse.quote(jql)
    # Newer Jira Cloud endpoint first, then the classic one (Server/DC, older Cloud).
    for path in (f"/rest/api/3/search/jql?jql={q}&fields=summary&maxResults=20",
                 f"/rest/api/2/search?jql={q}&fields=summary&maxResults=20"):
        try:
            return [i["key"] for i in _jira("GET", path).get("issues", [])]
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                continue
            raise
    raise RuntimeError("Jira search endpoint not found; check JIRA_BASE_URL and your Jira version")


def jira_labels(key, remove=None, add=None):
    ops = []
    if remove:
        ops.append({"remove": remove})
    if add:
        ops.append({"add": add})
    try:
        _jira("PUT", f"/rest/api/2/issue/{key}", {"update": {"labels": ops}})
    except Exception as e:
        print(f"  (could not update labels on {key}: {e})")


def jira_comment(key, text):
    try:
        _jira("POST", f"/rest/api/2/issue/{key}/comment", {"body": text})
    except Exception as e:
        print(f"  (could not comment on {key}: {e})")


def jira_pull(cfg, todo_dir, state, log):
    s = _s(cfg)
    trig, prog, need = _lbl(cfg, "trigger_label", "agent-ready"), \
        _lbl(cfg, "progress_label", "agent-in-progress"), _lbl(cfg, "needs_info_label", "agent-needs-info")
    min_chars = s.get("min_description_chars", 150)
    jql = s["jira"].get("jql") or (
        f'assignee = currentUser() AND labels = "{trig}" AND statusCategory != Done ORDER BY created ASC')
    n = 0
    for key in jira_search_keys(jql):
        if key in state["imported"]:
            continue
        f = _jira("GET", f"/rest/api/2/issue/{key}?fields=summary,description,labels,components")["fields"]
        repos = _repos_from_labels(cfg, f.get("labels", []), [c["name"] for c in f.get("components", [])])
        desc = (f.get("description") or "").strip()
        problems = []
        if not repos:
            problems.append("which repo(s) it touches (add a label like `repo-backend`, or set a Jira component)")
        if len(desc) < min_chars:
            problems.append(f"a fuller description (at least {min_chars} characters, with acceptance criteria)")
        if problems:
            log(f"{key}: needs info, skipping")
            jira_comment(key, "Agent loop did not start this ticket. It still needs: " + "; ".join(problems) +
                         f". Re-add the `{trig}` label when ready.")
            jira_labels(key, remove=trig, add=need)
            continue
        write_task(todo_dir, key, f["summary"], repos, f"jira:{key}", desc)
        jira_labels(key, remove=trig, add=prog)
        jira_comment(key, f"Agent loop picked this up (repos: {', '.join(repos)}). I'll comment again with the result.")
        state["imported"].append(key)
        log(f"{key}: imported -> tasks/todo/{key}.md (repos: {', '.join(repos)})")
        n += 1
    return n


# ------------------------------------------------------------------ GitHub Issues
def _gh(args):
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _origin_nwo(repo_path: Path):
    try:
        url = subprocess.run(["git", "-C", str(repo_path), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return None
    m = re.match(r"^(?:git@|ssh://git@|https?://)(?:[^@/]+@)?([^/:]+)[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if not m:
        return None
    host, owner, repo = m.groups()
    return f"{owner}/{repo}" if host == "github.com" else f"{host}/{owner}/{repo}"


def gh_labels(nwo, num, remove=None, add=None):
    args = ["issue", "edit", str(num), "--repo", nwo]
    if remove:
        args += ["--remove-label", remove]
    if add:
        args += ["--add-label", add]
    rc, out = _gh(args)
    if rc != 0:
        print(f"  (could not update labels on {nwo}#{num}: {out[:200]}; create the label in the repo?)")


def gh_comment(nwo, num, text):
    rc, out = _gh(["issue", "comment", str(num), "--repo", nwo, "--body", text])
    if rc != 0:
        print(f"  (could not comment on {nwo}#{num}: {out[:200]})")


def github_pull(cfg, todo_dir, state, log):
    s = _s(cfg)
    trig, prog, need = _lbl(cfg, "trigger_label", "agent-ready"), \
        _lbl(cfg, "progress_label", "agent-in-progress"), _lbl(cfg, "needs_info_label", "agent-needs-info")
    min_chars = s.get("min_description_chars", 150)
    repos_dir = ROOT / cfg["paths"].get("repos", "repos")
    known = known_repos(cfg)
    n = 0
    for name, rc_ in cfg.get("repos", {}).items():
        nwo = rc_.get("github") or _origin_nwo(repos_dir / name)
        if not nwo:
            log(f"[{name}] cannot determine GitHub repo; set `github = \"owner/repo\"` in config.toml")
            continue
        rc, out = _gh(["issue", "list", "--repo", nwo, "--assignee", "@me", "--label", trig,
                       "--state", "open", "--json", "number,title,body,url,labels", "--limit", "20"])
        if rc != 0:
            log(f"[{name}] gh issue list failed: {out[:200]}")
            continue
        for it in json.loads(out or "[]"):
            task_id = f"GH-{name}-{it['number']}"
            if task_id in state["imported"]:
                continue
            labels = [l["name"] for l in it.get("labels", [])]
            extra = [r for r in _repos_from_labels(cfg, labels) if r != name and r in known]
            repos = [name] + extra
            body = (it.get("body") or "").strip()
            if len(body) < min_chars:
                log(f"{task_id}: description too short, skipping")
                gh_comment(nwo, it["number"], f"Agent loop did not start this issue: it needs a fuller description "
                           f"(at least {min_chars} characters, with acceptance criteria). Re-add `{trig}` when ready.")
                gh_labels(nwo, it["number"], remove=trig, add=need)
                continue
            write_task(todo_dir, task_id, it["title"], repos, f"github:{nwo}#{it['number']}", body)
            gh_labels(nwo, it["number"], remove=trig, add=prog)
            gh_comment(nwo, it["number"], f"Agent loop picked this up (repos: {', '.join(repos)}). "
                       "I'll comment again with the result.")
            state["imported"].append(task_id)
            log(f"{task_id}: imported -> tasks/todo/{task_id}.md")
            n += 1
    return n


# ------------------------------------------------------------------ public API
def pull(cfg, todo_dir: Path, log=print) -> int:
    """Import newly assigned, labeled tickets. Returns the number of tasks created."""
    if not enabled(cfg):
        return 0
    s, state, n = _s(cfg), load_state(), 0
    for name, fn in (("jira", jira_pull), ("github", github_pull)):
        if s.get(name, {}).get("enabled"):
            try:
                n += fn(cfg, todo_dir, state, log)
            except Exception as e:
                log(f"[{name}] sync error: {e}")
    save_state(state)
    return n


def report(cfg, source, task_id, status, message, urls):
    """Write the result back to the originating ticket. Never raises."""
    if not source or not _s(cfg).get("write_back", True):
        return
    prog = _lbl(cfg, "progress_label", "agent-in-progress")
    final = _lbl(cfg, "done_label", "agent-pr-ready") if status == "done" else _lbl(cfg, "failed_label", "agent-failed")
    links = "\n".join(f"- {r}: {u}" for r, u in (urls or {}).items())
    if status == "done":
        text = (f"Agent loop finished {task_id}.\n{links or message}\n\n"
                "These are DRAFT pull requests generated by an AI agent; please review before merging.")
    else:
        text = f"Agent loop could not complete {task_id}: {message}\nSee the local logs for details."
    kind, _, ref = source.partition(":")
    try:
        if kind == "jira":
            jira_comment(ref, text)
            jira_labels(ref, remove=prog, add=final)
        elif kind == "github":
            nwo, num = ref.rsplit("#", 1)
            gh_comment(nwo, num, text)
            gh_labels(nwo, num, remove=prog, add=final)
    except Exception as e:
        print(f"write-back failed: {e}")
