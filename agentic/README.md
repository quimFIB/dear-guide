# Running a fan-out against the graph

Several agents proposing into one graph, and a person deciding.

The graph already supports it: the tray is shared, every staged op records who
staged it, `dg-agent claim` hands out names that cannot collide, and `--agent`
reads and applies one writer's proposal at a time. What was left to improvise
was the procedure around those, which is what this is.

```
agentic/QUICKSTART.md three recipes and what to check — start there to *run* one
agentic/RUNNING.md   the procedure, step by step
agentic/README.md    what each rule is for — sections 1 to 6 below
agentic/bin/dg       OPTIONAL: a capture, for when you want the run itself
agentic/bin/dg-agent afterwards. Two wrappers because there are two binaries,
                     and most of what a capture of a fan-out is for — the
                     claims, the setup, the parks — goes through the second.
                     Nothing in the procedure needs either.
```

The capture exists because one run needed to become a demo. It is genuinely
useful for that and for auditing what a set of agents proposed, and it is
genuinely not part of the workflow — a fan-out leaves behind exactly what it
decided, which is what the graph is for.

---

## The rule the whole thing rests on

> **Agents may `dg add` and `dg task add`. By default, only the supervisor runs
> `dg decide` — and `$DG_DECIDE` can make that a rule instead of a habit.**

A fan-out is a *search*: agents proposing questions, work, and structure. The
graph is a *record*. Keeping them apart is what stops one turning into the other.

An agent adding an OPEN decision says "here is a question somebody has to
answer" — cheap, reversible, exactly what fan-out is good at. An agent running
`dg decide` writes a falsifier for a question nobody made it live with, and the
skill's own warning applies: *a manufactured decision that nobody actually had
to make is worse than no record.* A decided edge is also the expensive one to
retract — `dg undep` works only on a bare edge, so reversing a hasty answer
means a `reopen` that files a reversal nobody made.

The tray is what makes the search safe: nothing an agent stages exists until
somebody applies it. `dg show`, `dg task` and `dg brief` read the store **with
the tray previewed**, so the frontier a second agent sees already accounts for
what the first has claimed. `dg serve` shows the two apart — the graph is the
store as it stands, and the footer is every staged op with the writer who
proposed it.

**That split is deliberate, and the reason is the canvas.** A terminal reading
reprints from scratch every time, so folding the tray into it costs nothing. A
panel holds a pan, a zoom and an open inspector, and a node appearing under a
merge moves all three while somebody is reading one of them — so the browser
puts the proposals where they can be read *as* proposals, beside the record
rather than inside it, which is also what lets it name the writer. The
`↻ refresh` button says when either has moved and redraws only when clicked,
for the same reason.

What follows from it: **a reading taken in the browser is of the record, not of
the record plus what is proposed.** To see a proposal as a graph, take it —
`dg apply --agent <name>` — or read it in the footer and in `dg pending`.

**And one thing the panel is not: covered by the floor.** A confinement floor
mounts `decisions.json` and `tasks.json` read-only, so an agent under one
cannot write them whatever its `$DG_APPLY` says — that is the tool's single
non-cooperative boundary. `dg serve` is a separate process outside that floor,
it writes both stores, and its token is served to anything that asks for the
page. So a panel left open beside a confined run is a writer the floor does not
cover. Nothing here stops that, deliberately: it is recorded rather than fixed,
like the relay's own exposure in `D40`. If a run needs the floor's guarantee to
be unconditional, do not leave a panel open beside it.

### ...and the real reason, which is narrower than "agents judge badly"

The graph has two exits from a decision and both are wrong for a premature one.
`dg reopen` files a reversal — but a reversal means *we changed our mind*, not
*that should not have been written*, and the skill calls a reversal that never
happened a lie in the record. `dg rm` erases, is explicitly for things that
should never have been written, and `dg gate` answers `ask` on it, so a person
decides anyway. There is no vocabulary for "an agent decided this too early".

Which means the restraint that is right depends on the DECISION, not on who is
asking:

- a falsifier that is **a measurement the agent made** — the benchmark ran, the
  number is 0.62 — is a fact being recorded, and the falsifier writes itself;
- a **judgement between defensible alternatives** is where a falsifier written
  by something that never had to live with the consequence comes out as
  rationalisation.

Only the first is mechanically recognisable, which is what `$DG_DECIDE` checks:

| `$DG_DECIDE` | an agent may close… |
|---|---|
| `open` *(default)* | anything — what the tool has always done |
| `evidence` | only a decision a **finished** `--evidence-for` task backs |
| `never` | nothing — the close is refused before it is composed, and a caller with no `$DG_AGENT` writes it |

A supervisor — anyone with no `$DG_AGENT` — is never refused by any value. And
it is cooperative, like `$DG_AGENT` itself: an agent could unset it, at which
point it *is* the supervisor. Nothing here is a security boundary; it is a rule
the launcher sets so an honest mistake is caught.

---

## The other four limits: where an agent may write, for how long, how much, and under what name

`$DG_DECIDE` limits what an agent may *record*. Four more variables limit what
it may do to the machine, to the clock, to the reader and to the vocabulary,
and all five are read the same way — declared by the launcher, never consulted
for a supervisor.

| variable | values | what it does |
|---|---|---|
| `$DG_WRITE` | `open` *(default)* · `launch` | where the agent may write without asking |
| `$DG_BUDGET` | `infinite` *(default)* · `1800` · `30m` · `2h` | how long before its work is handed back |
| `$DG_TERSE` | `off` *(default)* · `on` · `400` | how long a field may be before the development belongs in a file |
| `$DG_AREA` | `open` *(default)* · `strict` | whether it may file under an area nobody has used yet |
| — | `dg-agent env` | **what is actually in force**, and what was mistyped |

```sh
dg-agent run --decide evidence --write launch --terse on --budget 30m \
  -- claude -p "$(cat fanout/scout.md)"
```

`dg-agent run` composes that environment and hands it to a child. It is not a
convenience over the assignments it replaced: it **validates every value before
it spawns anything**, where the variables themselves fail open, and it is the
child's parent, which is what makes the budget real.

### The three curated remits, for when that is five questions too many

Every variable above is worth setting deliberately, and none of them can be
answered without a section of this page behind it. So `dg-agent setup` asks one
question first, and a preset fills the whole block:

| preset | what it settles | `$DG_DECIDE` | `$DG_APPLY` | may run unasked |
|---|---|---|---|---|
| `scout` | proposes only · nothing lands unapproved | `never` | `never` | `dg`, `dg-agent`, and the readers |
| `contributor` *(default)* | settles what evidence backs | `evidence` | `own` | the above · the project's build tools |
| `maintainer` | settles anything | `open` | `own` | the above · the project's build tools |

The same in all three: `$DG_WRITE=launch`, `$DG_AREA=open`, `$DG_TERSE=on`, and
a confinement floor wherever a backend is available. None of them sets a budget
— that follows the size of the work rather than the remit, and the wizard asks
it either way.

```sh
dg-agent presets                        # this table, from the code
dg-agent setup --preset scout           # or pick one in the wizard
dg-agent setup --preset scout --decide open   # the remit, except for this
```

**`$DG_APPLY` is the one that makes `scout` mean what it says.** `$DG_DECIDE`
guards what an agent may *answer* and guards nothing about what it may *add* —
an `add` is ungated at both ends, and `dg apply` writes an owned caller's own
ops with nothing consulted. So the tray keeps writers apart; it is not, by
itself, somebody approving them. Under `$DG_APPLY=never` an agent stages and is
refused the write, the ops stay exactly where they are, and a caller with no
`$DG_AGENT` applies them — which is what turns the tray into an approval queue.

That default is right where a person aimed the fan-out at a frontier they
chose: an OPEN question is cheap and reversible, and an agent that cannot read
its own proposal back as a graph is working half-blind. It is the wrong default
where **agents discover graphs on their own**, because then nobody chose the
target and the volume is not bounded by a brief. `scout` is the preset for
that, and it now delivers what its name always implied.

Enforceable for exactly the reason `$DG_DECIDE` is, and the argument is worth
repeating rather than referring to: **every apply goes through `dg`**. Both
doors take it — the CLI and the browser's apply endpoint — because a rule that
held at one and not the other is the drift the shared helpers in `pending`
exist to stop. A caller with no `$DG_AGENT` is never refused, since that caller
is the supervisor the ops are being held for.

**Three fields vary, and that is the honest count.** A preset is not worth having
because it moves seven knobs; it is worth having because one answer settles all
seven, five of them by holding a constant that otherwise has to be derived from
this page before anybody can start. Three of those constants are argued above —
`$DG_AREA=open` because a scout naming a corner nobody had found is a finding,
`$DG_TERSE=on` because the limit is about whoever reads the panel rather than
about the agent, and the floor because "I trust their judgement" and "I want a
shell redirection to escape the gate" are different claims that must not share
a word.

**`$DG_WRITE=launch` in all three**, and that one is not a preference. A
confinement floor seals `limits.writable_roots`, which never reads `$DG_WRITE`
— so `open` under a floor stops the *gate* asking about a write outside the
project while the kernel goes on refusing it, and the agent gets a bare
permission error carrying none of the prose the refusal exists to attach.
`dg-agent setup` now refuses that pair outright, beside the floor-under-the-
wrong-runner pair it already refused.

**A preset expands and is never stored.** `fanout/env.json` goes on holding the
resolved values and no preset name, for the same reason it holds no focus ids:
a name recorded beside the values it produced is free to disagree with them the
moment somebody edits one, and that file exists to close exactly that gap. The
wizard reads the name back off the values instead, and shows nothing where a
plan has been edited away from all three.

**The card carries its row wherever it appears.** A card reading `maintainer`
that quietly widened `$DG_DECIDE` would be the `DG_DECIDE=nevr` failure of the
next section wearing a friendlier name — a rule moved toward more permission by
something nobody could read. The wizard's cards, `dg-agent presets` and the
table above all render from `fanout.PRESETS`, and a test asserts this page still
matches it.

## The broker, and how it differs from the supervisor

Two halves of the person's side, and confusing them is easy, so:

|  | supervisor | broker |
|---|---|---|
| what it is | an identity — `$DG_AGENT` unset | a process listening on a socket |
| when it acts | afterwards, at your leisure | during the run, with an agent stopped |
| governs | the **record** — what enters the stores | the **machine** — writes and commands |
| acts by | `dg apply --agent <name>` | `allow` / `deny`, one at a time |
| if absent | ops wait in the tray; nothing is lost | escalations become refusals nobody chose |
| touches a tray | yes, that is its whole job | never; it writes only its own log |

**You do not start a supervisor.** `pending.owner()` answers `None` when
`$DG_AGENT` is unset, and `None` *is* the supervisor — so the terminal you ran
`launch.sh` from is one, and so is a Claude Code or opencode session opened in
the project afterwards. `dg-agent run` puts each name in its **child's**
environment and never exports it, precisely so the launcher does not become an
agent.

**You do start a broker**, in its own terminal, beside the run:

```sh
dg-agent broker
```

The difference that forces them apart is timing. A tray **waits** — a proposal
read tomorrow is a proposal read. A consent request **cannot**: an agent is
halted mid-run with a deadline, and `dg gate` is a pure function that can only
answer `ask`. With no broker listening the gate returns the verdict it always
did, so a project that never starts one behaves exactly as it did; in a
headless run that verdict is a refusal.

### Answering from a session, without becoming the one who decided

The terminal front end cannot serve a supervisor who is a Claude Code or
opencode session rather than a tty. So the front end is swappable — it always
was, `Broker.prompt` — and `--relay` fills it:

```sh
dg-agent broker --relay      # publishes each request instead of prompting
dg-agent consent             # ...what is waiting, and what answering means
dg-agent consent --allow --why "it is the test suite"
```

The broker publishes the pending request, `dg-agent consent` writes a verdict,
and the blocked agent moves. Everything else is the same broker: the same
rungs, the same memory of grants, the same log, the same one-at-a-time
answering.

**The relay is transport and never the decider**, and the log is where that
claim is kept honest. Every verdict now records **what produced it** —
`person`, `auto`, `grant`, `broker`, or `unanswered` — beside what it was. A
relayed answer records `person`, because a person answered through it; a
*model* answering there would write `person` for something no person decided,
and put a lie in the one artefact a supervisor reads afterwards.

### ...and letting the session answer, on the rung that means that

A model answering consent is legitimate. What it may not be is called `user` —
that word means a person, the rungs are read once at broker start out of an
agent's reach, and a model answering under it would write `person` for
something no person decided. So it answers on the rung that already existed for
deciding here rather than at a person:

```sh
dg-agent broker --relay --exec-rung auto     # commands: the session decides
                                             # writes: still `scoped`, still you
```

**Nothing else changes.** Same published request, same `dg-agent consent`, same
wait. The rung decides one thing only — what the answer is *called* — and that
is the entire difference between the two arrangements:

| rung | who answers | logged as | `dg-agent consent` says |
|---|---|---|---|
| `user` · `scoped` | a person, however they reach it | `person` | recorded as a person's; it must be one |
| `auto` | whatever is attached | `auto` | recorded as `auto`, not as a person |

Both the broker's banner and `dg-agent consent` name the rung before anything
is answered, so nobody signs as a person by accident.

**Set them per ladder, which is the point of there being two.** `--exec-rung
auto --write-rung scoped` is the useful shape: commands are frequent, mostly
dull, and mostly judged against the allowlist and the floor anyway, while a
write outside the launch scope is rare and worth a person. The reverse would be
strange, and nothing stops you writing it.

**And where nothing protects the channel, the label says less.** A relayed
verdict is logged `person` under a confinement floor and `relayed` without one:
the socket is a filesystem object an unconfined agent shares a uid with, so the
run cannot show which hand wrote the answer. The verdict stands either way and
relaying is not refused — `D40` weighed that and declined, since an agent with
no floor can already write the project at leisure. What it may not do is claim a
warrant it has not got. `dg-agent broker` says which you are getting, once, at
the door.

**The tool guarantees the label, not the judgement.** `relay_auto` carries the
question out and the answer back; whether the thing answering decides well is a
question about that thing. What cannot happen is a supervisor reading the log
afterwards and being unable to tell which requests a person actually saw —
which is what `D37` was for, and what `$DG_CONSENT_*=auto` was already for
before anything could be attached to it.



**The channel is not in the project, and that is the whole of its safety.**
The first version of this put the pending question and its answer beside the
socket — where every agent may write freely under `$DG_WRITE=launch`, and where
a confinement floor seals nothing, since it protects only the two stores. An
agent could therefore write its own `allow`, the blocked agent collected it, and
the log recorded `by: person` with whatever reason the forgery supplied: the
exact lie `answered_by` exists to prevent, arriving through the channel meant to
carry the truth. The rule it now meets is the one the memory-only grants already
met:

> **The consent channel lives where an agent cannot write without already
> holding the permission it is asking for.**

So it lives under `$XDG_RUNTIME_DIR`, per project, `0700` — and reaching it now
requires an out-of-scope write, which requires consent. `dg-agent broker
--relay` refuses to start if that path would land inside a writable root, a
third door check beside `unbindable` and `unattachable`.

**And it is a connection, not a pair of files.** Files were *passively*
forgeable: write the answer, walk away, and the blocked agent collects it. The
question is handed out and the verdict taken back on one connection to a socket
beside the channel, so between them there is no artifact anywhere — forging one
means winning a race against the legitimate answerer. It took the stale-answer
matching, the `expires` stamp and the phantom-question handling with it, none of
which a connection needs.

**What none of that is, is a boundary.** The socket is a filesystem object and
the agent runs as the same user, so it can `connect()` like anything else; what
actually stops it is the confinement floor, which puts every path outside the
project beyond reach. So the guarantee holds exactly while a floor is in force,
and `--relay` does **not** currently check that — a deliberate, recorded risk
(`D40`, open), taken because racing a socket is a great deal harder than writing
a file at leisure. If you relay without a floor, know that is what you are
relying on.

This matters more than the tool's other rules, and the difference is worth being
plain about. `$DG_AGENT`, `$DG_DECIDE` and `$DG_APPLY` are cooperative by
design — an agent could unset any of them, at which point it is the supervisor,
and nothing here pretends otherwise. The **confinement floor is not**
cooperative; it is the one kernel-enforced boundary in the tool, and the broker
is its only door. A door whose lock is a file inside the room is not a door.

**Answer inside the caller's deadline.** Both adapters give the gate 100
seconds and it answers `deny` before that elapses, so a verdict written two
minutes later is one the agent never receives — the log says
`delivered: false` rather than pretending otherwise. A relay that nobody
answers gives up a little after that and denies, recorded as `unanswered`: an
unreachable decider is not consent, which is the rule `consult` already follows
from the other side.

### `dg-agent env`, and why three of these fail open

`$DG_DECIDE`, `$DG_WRITE`, `$DG_TERSE` and `$DG_AREA` all answer *the widest
setting* to a value they cannot read. `DG_DECIDE=nevr` is `open`, and every
scout may then close any question it likes:

> **A typo does not weaken a rule by a notch. It removes it, silently, in the
> direction of more permission.**

Failing open is nonetheless right, and the reason is the tray. These are read
on the path of every stage, every close and every judged write — including the
supervisor's, who shares that tray — so a launcher's typo must not take the
graph away from the person reviewing the run. What makes it defensible is that
something *reports* it:

```
$ dg-agent env
  variable    set to  effective  read as
  DG_AGENT    —       —          supervisor — no refusal applies
  DG_DECIDE   nevr    ✗ open     may close any question
  DG_BUDGET   30m     30m        19m left on this lease

✗ $DG_DECIDE=nevr is not one of open, evidence, never — running as `open`,
  the widest. Set it where the agent is launched.
```

Three things `env | grep DG_` cannot do: **name a fallback as a fallback**,
which is the whole defect; show the **budget against the lease** rather than
against the variable, since the lease is what the hand-back reads; and resolve
`$DG_PROJECT` to the graph it actually **found**, which is how a stale
environment file ran a whole fan-out against no store while looking correct.

`dg-agent env --check` exits non-zero if anything set was not understood, and
`fanout/launch.sh` runs it before the first agent starts. Only *set and not
understood* is a finding — unset is the documented default for every one of
them.

`$DG_BUDGET` is the exception that raises rather than widening, and the
asymmetry is argued rather than accidental: a misread budget is not a wider
rule, it is a different number, and both directions are wrong in a way nobody
notices until an agent is parked hours early or never.

### Why this is not enforced here, and where it is

`$DG_DECIDE` can be a rule because **every decision goes through `dg`**. A write
does not — an agent writes with its host's own tools, and `dg` is not in that
path at all. A check that lived only in this tool would be a rule nothing ever
consulted.

The enforcement point that does exist is **`dg gate`**, and it was already
general: it takes a thing about to happen and answers `allow` / `warn` / `ask` /
`deny` with a reason, and *both host adapters relay that answer holding no
policy of their own*. So the scope is a second question the same gate answers:

```sh
dg gate --write /etc/passwd --json
```

Which means a rule written once is enforced under **every** host at once —
`hooks/prewrite.py` under Claude Code, `tool.execute.before` under opencode, and
a third scaffold earns it by relaying the same verdict. `tests/test_plugin.py`
asserts both adapters ask.

What an adapter still owns is *which of its host's tools writes*, and each has
one tool it cannot judge: a shell redirection under Claude Code (covering it
means parsing arbitrary shell for write intent) and `patch` under opencode
(which names its targets inside a diff rather than in an argument). Both are
stated in the adapter and in `opencode/README.md` rather than papered over — a
half-parser that failed open would be worse than a gap somebody knows about.

**Reads are never judged.** An agent that cannot read the repository it is
reasoning about is blindfolded rather than constrained, and every interesting
thing a fan-out does starts by reading something outside its own directory.

That is a statement about *this tool*, and the host may still have its own
opinion: opencode refuses a read outside the project on its own account, before
the plugin is consulted. If a scout reports it cannot read something you told it
to read, that is where to look first.

**An out-of-scope write is `ask`, never `deny`.** The rule is consent, not
prohibition: the person approves it where they are standing. `dg gate --write`
has no verdict that refuses outright.

Under `launch` the writable roots are **the project** — where the graph is — and
the system temporary directory. The project rather than the agent's current
directory, because a `cd` is not a change of remit and a scope anchored to the
working directory would widen every time an agent walked somewhere else.

### The budget stops the child, and hands its work back

**This changed with the split, and the change is worth being precise about.**
`dg` is not in the agent's process tree and never was — under `dg`, stopping was
the launcher's half and looked like `timeout 1800 …`, two numbers saying one
thing in a generated line people are expected to edit.

`dg-agent run` **is** the child's parent. So the budget is the timeout, there is
only one number, and a child stopped at it — or one that dies holding work — has
whatever it holds parked immediately, under its own name, when the information
is freshest. A child that exits clean has nothing parked: a park filed over a
finished session records a stop that never happened. The name is not released
either, because an agent that staged a proposal is holding something a person
has to read.

**What that does not cover, said plainly.** It covers the child timing out and
the child crashing. It does **not** cover `dg-agent run` itself being killed — a
`kill -9` on the process group, the machine going down, the terminal closing.
`dg-agent expire` is therefore still exactly what it was, and is still the step
the procedure tells you to run at the end. The window is narrower; nothing
closes it, and a fan-out that claimed otherwise would be claiming the supervisor
is no longer needed.

What the hand-back buys is the thing a fan-out cannot otherwise see. A task left
`DOING` by an agent that died reads exactly like one being worked on — and
`dg task park --why` is the verb that already fixes it:

```sh
dg-agent list          # who is over, and what they are still holding
dg-agent expire        # stage a park for each, naming the budget
dg apply --agent brisk-beacon
```

```
┃ Name          ┃ Staged ┃ Holding ┃ Budget         ┃ Seen       ┃
│ agile-azimuth │ 0      │ T07     │ SPENT +12m     │ silent 44m │
│ brisk-beacon  │ 4      │ T11     │ 30m (18m left) │ 20s        │
```

Each park is staged **under the agent's own name**, so it lands beside whatever
that agent had already proposed and `dg apply --agent <name>` takes the batch.
Parked work is still outstanding, so nothing downstream is released and the next
agent can pick it up — which is the whole point of parking rather than dropping.

An agent that spent its budget holding *nothing* is reported too. "Died before it
started" is invisible in every other reading of a run, because no task is `DOING`
for a roster of parked work to show.

Run `dg-agent expire` even when an agent died on its own; the budget is what
makes the queue honest afterwards, and this is a real case rather than a
hypothetical — a rate limit killed three scouts mid-wave in the run that
`.dgraph-capture/` was built from, and every one of their tasks had to be parked
by hand.

### `Seen`, and why nothing acts on it

A budget catches an agent that ran *out of time*. It does nothing for one killed
at minute five of thirty — a quota limit, a crash — which stays invisible for
the next twenty-five minutes while its row reads perfectly healthy. So every
`dg` call by an agent stamps a heartbeat, and `Seen` is how long ago that was.

The signal is better than "how often does an agent run `dg`" suggests, because
`dg` is not the only door: both host adapters call `dg gate` on the agent's own
tool calls, so **every file write is a heartbeat too**. For a scout that reads,
thinks, writes findings and records them, coverage is good.

**Nothing acts on it, and that is the design.** An elapsed budget is a fact
about a clock. Silence is a suspicion — an agent in a forty-minute build is
silent in exactly the way a dead one is, and no amount of tuning fixes that,
because the blind spot is precisely long non-`dg`, non-write work. So silence
gets a column and never a verb: `expire` fires on elapsed budgets only, and a
person decides what a quiet agent means.

Three things keep it honest:

- the window is deliberately generous — 15 minutes, and `$DG_SILENT_AFTER`
  raises it for a fan-out doing long compiles;
- it is reported **only for an agent holding work**, since one that is silent
  and holding nothing has cost nobody anything;
- and if you do act, `expire` still only *stages*, so a park on a live agent is
  reviewed before it lands and `dg clear --agent` throws it away.

The remaining blind spot is worth telling agents about rather than engineering
around: an agent that knows it is about to go quiet for an hour can say so, and
`dg apply --mine` before a long sweep is a heartbeat like any other.

### The synopsis, and why the graph is the thing doing the synthesising

The complaint that produced `$DG_TERSE` was not about agents being wrong. It was
that a fan-out fills the graph with *prose*, and the person who then has to
decide something is reading a wall of it in a panel where they came to compare
three proposals.

**The store holds the synopsis. The development goes in a file.** A record's
fields — answer, falsifier, note, outcome, the reason work stopped — are read
while deciding, so they are one or two sentences. Everything longer goes in a
file the record already has a way to name: `--source` on a decision, the
`--outcome` on a task. There is deliberately **no new field** for it; a second
way to name a file is a second thing that can disagree with the first.

And the sharper half: most of what an agent writes into a long field should not
be anywhere, because *the graph already holds it as structure*. The premises a
decision rests on, the questions it opens, the work resting on it, the evidence
brought against it — every one of those is an edge, and `dg context <id>`
computes the chain from them on demand, for anybody, six months later. An answer
that also narrates its premises is a second copy of the graph in the one place
nothing can check it against the first. The fix is an edge, not a paragraph:

```sh
dg dep D07 --after D03                 # this rests on that
dg task link T04 --evidence-for D09    # this work bears on that question
```

| `$DG_TERSE` | what an agent may write |
|---|---|
| `off` *(default)* | anything — what the tool has always done |
| `on` | 400 characters a field, refused at stage time |
| `<n>` | `n` characters a field |

Judged at `pending.stage_all` — the one door both trays and every command pass
through — so it reaches `dg decide`, `dg task done`, `dg task park` and the
browser's API alike. It is the **only** stage-time guard in this tool that
cannot refuse before composition, because it judges the prose and there is none
until it is written; the tray is still left untouched, which is what those
guards were actually protecting. A supervisor is never refused.

Two things it deliberately does not judge. A **title** — it is what every reader
refers to the record by, so "put it in a file" is advice nobody can take, and a
long title wants rewriting rather than a rule. And a **`--source`**, which is
the citation the refusal asks for.

**The other half is the browser, and it needs no policy.** `dg serve` folds any
field past 400 characters behind *show all*. That reaches what `$DG_TERSE`
cannot: a graph written before the rule existed, an imported one, and every
record a person wrote by hand. `dg check` reports `verbose_field` above the same
400 — a warning, never blocking, because a long answer is a legal record and a
graph must not become uncommittable over prose style.

**Those two read 400 and never `$DG_TERSE`, deliberately.** The variable is a
launcher's rule for its own agents; the check and the fold are read by a
supervisor, who never has it set, and a warning that went quiet exactly where
nobody had configured anything would be silent in every project it is for.

The consequence is worth knowing before you set a custom count: at `on` and at
`off` all three numbers agree, and at `DG_TERSE=800` they do not. An agent may
then stage a 700-character answer that `dg check` warns about the moment it
lands, in a panel that folds it at 400. Nothing breaks — the warning is not
blocking and the fold is not a limit — but the three are saying different
things, so prefer `on` unless you have a reason.

---

## 1. The graph, and what you already decided

```sh
dg init          # if there is not one
dg task init
```

Then **seed it from the prose you already have.** Every project has decisions
recorded somewhere that is not a graph — a design note, a comment block at the
top of a config, a paragraph in a README. Lift those first. A fan-out against a
blank graph rediscovers them badly and expensively; against a seeded one it
works on what is actually open.

The test for whether something belongs is the skill's: can you write a falsifier
for it? Then it is a decision. A definition of done? Then it is a task.

```sh
dg add --id D01 --title "…" --area …
dg decide D01 --answer "…" --source "path/to/the/prose.md" --falsifier "…"
dg apply
```

Where the prose records a decision that was already *reversed* once, record it
as a reversal rather than as a fresh answer. Those are the most valuable thing
the graph holds and the easiest to flatten by accident.

## 2. The goal, as a task

Whatever the fan-out is for, it is work with a definition of done, so it is a
task and not a decision.

```sh
dg task add --id T01 --title "…" --area …
```

> **Your acceptance criteria go here, and they have to be citable.** Every
> closed decision cites a `--source`, and `discussion` is the weakest one
> available. If the standard the outcome must meet lives in your head or in a
> chat, writing it into the repo is the first prerequisite — not paperwork
> around the work:
>
> ```sh
> dg task add --id T00 --title "Write the spec into the repo" --area …
> dg task dep T01 --after T00
> ```

Prerequisites get added as you find them:

```sh
dg task dep T01 --after T04       # a prerequisite discovered later
```

That is backwards elaboration, recorded as you learn it rather than searched for
inside the store.

## 3. Launching — and letting agents pick their own work

**Nobody has to hand an agent a role.** `dg task` ends with a computed `ready`
line, `dg task start` refuses work somebody already claimed, and blocked-ness is
derived — so *read the frontier → claim → do → `dg task done` → repeat* is a loop
an agent runs with nobody in it:

```sh
dg show && dg task                 # the frontier: what is open, and what is ready
dg task start T04                  # claim it
dg apply --mine                    # ...and publish the claim, so others see it
# ...do the work...
dg task done T04 --outcome "where the result is"
dg apply --mine
```

The claim is what makes the loop safe under several agents, and it is a refusal
rather than a warning:

```
$ DG_AGENT=beta dg task start T04
T04 is already DOING
```

Nothing in that loop was assigned. `ready` is derived from the edges every time
it is printed, so an agent that finishes `T04` makes `T05` startable for an agent
that was never told `T04` existed and does not know the first one exists — which
is `demo-agentic/` scene 3, run with no model in it at all.

**Apply the claim rather than leaving it in the tray.** `dg task start` stages
like everything else, and until it is applied `dg task` still prints the task as
`startable` — the listing reads the store, the guard reads the tray. Nobody
double-claims it either way: that refusal above is a *stage-time* one, so the
second agent is turned away before it writes anything. What it costs is that the
queue lies to whoever reads it, and `held by <name>` — the thing that tells a
stalled agent from a slow one — does not exist until the op lands. `dg apply
--mine` immediately after the start is the whole of the fix.

**And an agent that stops has to say so.** A task left `DOING` by an agent that
died reads exactly like one being worked on, and the loop has no other way to
hand it back:

```sh
dg task park T04 --why "the cluster queue is six days deep"
```

A parked task says *what* stopped it, which is the difference between work the
next agent can pick up and work nobody knows is free. And `dg check` reads the
park across the seam — a parked task that was the only evidence for an open
decision comes back as the question nobody is producing an answer for:

```
! [evidence_stalled] D02 is OPEN and waits on evidence nobody is producing —
T01 is parked and no other task meant to inform it is still going
```

Who holds what is kept **outside both graphs**, in `.dgraph-agents.json` beside
the names themselves — scratch, gitignored, gone when the run is. Neither store
records who did anything, and that is deliberate rather than an omission: the
stores are committed and kept forever, agent names are recycled the moment they
are released, and "who finished this" is noise six months on that a recycled
name no longer even identifies. Who holds work is a fact about a run.

**The whole contract with the host is environment variables.** Whatever spawns
an agent has to put `DG_AGENT` in its environment — and, if you want rules
rather than habits, `DG_DECIDE`, `DG_WRITE`, `DG_AREA` and `DG_BUDGET` beside
it. Nothing else about the host matters, because everything the agent does *to
the graph* it does through the `dg` CLI, and the one thing it does outside the
CLI — writing files — reaches the same policy through `dg gate`, which both
adapters already relay. That is what keeps this workflow independent of which
model or which scaffold is running it.

**That sentence is why there are two binaries.** `dg-agent` writes this
environment; `dg` reads it. Two binaries either side of a documented contract
say it better than one binary that was both, and the split is what gave the
contract a place to be *reported* — see `dg-agent env` above.

Each agent gets its name from the tool, never one you invent, and `dg-agent
run` is what puts it in that one child's environment:

```sh
for role in …; do
  dg-agent run --decide evidence --write launch --budget 30m \
    -- <however you spawn an agent> &
done
dg-agent list        # who holds what, and what each has staged
```

```sh
dg-agent run -- claude -p "$(cat fanout/scout.md)"      # Claude Code
dg-agent run -- opencode run "$(cat fanout/scout.md)"   # opencode
```

The spawn line stops being host-shaped: `dg-agent run --` wraps either
identically, and everything before the `--` is the same under both.

`agentic/prompts/scout.md` is a **template, not a prompt** — everything below
with each project-specific part left as a `⟨TOKEN⟩`. What an agent should be
told depends on what the fan-out is for, so the blanks are the work; running it
unfilled gets you an agent that has been told nothing. `orchestrator.md` beside
it is the same for an agent that spawns and watches rather than works.

**`dg-agent setup` fills them.** Most tokens come straight from the graph —
the project, the areas, the policies in force and what each means, the write
roots, the budget, and each focus id's full chain pasted from
`dg context --full`. Three do not: what the fan-out is for, what the agents may
read, where findings go. Three ways to answer them — a full-screen form where
`textual` is installed, a question at a time otherwise (which needs nothing the
tool did not already depend on, so interactive setup always works), and flags,
which is what an agent inside Claude Code or opencode uses since it can drive
neither. All three produce the same bytes. `RUNNING.md` §0.5 has it;
`RUNNING.md` end to end is the procedure it automates.

Both files live in `dgraph/prompts/` and are reached here by symlink, so an
installed `dg` carries them and there is exactly one copy to keep true.

`DG_DECIDE` is the other half of the launch, and the table at the top of this
file is what the values mean. Set it here rather than trusting the prompt: an
agent running the loop above finishes work all day, and the moment a finished
`--evidence-for` task leaves a question owed an answer is exactly when the
temptation to write one arrives. `evidence` lets it record what it measured and
refuses the rest; `never` sends every answer back to a person. Unset is the
default and the widest, which is what `demo-agentic/` is written against.

The flags are the host's business and change between versions; the variables are
not. A host with no way to set them per agent can still be used — run each agent
in its own shell and export them there — and a mixed fan-out is fine, since two
agents under different scaffolds are just two names in one tray.

`dg-agent claim` never hands out a name that is held or that has ops in either
tray, so two agents cannot end up sharing one — including across hosts, since
the tray is the only thing either of them touches. A claim does not expire;
`dg-agent release` and `dg-agent prune` give names back deliberately.

**And `dg` itself is host-neutral by construction.** The slash commands ship as
one set of files for both — `/dg:fanout` under Claude Code, `/dg-fanout` under
opencode — and the skill that teaches the recording discipline is loaded by
both. An agent under a third scaffold, or none, still has the CLI, which is
where all of this actually happens.

Each prompt should carry six things:

1. **The chain, in full.** `dg context <id> --full`, pasted in. A fresh context
   knows the task and nothing about why it exists, and without the chain it
   cannot tell a constraint from an implementation detail.
2. **The loop, and that nothing will be assigned.** `dg show && dg task`, claim
   with `dg task start`, `dg apply --mine`, finish with `dg task done --outcome`
   or `dg task park --why`, then read the frontier again. An agent told only what
   to do this once stops when it is done and leaves the rest of the queue sitting
   there.
3. **The rule** from the top of this file — add questions and work, decide
   nothing — **and which `$DG_DECIDE`, `$DG_WRITE`, `$DG_BUDGET` and
   `$DG_TERSE` it is running under**, so a refusal at stage time or a prompt
   about a write reads as the policy it is rather than as a broken tool.
4. **What it may read, and where it may write.** Point at the files; it will not
   find them. Under `DG_WRITE=launch` say so — an agent that knows the scope
   puts its findings in the project instead of discovering the rule by being
   stopped.
5. **That the store holds the synopsis and the file holds the development**,
   and that most of what it would have written is already an edge. An agent
   told only "be brief" writes the same paragraph with the vowels removed; one
   told `dg context` computes the chain writes the edge instead.
6. **Its budget, and that expiry parks rather than discards.** An agent told it
   has thirty minutes can decide what to finish; one told nothing runs until
   something kills it. Say `dg task park --why` is how to hand work back early,
   because an agent that stops without parking leaves work that looks alive.

## 4. Review, one proposal at a time

```sh
dg pending                       # the roster: who proposed what, across both trays
dg pending --agent brisk-beacon   # one agent's proposal, alone
dg serve                         # ...or as a graph, with the tray beside it
dg apply --agent brisk-beacon     # take this one
dg clear --agent agile-azimuth   # turn that one down — the others are untouched
```

Then **you** decide, with the proposals as input:

```sh
dg decide D07 --answer "…" --source "discussion" --falsifier "…"
```

If a proposal turns up a question nobody had written down, add the decision and
link the work to it rather than leaving it in prose:

```sh
dg task link T04 --evidence-for D09
```

## 5. Settle the empirical ones with evidence

The decisions a fan-out cannot settle by arguing are the ones that want a
measurement — a benchmark, a spike, a pilot. That loop is native:

```sh
dg task add --id T09 --title "…" --area … --evidence-for D07
dg task start T09
# ...run the thing...
dg task done T09 --outcome "where the result is"
dg decide D07 --answer "…" --source T09 --falsifier "…"
```

The source is the task, not `discussion`: what settled the question is a thing
that was run, and the id is how somebody six months from now gets from the answer
to the measurement. Where `D07` already had an answer and the evidence merely
holds it up, the verb is `dg confirm D07 --against T09 --note "what it showed"`
instead — a confirm needs an answer standing, and against an `OPEN` question it
refuses.

`dg check` warns when an `--evidence-for` task has finished and its decision is
still unsettled — *the measurement ran and nobody recorded its conclusion*.

**This is the loop `DG_DECIDE=evidence` exists for**, and it is why that value is
not simply a weaker `never`. An agent under it may close exactly the decisions a
finished task of its own backs, so the answer it writes is the measurement it
just ran and the falsifier is the number moving. Everything else is refused at
stage time, before an answer, a source and a falsifier have been composed:

```
✗ nothing staged — $DG_DECIDE=evidence: nothing is `--evidence-for D07`, so
there is no measurement for brisk-beacon to be recording. Link the work that
bears on it — `dg task link <id> --evidence-for D07` — or leave the question
open for a person
```

The refusal names its own fix, which is usually the right one: the agent found a
question its work bears on and had not said so. `dg task link` is cheap, is not
an answer, and is exactly what a fan-out is for.

## 6. Close

```sh
dg-agent expire       # hand back what any out-of-time agent still holds
dg apply --agent …    # ...and take the parks
dg task done T01 --outcome "…"
dg-agent prune        # release the names, now that nothing is staged
dg check
```

**`prune` will not strand work, and says what it kept back.** A name with
nothing staged reads as idle even when its agent is mid-task — that is the first
minute of every agent's life — so releasing it used to strand the task: still
`DOING`, holder no longer recorded, and `dg task start` refusing it as taken.
`prune` now keeps those names and names them:

```
released 1: agile-bearing
kept agile-azimuth — still holds T01
```

`dg-agent release` refuses for the same reason, and `--force` overrides either
when the stranding is what you want — a run whose tasks you are about to drop.

**So `expire` first is a convenience, not a rescue.** Expiring turns a kept-back
name into a park that says *why*, which is what the next agent needs; without it
you park by hand, or prune again once the work is settled. A run with no budgets
has nothing to expire and still cannot be stranded.

---

# Optional: recording the run

**Nothing above needs this.** Turn it on when you want the run itself
afterwards: to show somebody how the workflow behaves, to audit what a set of
agents actually proposed, or to build a demo out of a real session instead of a
scripted one. It was written for that last case.

Everything that touches the graph goes through `dg`, and everything that
launches an agent through `dg-agent`, so **two** wrappers first on `$PATH` catch
every interaction — **including the ones that leave no trace**,
which is the point. `dg drop`, `dg clear --agent` and an applied tray all erase
what was there: the graph keeps what landed and deliberately not what was
proposed.

```sh
export PATH="/path/to/dear-guide/agentic/bin:$PATH"
command -v dg          # must print .../agentic/bin/dg
command -v dg-agent    # ...and this one too, or the launch goes unrecorded
```

The record lands in `.dgraph-capture/dg.jsonl`, which the `.gitignore` `dg init`
writes already covers under `.dgraph-*`.

**Smoke-test it before the run, not during** — both of these fail silently:

```sh
NAME=$(dg-agent claim); echo "[$NAME]"          # exactly one name, nothing else
DG_AGENT=$NAME dg add --id D99 --title x --area General
dg drop 0                                        # ...and throw it away again
python3 -c "
import json
for l in open('.dgraph-capture/dg.jsonl'):
    e = json.loads(l)
    print(e['agent'], e['argv'], 'tray:',
          'empty' if not e['tray'] else f\"{len(json.loads(e['tray']))} op(s)\")"
```

The first property is that **stdout stays clean**: `dg-agent claim` prints a
bare name precisely so `DG_AGENT=$(dg-agent claim)` works, and a wrapper that
prepended a banner would break every launch.

The second is the one the capture exists for. The `add` line shows
`tray: 1 op(s)` and the `drop` line `tray: empty` — so the op that never reached
the graph is still in the log, in full, as the tray recorded on the entry before
it was dropped.

**What it cannot see**, so decide now rather than afterwards:

- **The browser.** `dg serve` is its own process and does not come through the
  wrapper, so a review done by clicking leaves no entry. Either do the recorded
  review in the terminal, or accept the gap and say so — but do not half-do it,
  because the step where you turned a proposal down is the part of the record
  that matters most.
- **Reasoning, and anything that is not a `dg` call.** Whatever host the agent
  ran under keeps that, wherever it keeps it — Claude Code writes one JSONL per
  session under `~/.claude/projects/<slug>/`, opencode keeps its own session
  store. Copy them at the end; nothing else holds them, and in a fan-out across
  two hosts you will be copying from two places.


## Turning a capture into a demo

Be clear about what you have, because it is **not** what `demo-agentic/` is.
That demo is *staged*: scenes in shell, deterministic, replayable, covered by
`test_demo_agentic.py`. Its virtue is that it runs cold and always says the same
thing.

A capture is the opposite: one real run, with dead ends, a proposal that was
rejected, and an agent that misread something. Its virtue is that nobody staged
it. The two are complements, and the captured one is worth most exactly where
the staged one is weakest — nobody believes a scripted demo about whether a
workflow survives contact with real agents.

- **Keep the log raw and narrate separately.** Editing the transcript to read
  better destroys the only property it has. Write the narration as a second file
  that points into the log by timestamp.
- **The refusals are the content.** A `dg apply` that refused because another
  writer's work was in the tray, a `clear --agent` where a proposal was turned
  down, a decision reopened when evidence contradicted it — these are the things
  prose about the tool cannot demonstrate.
- **Sanitise before publishing.** The log holds full model output and your
  prompts; the host's transcripts hold more. Read them, do not skim them.
- **A run where every proposal was accepted demonstrates nothing** about a tool
  whose whole subject is what happens when work is proposed, reviewed, and
  sometimes rejected.
