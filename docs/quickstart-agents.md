# Quick start: the agent plugin

A CLI only records what somebody remembers to run, and the thing that forgets is
an agent working across many sessions. The plugin turns three of the four habits
into mechanisms, for **Claude Code** and **opencode** alike.

|  | how |
|---|---|
| **read the frontier first** | `dg brief` is injected at the start of every session, and again after a compaction — no rule in an instructions file, no "read this first" |
| **know the discipline** | the `decisions` skill: the model, the rules, the flag-complete commands. Loaded on demand, not carried in every context |
| **refuse the contradictions** | a `git commit` that would leave the graph invalid is denied, quoting the rule that broke and the command that fixes it |

The interesting parts live in `dg` — `dg brief` and `dg gate` — so each adapter
is a few dozen lines of translation with no policy of its own.

## Install

Two steps, always. The hosts distribute plugins their own way and neither
installs Python packages.

Throughout, `/path/to/decision-graph-assistant` is your checkout of this
repository.

### 1. The CLI (both hosts)

```sh
pip install -e /path/to/decision-graph-assistant
dg --version        # so an adapter can tell when the two halves have drifted
```

### 2a. Claude Code

```
/plugin marketplace add /path/to/decision-graph-assistant
/plugin install decision-graph
```

### 2b. opencode

Three symlinks, since opencode has no marketplace:

```sh
repo=/path/to/decision-graph-assistant
ln -s "$repo/skills/decisions"               ~/.config/opencode/skills/decisions
ln -s "$repo/opencode/decision-graph.ts"     ~/.config/opencode/plugins/decision-graph.ts
ln -s "$repo/opencode/commands/decisions.md" ~/.config/opencode/commands/decisions.md
```

Two gotchas: the directories are **plural** (`plugins/`, `commands/` — the
singular form loads nothing and says nothing about it), and opencode also reads
`~/.claude/skills/` directly, so an existing skills directory works as the
symlink target instead. It is the same file either way.

Check it took:

```sh
opencode debug skill  | grep decisions       # the skill was found
opencode debug config | grep decision-graph  # the plugin and the command loaded
```

## What a session looks like

Open a session in a project with a `decisions.json` and the brief arrives before
you type anything:

```
DECISION GRAPH  /home/you/my-project  2 decisions: PROVISIONAL 1, REOPENED 1
Record what gets settled with `dg` -- see the decisions skill.

FRONTIER (1) -- not settled
  D01  REOPENED  Which corpus do we train on?  [Data]
       decidable now
       note: the 3B run contradicts it

RESTING ON A PREMISE UNDER REVIEW (1) -- PROVISIONAL, so not in the frontier
  D02  Tokenizer: BPE or unigram?  [Modelling]  rests on D01

STAGED BUT NOT APPLIED: 0
CHECK: clean
```

That is `dg brief`, verbatim — you can run it yourself any time, and on
opencode `/decisions` prints it on demand whether or not the injection hook
fired.

**In a directory with no `decisions.json`, nothing happens at all**: no output,
no error, no cost. A plugin is installed for a user, not for a project.

## The commit gate

Every `bash`/`Bash` call that mentions `commit` is handed to `dg gate`, which
answers `allow`, `ask` or `deny`. Three outcomes you will actually see:

**Clean graph** — silence, the command runs.

**Staged but unapplied work** → `ask`, because it is a judgement call about work
in progress:

```
2 decision op(s) are staged and not applied. .dgraph-pending.json is gitignored,
so committing now drops them from the record with no trace in the diff.
`dg apply` writes them; `dg clear` discards them.
```

**An invalid graph** → `deny`, quoting the rule and the remedy:

```
The decision graph is not valid, so this commit would record a contradiction:
  [stale_view] decision-graph.md does not match decisions.json. It is
  generated — run `dg render` rather than editing it.
Fix it first: `dg render` regenerates decision-graph.md; `dg check` names every
rule that broke.
```

There is also a case `dg check` cannot see, because it compares the worktree:
staging `decisions.json` without `decision-graph.md` produces a commit whose
store and view disagree. The gate denies that too.

The gate is scoped to the project it runs in — `git -C /elsewhere commit` into a
different repository is allowed, since it records nothing about this graph.

## The skill

`skills/decisions/SKILL.md` is one file both hosts read — the model, the rules,
the command table, the staging workflow. It loads on demand when a decision is
in play, rather than sitting in every context. A test asserts the skill's
command table only names commands that actually exist, and that neither adapter
names any `dg` subcommand but `brief` and `gate`.

Ask the agent to record a decision and it will reach for the skill, then run
something like:

```sh
dg decide D37 \
  --answer "32k BPE, from the sweep in report/tokenizer-sweep.md" \
  --source "report/tokenizer-sweep.md" \
  --falsifier "held-out perplexity gets worse when the corpus grows" \
  --opens "D40,D41"
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
  message. If a future version stops honouring that, `/decisions` still works.
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

- [The CLI](quickstart-cli.md) — what the agent is actually driving.
- [The web interface](quickstart-web.md) — for reviewing a graph by eye.
