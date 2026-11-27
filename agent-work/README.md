# Local agent loop

**Start with SETUP.md** for the full step-by-step installation guide.

Task file in, draft PRs out. Runs on your machine with Copilot CLI, git worktrees and `gh`.

```
tasks/todo/TASK-101.md
   -> worktrees/TASK-101/{backend,frontend}   (isolated branches agent/TASK-101)
   -> coder agent (sees all repos of the task)
   -> verify commands -> fix loop (max 3)
   -> reviewer agent (read-only) -> rework loop (max 2)
   -> commit per repo
   -> push + DRAFT PR per repo, cross-linked   (off by default)
   -> task moves to tasks/done or tasks/failed, logs in logs/TASK-101/
```

## Setup (once)

1. Install: Python 3.11+, git, GitHub CLI (`gh auth login`), Copilot CLI (`copilot --help` to confirm it works and you are logged in).
2. Clone your repos into `repos/`:
   ```
   git clone <backend-url>  repos/backend
   git clone <frontend-url> repos/frontend
   ```
3. Edit `config.toml`:
   - `[repos.*]` names must match the folder names; set `base` and the `verify` commands.
   - Check the `[agent]` flags against `copilot --help`. They vary by version.
4. Add `.github/copilot-instructions.md` to each repo (stack, conventions, how to build/test). This matters more than anything else for output quality.

## Daily use

**Ticket mode (tickets assigned to you run automatically):** enable `[sources.jira]` and/or `[sources.github]` in `config.toml`, label a ticket `agent-ready`, and run `python run.py watch`. See SETUP.md step 14a.

```
cp tasks/todo/_TEMPLATE.md tasks/todo/TASK-101.md   # fill it in
python run.py once            # run the oldest task
python run.py task TASK-101   # run a specific one
python run.py watch           # poll tasks/todo continuously
python run.py status
```

## Rollout (do it in this order)

1. **Local only.** Leave `git.push = false`. Run one small task. Open `worktrees/TASK-101/backend` in your editor and read the diff, then read `logs/TASK-101/*.log`.
2. **Tune.** Fix the task template and the repos' instruction files where the agent went wrong.
3. **Draft PRs.** Set `push = true` and `create_pr = true`. PRs are always drafts; you merge.
4. **Watch mode.** Only after several clean runs.
5. **Jira (optional).** Write a small script that turns a Jira issue into a file in `tasks/todo/` using the same template. The loop does not care where files come from.

## Safety checklist

- Draft PRs only. Turn on branch protection so nothing merges without your approval.
- Keep the tool allowlist narrow. If you need `--allow-all-tools`, run inside a dev container or VM.
- No production secrets in the environment. Use a GitHub token scoped to the needed repos.
- Reviewer agent is deliberately read-only and separate from the coder.
- Hard limits are in `[limits]`; one task runs at a time.
- Confirm your company policy allows running agents unattended against company repos from your machine.

## Cleanup

```
git -C repos/backend worktree remove --force ../../worktrees/TASK-101/backend
git -C repos/backend branch -D agent/TASK-101
```

## Troubleshooting

- *Agent does nothing / asks for permission:* a needed tool is not in the allowlist; check the log.
- *Fix loop never passes:* run the verify command by hand in the worktree; usually an environment issue (missing deps, DB, env vars).
- *Multi-repo mismatch:* the Contract section of the task was too vague. Make it explicit.
- *Windows:* works natively; for stronger isolation run it inside WSL.

## Custom agents (the "harness" part)

`agents/task-coder.agent.md` and `agents/task-reviewer.agent.md` are Copilot CLI custom agent profiles that `config.toml` selects with `--agent=...`. Install them once:

```
mkdir -p ~/.copilot/agents && cp agents/*.agent.md ~/.copilot/agents/
```

Or put them in each repo under `.github/agents/` so the team shares them. Edit them to encode your own standards; that is the cheapest way to improve results. Confirm the `tools:` names against the current Copilot docs.
