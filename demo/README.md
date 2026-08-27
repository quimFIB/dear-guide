# Demo: a development graph you can drive

A throwaway project, served locally, holding one of every record `dg` keeps —
a settled decision, a reversed one, a question still open, work in flight, work
parked, work abandoned, and evidence that arrived after the answer it was meant
to inform.

It is small enough to read in a minute and arranged so that nothing in it is
decoration: every vertex, every edge and all three standing findings are there
because some capability has nowhere else to show itself.

## Run it

```sh
./demo/demo.sh          # copies the graph to /tmp/dg-demo and serves it
```

Then open <http://127.0.0.1:8765>. Re-running resets everything, so nothing you
do here can be a mistake.

## What is in it

Seven decisions and ten tasks from an imaginary nearest-neighbour search
service.

```
D01 exact or approximate? ─┬─ D02 which index? ──── D04 efSearch ─── D05 sharding
        DECIDED            │   DECIDED               OPEN            BLOCKED:D04
                           │   (IVF-PQ superseded)   ← decide this
                           │
                           └─ D03 which metric? ──── D07 where is unit norm
                               REOPENED                  enforced?
                               (its falsifier came true)  PROVISIONAL

D06 what is recall measured against?   DECIDED
```

```
T05 recall harness         because D06        DONE
T01 build the index        because D02        DONE
T02 normalise at write     because D07        DONE
T09 measure resident size  evidence for D02   DONE     ← reported after the answer
T03 sweep efSearch         evidence for D04   DOING    ← D04 is waiting on this
T06 CI gate on recall      because D02        TODO     ← startable
T08 assert unit norm       because D07        TODO     ← startable, premise shaky
T04 shard fan-out          because D05        TODO     ← premise not settled
T10 backfill 2023 shard    because D02        PARKED   ← and T04 waits on it
T07 PQ codebook            because D02        DROPPED  ← parked first, then given up
```

Two things in the task graph are not prerequisites. `T01 ⇢ T09` and `T02 ⇢ T08`
are **provenance**: doing that work turned this up. It blocks nothing, which is
why T08 is startable today even though the task that revealed it is finished
and the taxonomy it belongs to is not.

## The one decision: D04

`D04` is the red, dashed node, and settling it is the demo's spine.

1. **Click D04.** The panel shows what it rests on, what it opens, and the
   form. `T03` is listed as the evidence it is waiting for.
2. **Type half a sentence into Answer.** Navigate away and come back — it is
   still there. Drafts are per-decision and survive navigation.
3. **Click Compose in emacs.** A frame opens on `.dgraph-edit.org`:
   - `* Input` holds the fields; point starts in `** Answer`.
   - `** Opens` already has `[X] D05 … (linked)`, because that edge exists. The
     box cannot lie about it — unticking it does not drop the edge.
   - `* Context` below is reference material: the incoming edge, each premise
     with its answer and falsifier, the ancestor chain as an org table. It is
     read-only, and it is not parsed either way, so mangling it cannot change
     what gets staged.
4. **Write a real answer.** Full org: tables, `=verbatim=`, `#+BEGIN_SRC`,
   footnotes. D02's answer is already a sweep table, so there is a worked
   example one click away. It is stored as typed; the browser and
   `decision-graph.md` convert it for display.
5. **`C-c C-o` on a `dg:` link** opens that decision, fetched live through
   `dg export`. `C-c d p` is the parent, `C-c d a` the ancestor chain. Under
   Doom the popup takes focus; `q` comes back.
6. **`C-c C-c`.** Emacs checks the required fields are filled, saves, exits. The
   browser stages the decision **and** `D05 → OPEN`, because it was
   `BLOCKED:D04` and nothing blocks it any more. That propagation is derived,
   never typed.
7. **Press Apply.** Only now are `decisions.json` and `decision-graph.md`
   written.

`C-c C-k` cancels. So does closing the frame without saving: the browser says
"Cancelled in the editor — nothing staged."

The same thing from a terminal, and it says one more:

```sh
dg decide D04 --answer "efSearch=128." --source bench/efsearch-sweep.md \
              --falsifier "p99 passes the 50 ms budget" --opens D05
```

> T03 is still going and was meant to inform this. Your call, but read the
> result against this answer when it lands.

Deciding ahead of the evidence you asked for is allowed and reported. It is
what puts a decision into the state D02 is already in, below.

## The three findings, and how each one ends

A **soundness chip** sits in the header. It appears only when `dg check` has
something to say, so its appearing is the whole signal; clicking it lists the
findings verbatim, remedies included. Three stand in this store, and each one
exists to show a different exit.

### 1. Evidence that arrived after the answer — D02

D02 was settled on 2026-02-14 saying HNSW is 61G resident. T09 measured it on
2026-03-02 and got 68G. Nobody reading the store six weeks later is told to
look, so `dg check` does the telling.

The panel says so on D02: what the work produced, and when. Three readings, all
three reachable:

- **it confirms the answer** — the common one. 68G is inside the 72G the
  falsifier names, so the answer holds and the sweep's figure is what needs
  correcting. Say that, and the reading is stored on the task:

  ```sh
  dg confirm D02 --against T09 --note "68G is inside the 72G the falsifier names"
  ```

  It stages a **task** op, because the reading lives on the task. `dg task
  pending` shows it, not `dg pending`.
- **it refutes it** — `dg reopen D02`, which files a reversal.
- **the answer never needed it** — remove the link, under **Edit structure**.

The note is required, for the reason a drop's reason is: without it the record
says somebody clicked a button, not what they found. And a reading is per
result and per date, so work that finishes *later* brings the finding back
rather than being permanently silenced.

> **What the falsifier was for.** It was written on 02-14, before anyone had a
> number. Had T09 come back with 74G, the same evidence would have tripped it
> and reopening would be the only honest exit. Writing it afterwards is
> rationalisation, which is why the form refuses a decision without one.

### 2. Work resting on a premise under review — T08

T08 exists `because` of D07, and D07 is `PROVISIONAL` — it was decided, then
D03 underneath it was reopened. The work is startable and the reason for it is
under review, which is a fact only the join between the two stores can produce.

It takes two steps, in this order:

```sh
dg decide D03 --answer "Cosine, normalised in the writer." \
              --source "PR #218" --falsifier "the writer stops being the only path in" \
              --opens D07
dg confirm D07                      # the premise settled and this answer still holds
```

Try `dg confirm D07` first and it refuses: *settle the premise first — until
then PROVISIONAL is the accurate status.* The browser behaves identically — no
**Re-affirm** button appears on D07 while D03 is still under review, because
re-affirming would claim a conclusion the graph cannot support.

Re-affirming supersedes nothing. That is the point of having it: the browser
was once the interface that manufactures `PROVISIONAL` and could not clear it,
so the one act it offered filed a reversal that never happened.

### 3. A park holding work up — T10

T10 is parked and T04 still waits on it. A park settles nothing downstream, and
that is the whole difference from a drop — so without this check the cheapest
thing the store offers would fill the backlog with work silently held.

Three exits, and the finding names all three: pick T10 up, drop it, or remove
the dependency if it was never real. Try the drop:

```sh
dg task drop T10 --why "the 2023 shard is not coming back"
```

> 1 task(s) need a verdict before T10 can be dropped
>   T04 — released, waited only on T10
> re-run naming each one: `--keep A,B` and/or `--drop-too C,D`

Abandoning work releases whatever waited on it, and that may be wrong: a
prerequisite that would have *produced* something does not release its
dependants, it undermines them. So the question is asked at the moment somebody
can answer it. **Drop it** in the browser asks the same question, from the same
function, before it stages anything.

## Reversals: the record this model exists to keep

Superseded is not a status here. It is an edge kept forever, and both kinds are
in the store.

**D02 — a reversal that finished.** `dg node D02`, or the panel, shows the live
answer and below it:

```
Superseded
  "IVF-PQ, nlist=4096, 64 subquantisers" → HNSW, M=32, efConstruction=200   2026-02-08
    why    The 0.95 target was not a preference. T05's harness scored IVF-PQ at
           0.918 on the frozen query set — four points under …
```

The archived answer, its falsifier, what it opened, and what overturned it. T07
is the work that died with it: dropped, with *"D02 went the other way — there
is no codebook to train"* as the reason.

**D03 — a reversal in progress.** Its falsifier said *"the encoder stops
emitting unit-norm vectors"*. Encoder v3 did exactly that, so the answer was
superseded and D03 is `REOPENED` with nothing in its place yet — and everything
decided underneath it went `PROVISIONAL`, which is finding 2 above.

## The work, and the two ways it stops

```sh
dg task node T07     # DROPPED
dg task node T10     # PARKED
```

Both show a **Stopped** list, and T07's has two entries: parked on 02-06
because the batch box was busy, abandoned on 02-15 because D02 went the other
way. The list is never cleared, so work stopped three times shows all three,
the last tagged *still parked* or *abandoned here* by whichever status claims
it. It is the one record here that outlives the state describing it.

**Park it** and **Drop it** write the same record and differ only in what they
release. Parked work is offered one button, **Pick it up again** — finishing or
abandoning work nobody is doing skips a step somebody has to take deliberately.

## Reading it from a terminal

The same store, no server needed:

```sh
cd /tmp/dg-demo

dg                     # the frontier: D03, D04, D05
dg brief               # ...plus provisional work, staging, and the check
dg node D02            # one decision in full, reversals and all
dg why D04             # the chain of premises underneath it
dg context T08         # D01 → D03! → D07! → T08 — across both stores
dg path D01 D05        # the single line of reasoning between two decisions
dg areas               # counts by area, one table per store
dg task                # outstanding work, and what is startable
dg task tree           # the order of the work, provenance marked "turned up doing it"
dg find recall         # one word, both graphs, with why each row matched
dg find 'is:unsettled' # a derived question, answered by the method behind it
dg check               # every invariant
```

`dg context T04` is the reading neither store gives alone. T04 waits on T10
*and* on D05, and the two are different problems — the panel and the CLI both
lead with the premise, because "starting it now is a bet on the answer" is the
one a prerequisite list cannot tell you.

## What to poke at

- **Leave Source empty and press `C-c C-c`.** Emacs refuses before exiting and
  puts point on the empty field. Python checks again regardless, for editors
  that cannot check anything.
- **Try to edit under `* Context`.** It signals `text-read-only`.
- **Open a second browser tab and click Compose in emacs there** while the first
  is still open. Refused: one buffer per project, the same property
  `COMMIT_EDITMSG` has.
- **Start the editor, then click other nodes.** The graph stays browsable, D04's
  button reads "waiting for emacs", every other node says an editor is open for
  D04, and Apply is held back. When you `C-c C-c`, the confirmation is waiting
  on D04 however far you wandered.
- **Reopen a decided one.** Click D02, then Compose in emacs. The buffer leads
  with what reopening drags into `PROVISIONAL`, because that set is the reason
  to interrupt someone.
- **+ new** in the header. In **joined** it asks which store you mean, because
  the tab has not answered that and guessing is how work gets filed as a
  decision. Choosing `BLOCKED` stages the dependency as an *edge*.
- **Edit structure** on any panel. Removing a prerequisite or a `because` says
  what it sets loose *before* staging anything — including any new `dg check`
  finding it would introduce. Try removing D02's premise: refused, because D01
  is decided and its targets are part of that answer.
- **why…** beside a decision's premises walks the whole chain and leads with
  whether anything underneath is still unsettled. From any premise there,
  **the chain from here…** is `dg path`.
- **Switch to "joined"** and click T09. The highlight crosses the seam: the
  dotted cyan edge to D02 is the `evidence_for` link, and T09 sits *above* D02
  because the answer waited on the work. T04 sits *below* D05, because the work
  waits on the answer.
- **Stage a decision and a task, then Apply once.** Both stores are written
  (the generated views are produced on demand with `dg render` /
  `dg task render`), and `dg pending` / `dg task pending` in a terminal show the
  same two trays the footer does. If one batch will not apply, the other still
  does — you are told which.
- **Resolve all three findings** and the chip disappears. That is the only way
  it goes away; there is no field anywhere whose job is to silence a check.

## Notes

- The work directory is `/tmp/dg-demo` (`$DG_DEMO_DIR` to move it,
  `$DG_DEMO_PORT` for the port). Re-running the script resets the graph.
- `$DG_GUI_EDITOR` picks a different windowed editor. `$EDITOR` is deliberately
  ignored here: it names a terminal editor, and the server has no terminal to
  give it. Without emacs you lose the links, the navigation and the pre-flight
  check, but the buffer still round-trips.
- With no `$DISPLAY` the Compose button is not offered at all, rather than
  offered and hanging.
- Restarting `dg serve` invalidates any page left open — the token is minted per
  run. Reload and the new one comes with it.
