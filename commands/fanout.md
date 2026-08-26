---
description: Set up a recorded fan-out — several agents proposing into one graph, one person deciding
allowed-tools: Bash(dg agent:*), Bash(dg pending:*), Bash(command -v dg), Read
---

The graph as somewhere several agents propose and one person decides. Who holds
a name right now:

!`dg agent list`

...and what is staged, across both trays:

!`dg pending`

Read `agentic/README.md` in the dear-guide checkout before setting one up — it
is the procedure, and three things in it are easy to get wrong by improvising.

**The rule.** Agents may `dg add` and `dg task add`; only the supervisor runs
`dg decide`. A fan-out is a search and the graph is a record: an agent adding an
OPEN question is cheap and reversible, and an agent deciding writes a falsifier
for a question nobody made it live with.

**Names come from the tool.** `DG_AGENT=$(dg agent claim)` per agent, never a
name somebody invented — `claim` refuses to hand out one that is held or that
has ops in a tray, so two agents cannot share one and conflate their work.

**The capture is opt-in and has to be set up first.** `agentic/bin/dg` on
`$PATH` records every call and both trays, which is the only way the proposals
that were turned down survive at all — the graph keeps what landed and
deliberately not what was proposed. Smoke-test it before the run, not during.

Review one proposal at a time: `dg pending --agent <name>` to read it, `dg serve`
to see it as a graph with staging applied, `dg apply --agent <name>` to take it,
`dg clear --agent <name>` to turn it down without touching the others.

$ARGUMENTS
