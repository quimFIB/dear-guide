# Quick start: the agent plugin

A CLI only records what somebody remembers to run, and the thing that forgets is
an agent working across many sessions. The plugin turns three of the four habits
into mechanisms, for **Claude Code** and **opencode** alike, and adds five
commands for the times you want to ask rather than wait to be told.

|  | how |
|---|---|
| **read the frontier first** | `dg brief` is injected at the start of every session, and again after a compaction — no rule in an instructions file, no "read this first" |
| **know the discipline** | the `dear-guide` skill: the model, the rules, the flag-complete commands. Loaded on demand, not carried in every context |
| **refuse the contradictions** | a `git commit` that would leave the graph invalid is denied, quoting the rule that broke and the command that fixes it |
| **ask on demand** | `/dg:brief` `/dg:frontier` `/dg:tasks` `/dg:context` `/dg:serve` — the same five files on both hosts, `/dg-brief` and friends on opencode |

The interesting parts live in `dg` — `dg brief`, `dg gate`, `dg context` — so
each adapter is a few dozen lines of translation with no policy of its own, and
a command file is three lines around a `dg` invocation.

## Install

Two steps, always. The hosts distribute plugins their own way and neither
installs Python packages.

Throughout, `/path/to/dear-guide` is your checkout of this
repository.

### 1. The CLI (both hosts)

```sh
pip install -e /path/to/dear-guide
dg --version        # so an adapter can tell when the two halves have drifted
```

### 2a. Claude Code

```
/plugin marketplace add /path/to/dear-guide
/plugin install dg
```

### 2b. opencode

Symlinks, since opencode has no marketplace:

```sh
repo=/path/to/dear-guide
ln -s "$repo/skills/dear-guide"      ~/.config/opencode/skills/dear-guide
ln -s "$repo/opencode/dear-guide.ts" ~/.config/opencode/plugins/dear-guide.ts
for c in "$repo"/commands/*.md; do
  ln -s "$c" ~/.config/opencode/commands/"dg-$(basename "$c")"
done
```

Three gotchas: the directories are **plural** (`plugins/`, `commands/` — the
singular form loads nothing and says nothing about it); `commands/` is at the
**repo root**, not under `opencode/`, because one copy serves both hosts; and
opencode also reads `~/.claude/skills/` directly, so an existing skills
directory works as the symlink target instead. It is the same file either way.

**The `dg-` the loop prepends is not decoration, and it is why the symlink is
named differently from its target.** Claude Code namespaces a plugin's commands
under the plugin's own name, so `commands/brief.md` is `/dg:brief` there with
nothing to add. opencode's user-scoped command directory is a flat namespace
shared with every other tool you have installed there, where `/context` and
`/tasks` are names something else will already have taken — so the prefix goes
on the link. One file, one name per host, no second copy to keep in step.

Check it took:

```sh
opencode debug skill  | grep dear-guide  # the skill was found
opencode debug config | grep dear-guide  # the plugin and the commands loaded
```

## What a session looks like

Open a session in a project with a `decisions.json` and the brief arrives before
you type anything:

```
DECISION GRAPH  /home/you/retrieval-index  4 decisions: OPEN 1, PROVISIONAL 2, REOPENED 1
Record what gets settled with `dg` -- see the decisions skill.

FRONTIER (2) -- not settled
  D01  REOPENED  Exact or approximate search?  [Search]
       decidable now
       note: the crawl finished at 48M vectors, five times the ~10M threshold in the…
  D04  OPEN      How does the index absorb new documents?  [Index]
       decidable now

RESTING ON A PREMISE UNDER REVIEW (2) -- PROVISIONAL, so not in the frontier
  D02  Which distance metric?  [Search]  rests on D01
  D03  How are queries spread across cores?  [Serving]  rests on D01

STAGED BUT NOT APPLIED: 0 decision, 0 task

TASKS  3: DONE 1, TODO 2   (2 ready, 0 blocked)
  premise under review (2) -- work resting on a decision being re-examined
    T02  Pin the scan threads to the performance cores  <- because D03, PROVISIONAL
    T03  Check in CI that the encoder still normalises  <- because D02, PROVISIONAL
CHECK: clean, 2 warning(s) -- `dg check`
```

That is `dg brief`, verbatim — you can run it yourself any time, and `/dg:brief`
(`/dg-brief` on opencode) prints it on demand, whether or not the injection hook
fired. [A session, start to finish](session-walkthrough.md) carries this same
project through a whole session, including the turn that put D01 back in the
frontier.

**In a directory with no `decisions.json`, nothing happens at all**: no output,
no error, no cost. A plugin is installed for a user, not for a project.

## The commit gate

Every `bash`/`Bash` call carrying one of `dg gate --triggers`' words is handed
to `dg gate`, which answers `allow`, `warn`, `ask` or `deny`. Four outcomes you
will actually see:

**Clean graph** — silence, the command runs.

**Staged but unapplied work** → `ask`, because it is a judgement call about work
in progress:

```
2 decision op(s) are staged and not applied. .dgraph-pending.json is gitignored,
so committing now drops them from the record with no trace in the diff.
`dg apply` writes them; `dg clear` discards them.
```

**A generated view that has fallen behind** → `warn`. The command runs; you are
told once, at the last moment it is cheap to fix:

```
decision-graph.md no longer matches decisions.json, and this commit records it.
`dg render` rebuilds it; committing as it stands leaves the generated view
behind until someone renders again.
```

The same warning covers the case `dg check` cannot see, because `check` compares
the worktree: staging `decisions.json` without `decision-graph.md` produces a
commit whose store and view disagree. Both are said, neither refuses — the view
is generated, `dg render` rebuilds it, and every `dg apply` rebuilds it anyway.

**An invalid graph** → `deny`, quoting the rule and the remedy:

```
The decision graph is not valid, so this commit would record a contradiction:
  [propagation] D02 is DECIDED but rests on D01 (REOPENED) — mark it
  PROVISIONAL or settle the premise
Fix it first: `dg check` names every rule that broke.
```

The gate is scoped to the project it runs in — `git -C /elsewhere commit` into a
different repository is allowed, since it records nothing about this graph.

## The commands

Five files in `commands/` at the repo root, loaded by Claude Code from the
plugin and by opencode from a symlink. Each is a `description`, a `dg`
invocation, and a sentence saying how to read the output — no policy, for the
same reason the adapters have none.

One file, two names: Claude Code takes the namespace from the plugin, opencode
from the prefix the install put on the link.

| Claude Code | opencode | |
|---|---|---|
| `/dg:brief` | `/dg-brief` | the whole situation: frontier, work under review, what is staged, whether the graph is valid |
| `/dg:frontier` | `/dg-frontier` | what could be picked up now — unsettled questions beside startable work |
| `/dg:tasks` | `/dg-tasks` | the backlog, with the cross-graph reading of what each piece waits on |
| `/dg:context <id> [--full]` | `/dg-context <id>` | the chain of premises a decision or a task rests on |
| `/dg:serve` | `/dg-serve` | the graphs in a browser |

Two of them are worth a note.

**`/dg:context` is the one to run before dispatching a subagent.** A fresh
context knows the task and nothing about why it exists; `dg context T14` prints
the chain and ends with the reading. Pass `--full` when the output is going into
the dispatch — the default clips each premise's answer to a sentence, which is
right for a question you are asking yourself and wrong for an agent that cannot
ask a follow-up:

```
→ the premise D02 is PROVISIONAL: it rests on something under review, so this
  work may not survive the outcome
```

Paste that into the dispatch and the agent starts knowing what it must not
quietly invalidate.

**`/dg:serve` returns immediately.** `dg serve` blocks forever, so a command file
that ran it would hang the session; `dg serve --detach` starts it in its own
session, prints the URL and exits. It is idempotent — run it twice and the
second run reports the first one's URL — and `dg serve --stop` ends it.

## The skill

`skills/dear-guide/SKILL.md` is one file both hosts read — the model, the rules,
the command table, the staging workflow. It loads on demand when a decision is
in play, rather than sitting in every context. Tests assert that the skill's and the
commands' tables only name subcommands that actually exist, that neither adapter
names any `dg` subcommand but `brief` and `gate`, and that no command file runs
something that would block.

Ask the agent to record a decision and it will reach for the skill, then run
something like:

```sh
dg decide D12 \
  --answer "HNSW with M=32, efConstruction=200 — the sweep in bench/ann-sweep.md." \
  --source "bench/ann-sweep.md" \
  --falsifier "recall@10 against exact search falls below 0.95 on the held-out queries" \
  --opens "D14,D15"
dg apply
```

Agents apply their own work. `apply` validates a copy before writing anything,
the store is append-only, and the result is a git-tracked diff you review like
any other — whereas an op staged into a gitignored file and then committed over
is unrecoverable.

## Switching it off

`DG_HOOK_OFF=1` disables both the brief and the gate. On opencode it has to be
in opencode's own environment, not in front of the command being run — the
plugin's environment is the host's.

## Known limits

- **opencode: the gate does not see subagent shell calls.**
  `tool.execute.before` is not invoked for tools run by agents spawned through
  the `task` tool ([opencode#5894](https://github.com/sst/opencode/issues/5894)),
  so a commit made from inside one is not gated.
- **opencode: injecting the brief is the one part not guaranteed by the API.**
  There is no session-start hook, so the plugin prepends to the first user
  message. If a future version stops honouring that, `/dg-brief` still works.
- **opencode expresses `ask` as a refusal that says whose call it is.** Its
  permission API carries no reason field, and a refusal the model cannot read is
  a refusal it retries.
- **Version skew is real** — the plugin and the package install separately.
  `dg --version` exists so an adapter can tell; the Claude Code brief hook says
  so explicitly when it meets a `dg` too old to know `dg brief`.

## What stays a habit

**Recording a decision at the moment it is made.** Nothing a host can observe
reveals that something was settled — it is a property of the reasoning, not of a
tool call — and the two obvious implementations both fail. A turn-end prompt
asking "did anything get decided?" is wrong on almost every turn and teaches the
model to say no; a nag on every commit that does not touch `decisions.json` is
wrong on most commits, and the habituation would cost the two mechanisms that do
work.

So the brief arrives with ids and titles, the skill is one hop away, and the
compaction boundary asks once — at the moment prose is actually about to be
lost.

## Where to go next

- [A session, start to finish](session-walkthrough.md) — all three mechanisms in
  one worked session, with the real output of every command.
- [How it works, and why](how-it-works.md) — what the agent is maintaining.
- [The CLI](quickstart-cli.md) — what the agent is actually driving.
- [The web interface](quickstart-web.md) — for reviewing a graph by eye.
