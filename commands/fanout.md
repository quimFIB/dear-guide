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
is the procedure, and two things in it are easy to get wrong by improvising.

**The rule.** Agents may `dg add` and `dg task add`; only the supervisor runs
`dg decide`. A fan-out is a search and the graph is a record: an agent adding an
OPEN question is cheap and reversible, and an agent deciding writes a falsifier
for a question nobody made it live with.

**Names come from the tool, and the name is the whole contract with the host.**
`DG_AGENT=$(dg agent claim)` per agent, never a name somebody invented — `claim`
refuses to hand out one that is held or that has ops in a tray, so two agents
cannot share one and conflate their work. Whatever spawns an agent only has to
put that variable in its environment, which is why a fan-out can mix Claude
Code, opencode and anything else that can run `dg`.

**Recording the run is OPTIONAL and usually not wanted.** A fan-out leaves
behind what it decided, which is what the graph is for. If this run needs to
become a demo, or somebody has to audit what was proposed rather than what was
taken, `agentic/bin/dg` first on `$PATH` records every call and both trays —
turn it on before the run, not during.

Review one proposal at a time: `dg pending --agent <name>` to read it, `dg serve`
to see it as a graph with staging applied, `dg apply --agent <name>` to take it,
`dg clear --agent <name>` to turn it down without touching the others.

$ARGUMENTS
