# SETUP GUIDE: local agent loop with GitHub Copilot

Follow the steps in order. Total time: about 1 to 2 hours for the first working run.
Commands are shown for **Windows (PowerShell)**, **macOS**, and **Linux/WSL**.

---

## 0. What you are building

```
You write a task file ──► tasks/todo/TASK-101.md
                              │
                  run.py picks it up
                              ▼
        worktrees/TASK-101/backend   (own branch agent/TASK-101)
        worktrees/TASK-101/frontend  (own branch agent/TASK-101)
                              │
             coder agent (Copilot CLI, --agent=task-coder)
                              │
          verify commands (tests / lint / build) ◄─┐
                              │ fail → agent fixes ─┘ (max 3 rounds)
                              ▼
        reviewer agent (Copilot CLI, read-only) ──► issues → coder reworks (max 2 rounds)
                              │
                       commit in each repo
                              │
        (optional) push + DRAFT PR per repo, linked to each other
                              │
        tasks/done/  or  tasks/failed/   +  logs/TASK-101/*.log
                              │
                     YOU review and merge
```

Files in this package:

| File | Purpose |
|---|---|
| `run.py` | The orchestrator loop |
| `config.toml` | All settings: repos, verify commands, agent commands, limits |
| `doctor.py` | Health check for your machine and config |
| `setup.sh` / `setup.ps1` | One-time bootstrap (folders + install agents + run doctor) |
| `agents/task-coder.agent.md` | Copilot custom agent that writes code |
| `agents/task-reviewer.agent.md` | Copilot custom agent that reviews (read-only) |
| `templates/copilot-instructions.md` | Template for each repo's Copilot instructions |
| `tasks/todo/_TEMPLATE.md` | Task template (copy it for each task) |
| `examples/TASK-001-example.md` | A small example task for your first test run |
| `sources.py` | Ticket-driven mode: pulls tickets assigned to you from Jira / GitHub Issues, writes results back |
| `jira_import.py` | Optional: manually import specific Jira issues as task files |
| `README.md` | Short overview |

---

## 1. Before you start: approvals and expectations

Do this first. It can save you from a blocked setup later.

- [ ] **Copilot CLI is allowed.** If your Copilot licence comes from your company, an admin must enable the **Copilot CLI** policy in the organization's Copilot settings. If `copilot` says it is unavailable or not permitted, ask your admin.
- [ ] **Policy check.** Confirm your company allows running AI agents on company repos from your own machine, and what code/data may be sent to Copilot.
- [ ] **Git permissions.** You can clone the repos and push branches (but not necessarily merge to main).
- [ ] **Expectations.** The agents write and review code; **you** approve and merge. Treat the output like a fast junior developer's work: review everything, especially auth, data handling, and database migrations.

---

## 2. Install prerequisites

You need: **Git**, **Python 3.11+**, **GitHub CLI (`gh`)**, **Node.js** (for Copilot CLI and probably your frontend), **Copilot CLI**, plus whatever toolchains your repos need to build and test (for example Java, .NET, pnpm, Docker).

### Windows (PowerShell)

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
winget install --id GitHub.cli -e
winget install --id OpenJS.NodeJS.LTS -e
```
Close and reopen PowerShell so PATH updates. For stronger isolation, consider doing everything inside **WSL2** (Ubuntu) and following the Linux steps instead.

### macOS (Homebrew)

```bash
brew install git python@3.12 gh node
```

### Linux / WSL (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install -y git python3 python3-venv curl
# GitHub CLI: follow https://github.com/cli/cli/blob/trunk/docs/install_linux.md
# Node.js LTS: https://nodejs.org (or nvm)
```

### Copilot CLI (all platforms)

```bash
npm install -g @github/copilot
```
Check the current install instructions in GitHub's Copilot CLI docs if this fails; Node must be a recent version.

### Verify

```bash
git --version
python --version        # must be 3.11 or newer  (use python3 on macOS/Linux)
gh --version
node --version
copilot --version
```

---

## 3. Log in

```bash
gh auth login           # choose GitHub.com (or your enterprise host), HTTPS, login with browser
gh auth status          # must say "Logged in"
```

Copilot CLI:
```bash
copilot
```
On first run, sign in when prompted (use `/login` inside the CLI if it does not ask). Use the GitHub account that has your company Copilot seat. Type `/exit` when done.

If you use SSO for your GitHub organization, authorize your token for the org when `gh` or git asks.

---

## 4. Put the folder in place

Copy this whole `agent-work` folder to a permanent place **outside** your project repos, for example:

- Windows: `C:\Users\<you>\agent-work`
- macOS/Linux: `~/agent-work`

All following commands run from inside that folder:
```bash
cd ~/agent-work        # Windows: cd C:\Users\<you>\agent-work
```
(Optional: `git init` this folder to version your tasks and config. `.gitignore` already excludes repos, worktrees and logs.)

---

## 5. Clone your repos into `repos/`

The folder name becomes the repo name you use in tasks and config.

```bash
git clone https://github.com/<org>/<backend-repo>.git   repos/backend
git clone https://github.com/<org>/<frontend-repo>.git  repos/frontend
```

Use these as dedicated clones for the agents. Do not use your daily working copy, because the loop runs `git fetch` and creates worktrees from it. Leave the clones on their default branch with no uncommitted changes.

---

## 6. Add Copilot instructions to each repo (most important step for quality)

For each repo:

1. Copy `templates/copilot-instructions.md` to `<repo>/.github/copilot-instructions.md`.
2. Fill in the exact commands (install, test, single test, lint, build), the project layout, conventions, and "never do" rules.
3. Commit and push it to the repo (through a normal PR, since the agent worktrees are created from `origin/<base>`, so the file must be on the base branch to be seen by the agent).

If you skip this, the agent will guess your standards and the results will be noticeably worse.

---

## 7. Edit `config.toml`

Open `config.toml` and change:

1. **`[repos.<name>]` sections.** Names must match the folders in `repos/`. Add or remove sections as needed.
   - `base`: the branch PRs target (often `main`, `master`, or `develop`).
   - `verify`: commands run inside the repo's worktree after the agent finishes. All must exit 0. Examples:
     - Python: `["pip install -r requirements.txt", "pytest -q", "ruff check ."]`
     - Node: `["npm ci", "npm run lint", "npm test -- --watchAll=false", "npm run build"]`
     - Java/Maven: `["mvn -q verify"]`
     - .NET: `["dotnet test"]`
   - Tip: put your real, fast checks first. Every command here is also what the agent is told to fix when it fails.
2. **`[agent]` commands.** Run `copilot --help` and confirm the flags exist in your version: `--agent`, `-p/--prompt`, `--allow-tool`, `--deny-tool`. Flags change between releases; adjust the lists to match.
   - The coder needs permission to edit files and run your build/test tools. The default allowlist covers `write`, `git`, `npm`, `pytest`. **Add your own tools** (for example `shell(mvn)`, `shell(dotnet)`, `shell(pnpm)`).
   - The reviewer must stay read-only; leave its `--deny-tool` lines as they are.
3. **`[limits]`**: retries, timeouts. The defaults are fine to start.
4. **Leave `[git] push = false` and `create_pr = false`** for now.

---

## 8. Run the bootstrap

**macOS / Linux / WSL**
```bash
chmod +x setup.sh
./setup.sh
```

**Windows PowerShell**
```powershell
# if scripts are blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\setup.ps1
```

This creates the folders, copies the two agent files into `~/.copilot/agents/` (Windows: `%USERPROFILE%\.copilot\agents`), and runs `doctor.py`.

**Read the doctor output.** Fix every `[FAIL]`, then re-run `python doctor.py` (or `python3`). `[WARN]` items are optional but worth fixing (for example missing instruction files).

> You can also share the agents with your whole team: put the two `.agent.md` files in each repo under `.github/agents/`.

---

## 9. Sanity-test Copilot CLI by hand (5 minutes)

Before automating, confirm Copilot works in your environment:

```bash
cd repos/backend
copilot -p "Describe the structure of this repository in 5 bullet points"
```

Then test the custom agent:
```bash
copilot --agent=task-reviewer -p "Say hello and list the tools you are allowed to use"
cd ../..
```

If this fails, fix it now (login, org policy, flags). The automation cannot work until this does. Check the [Troubleshooting](#16-troubleshooting) table.

---

## 10. Your first task (single repo, local only)

1. Pick something tiny and safe in one repo: add a unit test, fix a typo in docs, add a trivial endpoint.
2. Copy the example and edit it to fit your repo:
   ```bash
   cp examples/TASK-001-example.md tasks/todo/TASK-001.md      # Windows: copy examples\TASK-001-example.md tasks\todo\TASK-001.md
   ```
   Set `repos: [backend]` (or your repo name) and write real acceptance criteria.
3. Run:
   ```bash
   python run.py once        # python3 on macOS/Linux
   ```
4. Watch the output. A run goes: worktree created → coder → verify (→ fixes) → reviewer → commit.
5. Inspect the result:
   - The code: open `worktrees/TASK-001/backend` in your editor, or run
     `git -C worktrees/TASK-001/backend diff origin/main` and `git -C worktrees/TASK-001/backend log -3`.
   - The story: `logs/TASK-001/<timestamp>.log` shows every command, agent output, and test result.
   - The status: the task file moved to `tasks/done/` or `tasks/failed/`.
6. Judge it honestly. Would you approve this in a PR? If not, note what went wrong: usually a vague task, missing instructions in the repo, or a tool missing from the allowlist.

**Re-running a task:** move the file from `tasks/failed/` (or `done/`) back to `tasks/todo/`, and clean up first (see [Cleanup](#17-cleanup-and-maintenance)) so you start from a fresh worktree.

---

## 11. Tune before trusting

Repeat step 10 with 3 to 5 small tasks. After each one:
- Add what the agent got wrong to `.github/copilot-instructions.md` in that repo.
- Edit `agents/task-coder.agent.md` / `task-reviewer.agent.md` to encode your standards, then re-copy them to `~/.copilot/agents/`.
- Add missing tools to the coder's `--allow-tool` list.
- Make your task descriptions more specific where the agent guessed.

This tuning is where most of the value comes from.

---

## 12. Multi-repo tasks (backend + frontend)

Use `repos: [backend, frontend]`. The agent runs from `worktrees/TASK-xxx/` and sees both repos side by side.

The **Contract** section is what keeps them consistent. Spell out:
- endpoint paths and methods,
- request and response JSON (field names and types),
- error codes,
- auth rules.

Example:
```markdown
## Contract
POST /api/orders
Body:    { "customerId": string, "items": [{ "sku": string, "qty": number }] }
201 ->   { "orderId": string, "status": "PENDING" }
400 ->   { "error": string }
```

Put per-repo instructions under "Per-repo work". Keep tasks small: one feature slice at a time beats a huge epic.

---

## 13. Enable pushing and draft PRs

Only after several good local runs:

1. In `config.toml`:
   ```toml
   [git]
   push      = true
   create_pr = true
   ```
2. Make sure `gh auth status` is green and you can push branches to the repos.
3. **Turn on branch protection** on each repo's base branch (require PR + human approval + passing checks) so nothing can merge without you. Ask your admin if you cannot.
4. Run a task. The loop pushes `agent/TASK-xxx` and opens a **draft** PR per repo, with the task text and reviewer notes in the description, and cross-links PRs in multi-repo tasks.
5. Review the PRs like any teammate's: read the diff, run the app, check CI.

---

## 14. Optional: Jira import

1. Create a Jira API token (Jira Cloud: Atlassian account → Security → API tokens).
2. Set environment variables in your shell (do not commit them):
   - macOS/Linux:
     ```bash
     export JIRA_BASE_URL="https://yourcompany.atlassian.net"
     export JIRA_EMAIL="you@company.com"
     export JIRA_API_TOKEN="..."
     ```
   - Windows PowerShell:
     ```powershell
     $env:JIRA_BASE_URL="https://yourcompany.atlassian.net"
     $env:JIRA_EMAIL="you@company.com"
     $env:JIRA_API_TOKEN="..."
     ```
   - Jira Server/Data Center: set `JIRA_PAT` instead of email + token.
3. Add `agent-ready` and `agent-ramp-backend` and/or `agent-ramp-frontend`
   to your assigned, not-Done tickets. Import all matching tickets across projects:
   ```bash
   python jira_import.py
   ```
   Both repo labels produce one task covering both repositories. Tickets without a
   repository mapping and tasks already in `todo`, `doing`, `done` or `failed` are skipped.
   This manual import does not change Jira labels or comments.
   Keep the automatic Jira source disabled when using this manual importer; it uses
   separate tracking and can import the same tickets again.
4. **Open the generated `tasks/todo/<issue-key>.md` and improve it** (add the contract and acceptance criteria if the Jira text is thin) before running the loop. Garbage in, garbage out.

Check your company policy before linking Jira content to automated tools.

---

## 14a. Ticket-driven mode: "the ticket is assigned to me, the agents do it"

This is the mode most people actually want. Everything runs **locally on your PC** (no cloud agent). The loop watches Jira and/or GitHub Issues, and when a ticket is **assigned to you** and you give it the trigger label, it becomes a task, the agents work on it, and the PR links come back as a comment on the ticket.

```
Ticket assigned to you  +  label `agent-ready`
        │  (python run.py watch checks every 2 minutes)
        ▼
 tasks/todo/PROJ-123.md  ──► coder ► verify ► reviewer ► commit ► draft PRs
        │
        ▼
 Comment on the ticket with PR links, label → `agent-pr-ready`  (or `agent-failed`)
```

**Why a label as well as assignment?** So that agents never grab a ticket you did not intend. You assign work to yourself all day; only tickets you mark `agent-ready` are delegated. It also lets you write the ticket properly first.

What happens to each ticket:

| Situation | What the loop does |
|---|---|
| Assigned to you + `agent-ready`, good description, repo known | Imports it, swaps label to `agent-in-progress`, comments "picked up", runs the loop |
| Description shorter than 150 chars, or no repo mapping | Does **not** run it. Comments what is missing, label → `agent-needs-info`. Fix it and re-add `agent-ready` |
| Finished | Comments PR links (or "local branches ready" if push is off), label → `agent-pr-ready` |
| Failed | Comments the failure reason, label → `agent-failed`; task goes to `tasks/failed/` |

### Setup for Jira

1. Create the five labels' worth of convention in Jira (labels are free-form, no setup needed): `agent-ready`, `agent-in-progress`, `agent-pr-ready`, `agent-failed`, `agent-needs-info`.
2. Tell the loop which repos a ticket touches. Either add labels `repo-backend`, `repo-frontend` (matching your `[repos.*]` names), or use Jira components with those names.
3. Set credentials in your shell (never commit them; see step 14 for how), then enable the source in `config.toml`:
   ```toml
   [sources.jira]
   enabled = true
   jql = 'assignee = currentUser() AND labels = "agent-ready" AND statusCategory != Done ORDER BY created ASC'
   ```
   Adjust the JQL if you want to restrict by project, e.g. `project = PROJ AND ...`.
4. Test the connection without running any agents:
   ```bash
   python run.py sync
   ```
   It prints what it imported. Check `tasks/todo/` and read the generated file.

### Setup for GitHub Issues

1. Create the labels in each repo: `agent-ready`, `agent-in-progress`, `agent-pr-ready`, `agent-failed`, `agent-needs-info` (`gh label create agent-ready --repo owner/repo`). Label updates fail quietly if a label does not exist, so create them first.
2. In `config.toml`:
   ```toml
   [sources.github]
   enabled = true
   ```
   The GitHub repo is read from each clone's `origin` URL. If that does not work (for example a custom remote alias), add `github = "owner/repo"` under that `[repos.<name>]`.
3. A GitHub issue lives in one repo, so it creates a task for that repo. To include more repos, add labels such as `repo-frontend` to the same issue and put the API contract in the issue text.
4. Test: `python run.py sync`.

You can enable Jira and GitHub together.

### Run it

```bash
python run.py watch
```
Each cycle it imports newly labeled tickets, then runs one task at a time. `python run.py once` also imports tickets first, then runs the oldest task. `python run.py sync` only imports.

### Tips for tickets the agent can handle

- Write the ticket like a spec: goal, acceptance criteria, and for multi-repo work the exact API contract (paths, JSON fields, error codes).
- Keep tickets small: a bug fix, one endpoint, one screen. Split epics before labeling.
- Review the generated `tasks/todo/<KEY>.md` the first few times; that is exactly what the agent will read.
- Remember it is *your* ticket: the PRs are drafts in your name. Review before marking them ready.

### Limits you should know

- Your PC has to be on, awake, and online for tickets to be picked up.
- Jira ticket **status transitions** (To Do → In Progress → Done) are not automated, because workflows differ per team. The loop uses labels and comments only. Tell me your workflow if you want transitions added.
- The Jira search endpoint differs between Jira Cloud and Server/Data Center; the code tries the newer Cloud one first and falls back to the classic one. I tested the logic against a mock server, not your real Jira.
- Company policy: confirm you may connect ticket content to automated tooling.

---

## 15. Running continuously (watch mode)

Only after the earlier steps are solid.

```bash
python run.py watch
```
It polls `tasks/todo/` every 30 seconds (`poll_seconds` in `[limits]`) and, if a ticket source is enabled (step 14a), checks your assigned tickets every `[sources] poll_seconds`. It processes one task at a time.

To keep it running when you close the terminal:
- **macOS/Linux/WSL:** `tmux new -s agents` then run the command, detach with `Ctrl+B D`. Or `nohup python3 run.py watch > watch.log 2>&1 &`.
- **Windows:** Task Scheduler → create a task "At log on" running `python.exe` with arguments `run.py watch` and "Start in" set to your `agent-work` folder.
- Your PC must stay awake and online. Adjust sleep settings if needed.

Optional notifications: fill in the `[notify]` section in `config.toml` with a Teams or Slack webhook so you get a message when a task finishes or fails.

**Daily rhythm that works well**
1. Morning: write or import 3 to 5 well-specified tasks into `tasks/todo/`.
2. Start `run.py watch`.
3. During the day: review finished PRs, move failed tasks back with better descriptions.
4. Merge what you approve.

---

## 16. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `copilot: command not found` | Not installed or PATH not refreshed | `npm install -g @github/copilot`, reopen terminal |
| Copilot says the feature/CLI is disabled | Org policy | Ask admin to enable the **Copilot CLI** policy |
| Agent exits immediately with an "unknown option" error | CLI flags differ in your version | `copilot --help`, then fix the `[agent]` commands in `config.toml` |
| Agent runs but changes nothing; log mentions permission or tool denied | Tool not in the allowlist | Add `--allow-tool 'shell(<tool>)'` for the tool in the log |
| `agent produced no changes` | Task too vague, or agent blocked by permissions | Read the agent output in the log; sharpen the task |
| Verify keeps failing after 3 rounds | Environment problem (missing deps, DB, env vars) | Run the same command by hand in `worktrees/<TASK>/<repo>`; fix the environment or the command |
| `git worktree add failed` | Branch/worktree already exists | See [Cleanup](#17-cleanup-and-maintenance) |
| `<repo> is not a git clone` | Folder name mismatch | Names in `config.toml` must equal folders in `repos/` |
| `origin/main not found` | Wrong `base` | Set `base` to your real default branch |
| `push failed` | Permissions/SSO/branch rules | `gh auth status`; authorize SSO; check you may push branches |
| `gh pr create` fails | Not authenticated, or PR already exists | `gh auth status`; check the repo for an existing PR |
| Frontend and backend PRs disagree | Contract not explicit | Rewrite the task's Contract section; re-run |
| Reviewer never prints a verdict | Model ignored format | Harmless: the loop proceeds after max rounds and notes it in the PR |
| Windows: script blocked | PowerShell policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Windows: command too long | Rare; very long args | Prompts already go through files; keep task files concise |

Always start debugging in `logs/<TASK>/<timestamp>.log`.

---

## 17. Cleanup and maintenance

Remove a task's worktrees and branches (replace names as needed):

```bash
git -C repos/backend  worktree remove --force ../../worktrees/TASK-101/backend
git -C repos/frontend worktree remove --force ../../worktrees/TASK-101/frontend
git -C repos/backend  branch -D agent/TASK-101
git -C repos/frontend branch -D agent/TASK-101
rm -rf worktrees/TASK-101          # Windows: rmdir /s /q worktrees\TASK-101
git -C repos/backend worktree prune
```

Other upkeep:
- Delete old folders under `logs/` now and then.
- After a PR is merged, clean up its worktree and delete the remote branch.
- Update tools occasionally: `npm install -g @github/copilot@latest`, `gh` and Python via your package manager. After updating Copilot CLI, run `python doctor.py` and a small test task.
- Re-copy the agent files to `~/.copilot/agents/` whenever you edit them.

---

## 18. Safety rules (do not skip)

1. **Draft PRs only; humans merge.** Use branch protection.
2. **Narrow tool permissions.** If you ever need `--allow-all-tools`, run the whole loop inside a VM, dev container, or WSL, not on your bare machine.
3. **No production secrets** in your shell environment, repos, or task files. Give the agent only dev/test credentials.
4. **Least-privilege GitHub token** (the one from `gh auth login`): do not use an admin account.
5. **Read every diff.** Pay extra attention to authentication, authorization, input validation, dependency changes, CI/CD files, and database migrations.
6. **Keep tasks small and specific.** Big vague tasks are where agents go wrong.
7. **One task at a time** until you are confident; the loop is sequential by design.
8. **Follow company policy** on AI tools and data.

---

## 19. Command cheat sheet

```bash
./setup.sh | .\setup.ps1              # bootstrap
python doctor.py                       # health check
python run.py once                     # run oldest task in tasks/todo
python run.py task TASK-101            # run a specific task
python run.py watch                    # keep processing tasks/todo
python run.py status                   # counts per folder
python run.py sync                     # import tickets assigned to you (needs a [sources.*] enabled)
python jira_import.py                                   # optional
copilot --help                         # verify CLI flags
gh auth status                         # verify GitHub login
```

---

## 20. What I could and could not verify

- Ticket-driven mode was tested against a **mock Jira server** and a **fake `gh`**: import, endpoint fallback, bouncing thin tickets, label swaps, comments, no double import, and write-back after completion.
- Tested end to end in a sandbox with a **stand-in agent** and a local git remote: worktree creation, agent call, verify, review, commit, move to done, and the failure path to `tasks/failed/`. The health check and Jira script were syntax- and error-path-tested.
- **Not tested** against the real Copilot CLI, GitHub, `gh`, or Jira, since those are not available in the sandbox. The Copilot CLI flags and custom-agent `tools:` names in this package follow GitHub's documentation as I found it, but Copilot changes often. Section 9 and the troubleshooting table are there to catch any mismatch quickly.

1. run python jira_import.py to import tickets to todo list

'''
$env:JIRA_BASE_URL = "https://your org.atlassian.net"
$env:JIRA_EMAIL = "your email"
$token = Read-Host "Jira API token" -AsSecureString
$env:JIRA_API_TOKEN = [System.Net.NetworkCredential]::new("", $token).Password
python jira_import.py
'''

2. run python run.py watch to do code and send pr
