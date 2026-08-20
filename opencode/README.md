# The development graph in opencode

Same tool, same skill, same policy — a different host. opencode gets the two
mechanisms Claude Code gets: the brief arrives at the start of a session, and a
commit that would leave the graph contradicting itself is refused.

## Install

`dg` itself first:

```sh
pip install -e /path/to/development-graph-assistant
```

Then symlinks, since opencode has no plugin marketplace:

```sh
repo=/path/to/development-graph-assistant
ln -s "$repo/skills/development-graph"      ~/.config/opencode/skills/development-graph
ln -s "$repo/opencode/development-graph.ts" ~/.config/opencode/plugins/development-graph.ts
for c in "$repo"/commands/*.md; do
  ln -s "$c" ~/.config/opencode/commands/"$(basename "$c")"
done
```

Two things about those paths:

- The directories are **plural** — `plugins/`, `commands/`. The singular form
  loads nothing and says nothing about it.
- **`commands/` is at the repo root, not under `opencode/`.** It used to hold
  one file for one host; it now holds the same five files Claude Code loads
  from the plugin root. One copy, two hosts — the arrangement the skill has
  always had.
- opencode also reads `~/.claude/skills/` and `.claude/skills/` directly, so if
  you already keep skills there, the skill symlink can point at one of those
  instead. It is the same file either way: the frontmatter is restricted to
  `name` and `description`, which is what both hosts accept.

Check it took:

```sh
opencode debug skill   | grep development-graph   # the skill was found
opencode debug config  | grep development-graph  # the plugin and the command loaded
```

Project-scoped instead of user-scoped works for two of the three:
`.opencode/skills/development-graph` and `.opencode/plugins/development-graph.ts`
are picked up from the repo that has the graph, but on opencode 1.18 a command
in `.opencode/commands/` is not registered — the commands need the user-scoped
directory. The two mechanisms do not depend on them.

## What you get

| | |
|---|---|
| the brief | prepended to the first message of a session, and again after a compaction |
| `/dg-brief` | the brief on demand — works whether or not the injection hook does |
| `/dg-frontier` · `/dg-tasks` | what is decidable now; the backlog and what is startable |
| `/dg-context <id>` | every premise a decision or a task rests on — what to read before dispatching work |
| `/dg-serve` | the graphs in a browser, started detached so the session keeps its prompt |
| the commit gate | `dg gate` judges every `bash` call that mentions `commit`; a refusal arrives as the tool's error, with the reason and the fix |
| the `development-graph` skill | loaded by opencode's own `skill` tool when a decision or a piece of work is in play |

`DG_HOOK_OFF=1` in the environment switches off both the brief and the gate. It
has to be in opencode's own environment, not in front of the command being run —
the plugin's environment is the host's.

## Two limits worth knowing

- **The gate does not see subagent shell calls.** `tool.execute.before` is not
  invoked for tools run by agents spawned through the `task` tool
  ([opencode#5894](https://github.com/sst/opencode/issues/5894)), so a commit
  made from inside one is not gated.
- **Injecting the brief is the one part not guaranteed by the API.** opencode has
  no session-start hook; the plugin edits the first user message instead. If a
  future version stops honouring that, `/decisions` still works, and
  `experimental.chat.system.transform` is the other candidate — the payload is
  the same string either way.
