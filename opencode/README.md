# The decision graph in opencode

Same tool, same skill, same policy — a different host. opencode gets the two
mechanisms Claude Code gets: the brief arrives at the start of a session, and a
commit that would leave the graph contradicting itself is refused.

## Install

`dg` itself first:

```sh
pip install -e <this repo>
```

Then three symlinks, since opencode has no plugin marketplace:

```sh
repo=<this repo>
ln -s "$repo/skills/decisions"              ~/.config/opencode/skills/decisions
ln -s "$repo/opencode/decision-graph.ts"    ~/.config/opencode/plugins/decision-graph.ts
ln -s "$repo/opencode/commands/decisions.md" ~/.config/opencode/commands/decisions.md
```

Two things about those paths:

- The directories are **plural** — `plugins/`, `commands/`. The singular form
  loads nothing and says nothing about it.
- opencode also reads `~/.claude/skills/` and `.claude/skills/` directly, so if
  you already keep skills there, the skill symlink can point at one of those
  instead. It is the same file either way: the frontmatter is restricted to
  `name` and `description`, which is what both hosts accept.

Check it took:

```sh
opencode debug skill   | grep decisions      # the skill was found
opencode debug config  | grep decision-graph # the plugin and the command loaded
```

Project-scoped instead of user-scoped works for two of the three:
`.opencode/skills/decisions` and `.opencode/plugins/decision-graph.ts` are picked
up from the repo that has the graph, but on opencode 1.18 a command in
`.opencode/commands/` is not registered — `/decisions` needs the user-scoped
directory. The two mechanisms do not depend on it.

## What you get

| | |
|---|---|
| the brief | prepended to the first message of a session, and again after a compaction |
| `/decisions` | the brief on demand — works whether or not the injection hook does |
| the commit gate | `dg gate` judges every `bash` call that mentions `commit`; a refusal arrives as the tool's error, with the reason and the fix |
| the `decisions` skill | loaded by opencode's own `skill` tool when a decision is in play |

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
