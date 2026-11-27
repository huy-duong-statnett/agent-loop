#!/usr/bin/env python3
"""
Local agent loop: task file -> worktrees -> coder agent -> verify/fix ->
reviewer agent -> commit -> (optional) push + draft PRs.

Usage:
    python run.py once            # process the oldest task in tasks/todo
    python run.py task TASK-101   # process a specific task from tasks/todo
    python run.py watch           # keep polling tickets + tasks/todo
    python run.py sync            # import tickets assigned to you, then stop
    python run.py status          # show task counts

Requires Python 3.11+, git, gh (for PRs) and your agent CLI (see config.toml).
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
import tomllib
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))

P = CFG["paths"]
TODO = ROOT / P.get("todo", "tasks/todo")
DOING = ROOT / P.get("doing", "tasks/doing")
DONE = ROOT / P.get("done", "tasks/done")
FAILED = ROOT / P.get("failed", "tasks/failed")
REPOS = ROOT / P.get("repos", "repos")
WORKTREES = ROOT / P.get("worktrees", "worktrees")
LOGS = ROOT / P.get("logs", "logs")
for d in (TODO, DOING, DONE, FAILED, REPOS, WORKTREES, LOGS):
    d.mkdir(parents=True, exist_ok=True)

L = CFG["limits"]
MAX_FIX_ROUNDS = L.get("max_fix_rounds", 3)
MAX_REVIEW_ROUNDS = L.get("max_review_rounds", 2)
AGENT_TIMEOUT = L.get("agent_timeout_seconds", 1800)
VERIFY_TIMEOUT = L.get("verify_timeout_seconds", 900)
MAX_DIFF_CHARS = L.get("max_diff_chars", 200_000)

PUSH = CFG["git"].get("push", False)
CREATE_PR = CFG["git"].get("create_pr", False)
BRANCH_PREFIX = CFG["git"].get("branch_prefix", "agent/")


# ---------------------------------------------------------------- helpers
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Log:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def write(self, text: str):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(text)
        print(text, end="" if text.endswith("\n") else "\n")

    def section(self, title: str):
        self.write(f"\n===== [{now()}] {title} =====\n")


def run(cmd, cwd, log: Log, timeout=None, shell=False, label=None):
    """Run a command, log the output, return (returncode, output)."""
    log.write(f"$ {label or (cmd if shell else ' '.join(map(str, cmd)))}   (cwd={cwd})\n")
    try:
        p = subprocess.run(
            cmd, cwd=cwd, shell=shell, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        out = (p.stdout or "") + (p.stderr or "")
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = f"TIMEOUT after {timeout}s\n{(e.stdout or '')}"
        rc = 124
    except FileNotFoundError as e:
        out, rc = f"Command not found: {e}", 127
    log.write(out + f"\n[exit {rc}]\n")
    return rc, out


def notify(message: str):
    cmd = CFG.get("notify", {}).get("command")
    if not cmd:
        return
    try:
        subprocess.run([c.replace("{message}", message) for c in cmd], timeout=30)
    except Exception as e:  # notification must never break the loop
        print(f"notify failed: {e}")


# ---------------------------------------------------------------- ticket sources
def pull_sources():
    """Import tickets assigned to you (Jira / GitHub Issues). Never raises."""
    try:
        import sources
        if sources.enabled(CFG):
            n = sources.pull(CFG, TODO, log=lambda m: print(f"[{now()}] {m}"))
            if n:
                print(f"[{now()}] imported {n} new task(s) from tickets")
    except Exception as e:
        print(f"[{now()}] ticket sync failed: {e}")


def report_source(task, status, message, urls):
    try:
        import sources
        sources.report(CFG, task.meta.get("source"), task.id, status, message, urls)
    except Exception as e:
        print(f"write-back failed: {e}")


# ---------------------------------------------------------------- tasks
class Task:
    def __init__(self, path: Path):
        self.path = path
        text = path.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
        if not m:
            raise ValueError(f"{path.name}: missing front matter (--- ... ---)")
        self.meta = self._parse_meta(m.group(1))
        self.body = m.group(2).strip()
        self.id = self.meta.get("id") or path.stem
        self.repos = self.meta.get("repos") or []
        if not self.repos:
            raise ValueError(f"{path.name}: front matter needs 'repos: [..]'")
        heading = re.search(r"^#\s+(.+)$", self.body, re.MULTILINE)
        self.title = self.meta.get("title") or (heading.group(1) if heading else self.id)
        self.raw = text

    @staticmethod
    def _parse_meta(block: str) -> dict:
        meta = {}
        for line in block.splitlines():
            if ":" not in line or line.strip().startswith("#"):
                continue
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            if v.startswith("[") and v.endswith("]"):
                meta[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
            else:
                meta[k] = v.strip("'\"")
        return meta


def move(path: Path, dest_dir: Path) -> Path:
    dest = dest_dir / path.name
    shutil.move(str(path), str(dest))
    return dest


# ---------------------------------------------------------------- git
def repo_cfg(name: str) -> dict:
    rc = CFG.get("repos", {}).get(name)
    if rc is None:
        raise ValueError(f"Repo '{name}' not defined under [repos.{name}] in config.toml")
    return rc


def base_branch(name: str) -> str:
    return repo_cfg(name).get("base", "main")


def setup_worktrees(task: Task, log: Log) -> Path:
    tdir = WORKTREES / task.id
    tdir.mkdir(parents=True, exist_ok=True)
    for r in task.repos:
        repo = REPOS / r
        if not (repo / ".git").exists():
            raise RuntimeError(f"{repo} is not a git clone. Clone it into repos/{r} first.")
        wt = tdir / r
        if wt.exists():
            log.write(f"worktree {wt} already exists, reusing\n")
            continue
        run(["git", "fetch", "origin"], repo, log)
        rc, out = run(
            ["git", "worktree", "add", "-B", BRANCH_PREFIX + task.id, str(wt),
             f"origin/{base_branch(r)}"], repo, log)
        if rc != 0:
            raise RuntimeError(f"git worktree add failed for {r}")
    # Task copy at the parent level (outside every repo, so never committed)
    (tdir / "TASK.md").write_text(task.raw, encoding="utf-8")
    return tdir


def diff_text(task: Task, tdir: Path, log: Log) -> str:
    parts = []
    for r in task.repos:
        wt = tdir / r
        run(["git", "add", "-A"], wt, log)
        _, d = run(["git", "diff", "--cached", f"origin/{base_branch(r)}"], wt, log)
        parts.append(f"# ===== repo: {r} =====\n{d}")
    text = "\n\n".join(parts)
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS] + "\n\n[... diff truncated ...]"
    return text


# ---------------------------------------------------------------- agent
def call_agent(role: str, prompt: str, cwd: Path, log: Log) -> str:
    template = CFG["agent"][f"{role}_command"]
    cmd = [part.replace("{prompt}", prompt) for part in template]
    log.section(f"agent:{role}")
    rc, out = run(cmd, cwd, log, timeout=AGENT_TIMEOUT, label=f"<{role} agent> {template[0]} ...")
    if rc != 0:
        log.write(f"WARNING: {role} agent exited with {rc}\n")
    return out


CODER_PROMPT = """You are the coding agent. Read TASK.md in the current directory first.
The repositories listed in its front matter are checked out as sub-folders here.
Implement the task across those repos exactly as described, following each repo's
own instruction files and conventions. Add or update tests. Run the verify commands
yourself when possible. Do NOT commit, push, or touch anything outside these folders.
When done, reply with a short summary of what you changed per repo."""

FIX_PROMPT = """The verification step failed. Read TASK.md for context and FAILURES.md for the
failing commands and their output, then fix the problems in the repos here.
Do NOT commit or push. Reply with a short summary of the fix."""

REVIEW_PROMPT = """You are a strict code reviewer. Do NOT modify any files.
Read TASK.md (the requirements) and REVIEW_INPUT.md (the diff of each repo).
Check: acceptance criteria met, bugs, missing tests, security problems, broken
contracts between repos (API shapes, field names), and style problems.
List concrete issues, each with file and reason, marked BLOCKING or MINOR.
Finish with exactly one last line: `VERDICT: APPROVE` or `VERDICT: CHANGES`.
Use CHANGES only if there is at least one BLOCKING issue."""

REWORK_PROMPT = """A reviewer found issues. Read TASK.md and REVIEW_FEEDBACK.md, then address
every BLOCKING issue in the repos here. Do NOT commit or push. Reply with a short summary."""


# ---------------------------------------------------------------- verify
def verify(task: Task, tdir: Path, log: Log):
    """Return (ok, failure_report)."""
    failures = []
    for r in task.repos:
        for cmd in repo_cfg(r).get("verify", []):
            rc, out = run(cmd, tdir / r, log, timeout=VERIFY_TIMEOUT, shell=True)
            if rc != 0:
                tail = out[-6000:]
                failures.append(f"[{r}] `{cmd}` failed (exit {rc}):\n{tail}")
    return (not failures), "\n\n".join(failures)


def verify_and_fix(task: Task, tdir: Path, log: Log) -> bool:
    for attempt in range(MAX_FIX_ROUNDS + 1):
        log.section(f"verify (attempt {attempt + 1})")
        ok, report = verify(task, tdir, log)
        if ok:
            return True
        if attempt == MAX_FIX_ROUNDS:
            return False
        (tdir / "FAILURES.md").write_text(report, encoding="utf-8")
        call_agent("coder", FIX_PROMPT, tdir, log)
    return False


def parse_verdict(text: str):
    m = re.findall(r"VERDICT:\s*(APPROVE|CHANGES)", text)
    return m[-1] if m else None


# ---------------------------------------------------------------- publish
def commit_all(task: Task, tdir: Path, log: Log) -> list[str]:
    """Commit in every repo that has changes; return repos with commits."""
    changed = []
    for r in task.repos:
        wt = tdir / r
        run(["git", "add", "-A"], wt, log)
        rc, _ = run(["git", "diff", "--cached", "--quiet"], wt, log)
        if rc == 0:
            log.write(f"[{r}] no changes\n")
            continue
        run(["git", "commit", "-m", f"{task.id}: {task.title}"], wt, log)
        changed.append(r)
    return changed


def publish(task: Task, tdir: Path, changed: list[str], review: str, log: Log) -> dict:
    urls = {}
    if not (PUSH and CREATE_PR):
        log.write("Push/PR disabled in config.toml (git.push / git.create_pr). "
                  f"Commits are local on branch {BRANCH_PREFIX}{task.id} in {tdir}\n")
        return urls
    branch = BRANCH_PREFIX + task.id
    for r in changed:
        wt = tdir / r
        rc, _ = run(["git", "push", "-u", "origin", branch], wt, log)
        if rc != 0:
            raise RuntimeError(f"push failed for {r}")
        body = (f"Automated draft PR for **{task.id}** ({task.title}).\n\n"
                f"Repos in this task: {', '.join(task.repos)}\n\n"
                f"## Task\n{task.body}\n\n## Automated review notes\n{review[-3000:]}\n\n"
                "_Generated by the local agent loop. Please review carefully._\n")
        body_file = tdir / f"PR_BODY_{r}.md"
        body_file.write_text(body, encoding="utf-8")
        rc, out = run(["gh", "pr", "create", "--draft", "--base", base_branch(r),
                       "--head", branch, "--title", f"{task.id}: {task.title}",
                       "--body-file", str(body_file)], wt, log)
        if rc == 0:
            urls[r] = out.strip().splitlines()[-1]
    # cross-link multi-repo PRs
    if len(urls) > 1:
        links = "\n".join(f"- {r}: {u}" for r, u in urls.items())
        for r in urls:
            bf = tdir / f"PR_BODY_{r}.md"
            bf.write_text(bf.read_text(encoding="utf-8") + f"\n## Related PRs\n{links}\n",
                          encoding="utf-8")
            run(["gh", "pr", "edit", urls[r], "--body-file", str(bf)], tdir / r, log)
    return urls


# ---------------------------------------------------------------- main flow
def process(task_path: Path):
    task = Task(task_path)
    log = Log(LOGS / task.id / f"{datetime.now():%Y%m%d-%H%M%S}.log")
    doing_path = move(task_path, DOING)
    log.section(f"START {task.id}: {task.title} repos={task.repos}")
    try:
        tdir = setup_worktrees(task, log)

        call_agent("coder", CODER_PROMPT, tdir, log)
        if not verify_and_fix(task, tdir, log):
            raise RuntimeError("verification still failing after max fix rounds")

        review_text = "(no review run)"
        for rnd in range(MAX_REVIEW_ROUNDS + 1):
            (tdir / "REVIEW_INPUT.md").write_text(diff_text(task, tdir, log), encoding="utf-8")
            review_text = call_agent("reviewer", REVIEW_PROMPT, tdir, log)
            verdict = parse_verdict(review_text)
            log.write(f"Reviewer verdict (round {rnd + 1}): {verdict}\n")
            if verdict == "APPROVE" or rnd == MAX_REVIEW_ROUNDS:
                break
            (tdir / "REVIEW_FEEDBACK.md").write_text(review_text[-12000:], encoding="utf-8")
            call_agent("coder", REWORK_PROMPT, tdir, log)
            if not verify_and_fix(task, tdir, log):
                raise RuntimeError("verification failing after review rework")
        if verdict != "APPROVE":
            review_text += "\n\n> NOTE: reviewer did not approve after max rounds."

        changed = commit_all(task, tdir, log)
        if not changed:
            raise RuntimeError("agent produced no changes")
        urls = publish(task, tdir, changed, review_text, log)

        move(doing_path, DONE)
        msg = f"{task.id} done. " + (", ".join(urls.values()) or f"local branches ready in {tdir}")
        log.write(msg + "\n")
        notify(msg)
        report_source(task, "done", msg, urls)
    except Exception as e:
        log.write(f"FAILED: {e}\n{traceback.format_exc()}\n")
        move(doing_path, FAILED)
        notify(f"{task.id} FAILED: {e}")
        report_source(task, "failed", str(e), {})


def next_task(name: str | None = None) -> Path | None:
    if name:
        p = TODO / (name if name.endswith(".md") else f"{name}.md")
        return p if p.exists() else None
    files = sorted((f for f in TODO.glob("*.md") if not f.name.startswith("_")),
                   key=lambda f: f.stat().st_mtime)
    return files[0] if files else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["once", "task", "watch", "status", "sync"])
    ap.add_argument("name", nargs="?")
    a = ap.parse_args()

    if a.cmd == "sync":
        pull_sources()
        return
    if a.cmd == "status":
        for d in (TODO, DOING, DONE, FAILED):
            print(f"{d.name:7} {len([f for f in d.glob('*.md') if not f.name.startswith('_')])}")
        return
    if a.cmd in ("once", "task"):
        if a.cmd == "once":
            pull_sources()
        t = next_task(a.name if a.cmd == "task" else None)
        if not t:
            print("No task found in", TODO)
            return
        process(t)
        return
    interval = L.get("poll_seconds", 30)
    print(f"Watching {TODO} every {interval}s. Ctrl+C to stop.")
    ticket_every = CFG.get("sources", {}).get("poll_seconds", 120)
    last_pull = 0.0
    while True:
        if time.time() - last_pull >= ticket_every:
            pull_sources()
            last_pull = time.time()
        t = next_task()
        if t:
            process(t)
        else:
            time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
