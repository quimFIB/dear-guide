---
description: Open the graph in a browser — both graphs, clickable, with the same staging the CLI uses; `stop` closes it, `status` asks
argument-hint: [stop|status]
allowed-tools: Bash(dg serve:*)
---

!`dg serve --detach $ARGUMENTS`

With no word, that started the server (or found the one already running) and
printed its URL: give the user that URL. The page has three views — decisions,
tasks, and the two joined — and anything staged there lands in the same tray
`dg pending` and `dg task pending` read, so the browser and this session cannot
disagree about what is about to be written.

`/dg:serve stop` stops it — the word overrides `--detach`, and the server is
found by the record it left in the project and signalled only after it has
answered `/api/health` as ours, so a recycled pid is never touched. `/dg:serve
status` says whether one is running. Any other word is refused rather than
read as "start".
