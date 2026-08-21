---
description: Open the graph in a browser — both graphs, clickable, with the same staging the CLI uses
allowed-tools: Bash(dg serve:*)
---

!`dg serve --detach`

Give the user that URL. The page has three views — decisions, tasks, and the
two joined — and anything staged there lands in the same tray `dg pending` and
`dg task pending` read, so the browser and this session cannot disagree about
what is about to be written.

`dg serve --stop` when the work is done.

$ARGUMENTS
