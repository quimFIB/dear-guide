---
description: Write down how this project decides — asked one section at a time, saved as PROCEDURE.md for every session to read
allowed-tools: Bash(dg procedure:*), Bash(dg brief:*), Bash(dg areas:*), Read
---

The skeleton every procedure starts from:

!`dg procedure --template`

Help the owner write down this project's decision procedure. It belongs to
**them**: it says how they want decisions recorded and closed when they work
with a model. None of it is yours to decide.

**Before asking anything,** run `dg procedure`. If the project already has a
procedure, you are revising it, so ask what should change rather than going
through every section again. Read `dg brief` and `dg areas` as well, so your
questions can use this project's own records as examples rather than generic
ones.

**Then ask about the sections one at a time, in the skeleton's order.** For each:

- offer the choices the skeleton lists, with the dear-guide skill's default
  among them and marked as the default;
- take the owner's answer as given. Never ask them to justify it: it is their
  procedure, and choosing is the whole of their part. If they offer a reason
  unprompted, write it down with the rule;
- if the owner has no answer, leave the section out. The skill's default then
  applies, which is better than a rule invented to fill a heading.

Never fill in a section yourself, and never propose rules the owner did not
give. Offering examples is your part; stating rules is theirs.

**Show the whole draft before writing it.** Once the owner approves, write it to
the path `dg procedure` names (`PROCEDURE.md` beside the project's store) and
commit it with the project, like any other file that says how the project works.

The procedure applies when a person is in the session. Under a launch
(`$DG_AGENT` set), the launcher's policy applies instead and `dg brief` does not
name the file. So agentic rules do not belong in it; `dg-agent setup` handles
those.

$ARGUMENTS
