---
description: Why a decision or task is where it is — the chain of premises behind it
argument-hint: <D0n or T0n> [--full]
allowed-tools: Bash(dg context:*)
---

!`dg context $ARGUMENTS`

`CHAIN` is the shape of the reasoning, oldest premise first. A trailing
exclamation mark on an id means that premise is not settled, and anything
resting on one is a bet rather than a conclusion.

Re-run with `--full` for each premise's answer, its evidence and the falsifier
that would overturn it — that is the form to hand a subagent, because the chain
is precisely what a fresh context is missing.
