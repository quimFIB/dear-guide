# The development graph in opencode

Same tool, same skill, same policy — a different host. opencode gets the two
mechanisms Claude Code gets: the brief arrives at the start of a session, and a
commit that would leave the graph contradicting itself is refused.

## Install

`dg` itself first:

```sh
pip install -e /path/to/dear-guide
```

(`'/path/to/dear-guide[tui]'` adds a full-screen `dg agent setup`; optional,
and the command asks the same questions without it.)

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
| `/dg-fanout` | who holds a name, what each of them is holding, and what is staged — before running several agents against one graph |
| the agent write scope | when `$DG_AGENT` is set, `dg gate --write` judges every `write` and `edit` call. Out of scope throws, carrying the reason and saying whose call it is; in scope and unowned sessions cost nothing. Reads are never judged — `read` carries a `filePath` too and is deliberately absent. The policy is `$DG_WRITE`, shared with Claude Code; see `agentic/README.md`, and the fourth limit below for `patch` |
| the commit gate | `dg gate` judges every `bash` call carrying one of `dg gate --triggers`' words — `commit` and `rm` today. It answers four ways: `deny` and `ask` both stop the call and arrive as the tool's error, carrying the reason and the fix, with `ask` saying whose call it is; `warn` stops nothing and is the third limit below; `allow` says nothing |
| the `dear-guide` skill | loaded by opencode's own `skill` tool when a decision or a piece of work is in play |

`DG_HOOK_OFF=1` in the environment switches off both the brief and the gate. It
has to be in opencode's own environment, not in front of the command being run —
the plugin's environment is the host's.

## Five limits worth knowing

- **The gate does not see subagent tool calls.** `tool.execute.before` is not
  invoked for tools run by agents spawned through the `task` tool
  ([opencode#5894](https://github.com/sst/opencode/issues/5894)), so a commit
  made from inside one is not gated — and neither is a write, which matters
  more now that the write scope rides the same hook. A fan-out launched as
  separate `opencode run` processes is unaffected; one launched through the
  `task` tool is not scoped at all. Claude Code has no equivalent gap, so this
  is a reason to prefer separate processes here.
- **The write scope does not cover the `patch` tool.** `write` and `edit` name
  their target in `filePath`, which is what `dg gate --write` judges. `patch`
  takes `patchText` — one diff that may touch several files — so there is no
  single path to hand the gate, and covering it would mean parsing a diff for
  its targets inside an adapter whose whole rule is that it holds no policy. A
  half-parser that failed open would be worse than a stated gap, so this is
  stated. Claude Code has the same shape of hole for a shell redirection, which
  `hooks/prewrite.py` records for the same reason.

  The tool names and their argument were **read out of opencode 1.18.25**
  rather than assumed, and `tests/test_opencode.py` pins the pair: a rename in
  a future opencode would otherwise stop the scope applying while looking
  exactly like it still did.

  The rest of the scope was **run end to end against opencode 1.18.25**, not
  only typechecked. An out-of-scope `write` is refused with the gate's reason
  reaching the model, which asked the person rather than retrying — which is
  what `ask` degrading to a throw is supposed to buy. An in-scope `write`
  passes with nothing said.

- **opencode has its own opinion about reads, and it is not this one.** The
  scope never judges a read, but opencode's own permission layer refuses a
  `read` outside the project — `permission requested: external_directory
  (/etc/*); auto-rejecting` — before the plugin is consulted. So "reading is
  never restricted" is a statement about *this tool*, and an agent under
  opencode may still be stopped from reading somewhere by the host. Claude Code
  has its own equivalent. Worth knowing when a scout is told to read something
  outside its project and reports that it cannot.

- **Injecting the brief is the one part not guaranteed by the API.** opencode has
  no session-start hook; the plugin edits the first user message instead. If a
  future version stops honouring that, `/dg-brief` still works, and
  `experimental.chat.system.transform` is the other candidate — the payload is
  the same string either way.
- **The `warn` verdict may not reach you, and this has not been checked.** A
  generated view that has fallen behind its store is worth *mentioning* at the
  moment a commit records it and is not worth refusing over — so the adapter
  writes it with `console.error` rather than throwing, since throwing would
  block the commit, which is the one thing `warn` exists not to do. Whether a
  plugin's console output reaches the session or only
  `~/.local/share/opencode/log/` is not something anybody has established. The
  same notice covers a `dg gate` that exited non-zero, meaning the command ran
  unchecked.

  Nothing is lost either way: `dg check` reports a lagging view on demand and
  `dg render` rebuilds it. Both
  refusing verdicts are thrown and so arrive by construction; it is only the
  two advisories that ride this channel. If the answer turns out to be the log,
  `client.tui.showToast` is the supported one — `PluginInput` hands the plugin
  an `OpencodeClient` for exactly this.
