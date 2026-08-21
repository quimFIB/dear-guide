---
description: The backlog — what is outstanding, what is ready, and what each piece waits on
allowed-tools: Bash(dg task:*)
---

Outstanding work in this project:

!`dg task`

`waits` mixes both graphs: a `T` id is a prerequisite task, a `D` id marked
`(undecided)` is a decision whose answer this work depends on. Readiness
accounts for both, and `ready` names only what clears both.

`dg task --full` for untruncated titles; `dg task node <id>` for one piece in
detail, with its premise.

$ARGUMENTS
