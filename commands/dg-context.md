---
description: Why a decision or task is where it is — the whole chain of premises behind it
argument-hint: <D0n or T0n>
allowed-tools: Bash(dg context:*)
---

The reasoning behind $1:

!`dg context $1`

Each premise carries the answer it reached, the evidence that reached it, and
the falsifier that would overturn it. Anything marked as not settled is a
premise the conclusion above it does not really have yet.

Hand this to a subagent before dispatching work that rests on it — the chain is
precisely what a fresh context is missing.

$ARGUMENTS
