# The development graph in opencode

Same tool, same skill, same policy — a different host. opencode gets the two
mechanisms Claude Code gets: the brief arrives at the start of a session, and a
commit that would leave the graph contradicting itself is refused.

## Install

`dg` itself first:

```sh
pip install -e /path/to/dear-guide
```

Then symlinks, since opencode has no plugin marketplace:

```sh
repo=/path/to/dear-guide
ln -s "$repo/skills/dear-guide"      ~/.config/opencode/skills/dear-guide
ln -s "$repo/opencode/dear-guide.ts" ~/.config/opencode/plugins/dear-guide.ts
for c in "$repo"/commands/*.md; do
  ln -s "$c" ~/.config/opencode/commands/"dg-$(basename "$c")"
done
```

Two things about those paths:

- The directories are **plural** — `plugins/`, `commands/`. The singular form
  loads nothing and says nothing about it.
- **`commands/` is at the repo root, not under `opencode/`.** It used to hold
  one file for one host; it now holds the same six files Claude Code loads
  from the plugin root. One copy, two hosts — the arrangement the skill has
  always had.
- **The link is named `dg-<file>`, not `<file>`.** Claude Code namespaces a
  plugin's commands under the plugin name, so `commands/brief.md` is `/dg:brief`
  there and needs no prefix in the file. Here the directory is flat and shared
  with every other tool installed into it — `/context` and `/tasks` are names
  something else has already taken — so the prefix goes on the link instead of
  in a second copy of the file.
- opencode also reads `~/.claude/skills/` and `.claude/skills/` directly, so if
  you already keep skills there, the skill symlink can point at one of those
  instead. It is the same file either way: the frontmatter is restricted to
  `name` and `description`, which is what both hosts accept.

Check it took:

```sh
opencode debug skill   | grep dear-guide   # the skill was found
opencode debug config  | grep dear-guide  # the plugin and the command loaded
```

Project-scoped instead of user-scoped works for two of the three:
`.opencode/skills/dear-guide` and `.opencode/plugins/dear-guide.ts`
are picked up from the repo that has the graph, but on opencode 1.18 a command
in `.opencode/commands/` is not registered — the commands need the user-scoped
directory. The two mechanisms do not depend on them.

## What you get

| | |
|---|---|
| the brief | prepended to the first message of a session, and again after a compaction |
| `/dg-brief` | the brief on demand — works whether or not the injection hook does |
| `/dg-frontier` · `/dg-tasks` | what is decidable now; the backlog and what is startable |
| `/dg-find <query>` | decisions and work by what they *say* — the only reading that starts from a word, and so the only answer to *was this already decided?* |
| `/dg-context <id>` | every premise a decision or a task rests on — what to read before dispatching work |
| `/dg-serve` | the graphs in a browser, started detached so the session keeps its prompt |
| the commit gate | `dg gate` judges every `bash` call carrying one of `dg gate --triggers`' words — `commit` and `rm` today; a refusal arrives as the tool's error, with the reason and the fix |
| the `dear-guide` skill | loaded by opencode's own `skill` tool when a decision or a piece of work is in play |

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
  future version stops honouring that, `/dg-brief` still works, and
  `experimental.chat.system.transform` is the other candidate — the payload is
  the same string either way.
