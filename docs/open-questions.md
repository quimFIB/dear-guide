# Still open

The design questions this tool has not settled — each with what it does today,
the argument for it, and what would change it. A non-goal asserted without its
reasoning is a non-goal nobody can reopen, so these are written as the
arguments they are rather than as a list of limitations.


- **Whether an agent should `apply` unattended.** It does, today: `apply`
  validates a copy before writing anything, the store is append-only, and the
  result is a git-tracked diff reviewed like any other — whereas an op staged
  into a gitignored file and then committed over is unrecoverable. Staging for a
  human to confirm would also mean the commit gate stopped to ask on nearly every
  commit, which is how a gate stops being read.
- **Whether two agents may work one graph.** They do, and what is still open is
  *isolation* rather than whether it can be done at all —
  [`agentic/README.md`](../agentic/README.md) is the fan-out end to end, with the
  rule it rests on and the cost it carries. The honest summary of that cost is
  still *one writer at a time* inside a single checkout, and what a second writer
  can do to you is now reported rather than silent. Each staged op records what the premises it names
  looked like when it was composed, and `dg apply` says which of them moved
  while the batch waited:

  ```
  · D01 moved since this batch was staged (DECIDED → REOPENED) — op 1
    (add_edge D01) rests on it
  ✓ applied 2 op(s) → decisions.json; the view is regenerated on demand with
    `dg render`
  ```

  Never a refusal: the invariants already refuse the case that matters (a
  decided answer on a reopened premise is a blocking `propagation` finding, and
  that batch aborts naming the premise). This covers what is still *legal* after
  the ground moves and would otherwise land in silence. What holds under contention has been tested and does
  hold: the locks work across processes, the atomic writes hold, a collision is
  always a refusal and never a corrupt store, and an op refused because somebody
  else applied it says so rather than reading as "your work failed". What does
  **not** hold is isolation — the staging tray is one file per project, and
  what two agents can still do is refuse each other's guards and reason about a
  graph the other is halfway through changing. One person in two terminals, or
  a browser and a terminal, is fine and is what `commands/serve.md` describes:
  that is two writers with one intent. Two agents are two intents.

  What has stopped happening is the silent half of that. **Set `$DG_AGENT` and
  each staged op records who staged it** — and the name itself now comes from
  `dg-agent claim` rather than from whoever was launching, because every value
  that variable went wrong on was one somebody invented. A claim is checked
  against the leases *and* both trays, so two agents cannot share a name; it
  never expires; and if the 7004 ever run out, `claim` refuses and says what to
  empty instead of inventing one, `dg apply` writes yours and leaves the
  rest, and an unowned `dg apply` refuses a tray holding somebody else's work
  rather than sweeping their draft into the store — `--all` and `--mine` say
  which you meant, and `--agent <name>` names one of them. That last one is for
  the review the other two cannot express: several agents proposing
  *alternatives* into one tray, where the supervisor means to write one of them
  and turn the rest down. `dg pending` counts them under the listing so the
  names are discoverable, `dg pending --agent b` reads one proposal on its own,
  and `dg clear --agent b` is the reject verb — a bare `dg clear` takes the
  whole file whoever runs it, which is blunt once four agents share it. The one
  constraint on a name: **`unowned` is reserved**, because it is what the tool
  calls an op nobody signed, and a writer by that name made the reading and the
  write disagree about who was meant. Anything else goes. Where
  agents propose *complementary* pieces of one elaboration the union is what
  you want, and `--all` always was. That mattered most for a `close`: applied by mistake it is a
  decision, and the only way back is a `reopen` that files a reversal nobody
  made. The tray deliberately stays **one file**, so `dg brief`'s "staged and
  about to be lost" still counts everybody's; splitting it per agent would push
  every conflict from stage time — where the second answer is refused before it
  is written — to apply time, where it is refused after. Unset the variable and
  none of this exists: every op is unowned and every apply takes the tray, which
  is what a single writer has always had.

  All of that is runnable rather than asserted — [`demo-agentic/`](../demo-agentic/)
  is a day's work on one graph with three agents on it, driven by the *task*
  side: the queue says what is ready, an agent picks it up, doing it produces
  evidence, and evidence settles a question. The concurrency problems arrive out
  of joining that work up rather than as the subject. `./demo-agentic/demo.sh`. Six of the seven scenes close with the tool doing
  something; the seventh is the stale premise, which no locking discipline
  reaches and which the falsifier is there for.

  One class of collision is now off the table, though, and it is worth naming
  because it was the worst-behaved: two clones of one graph both computed the
  next id as `max(stored) + 1`, so on a shared base they did not *sometimes*
  pick the same id — they picked it **every time, for every record either of
  them added**. `dg range --set 50-99` grants a clone a range of its own, and
  from then on every door allocates inside it and refuses an `--id` outside it.
  It is prevention, not correctness: what it buys is that a future integration
  report is not a rename line per record anyone wrote, which is the volume that
  trains a reader to stop reading it. Nothing fires without a grant, which is
  every single-writer project.

  **And bringing two clones together no longer needs a hand-edit.** `dg
  integrate <ref>` expresses an arriving contribution as *ops* against the
  graph you have — derived from what its writer started from, which is what
  makes a removal a removal — and replays them, collecting every conflict
  before asking anything:

  ```
  3 op(s) from worker against 32ec2d15d9 — 0 clean, 3 contested, 0 blocking

  contested — it applies, but this graph says otherwise.
    d0  D01 title differs — here 'Which index structure?', arriving 'Which index, at 48M?'
    d1  D01 was answered here too — 'IVF-PQ' against 'HNSW, M=32'
    t0  T01 was finished here too (2026-06-01, 'recall 0.91') — arriving 'recall 0.94'
  ```

  Those three are the ones only a person can settle, and they arrive together
  rather than one refusal at a time. Everything else is mechanical or is a
  refusal quoting a rule. The ops wait in `.dgraph-incoming.json` — **not the
  tray**, because the tray is what every stage-time guard consults and an
  unadjudicated op there would have this clone answering `dg node` with a
  title nobody accepted — and the commit gate denies while it is non-empty,
  since that file is gitignored and a commit over it drops the contribution
  with nothing saying it arrived.

  Each contested op is answered by ref — `dg incoming --take d1` for the
  arriving version, `--keep d1` for this store's, or `--split d1 --as D51`
  where the two answers turn out to be to two different questions worded as
  one — and `--adopt` moves the whole contribution into the trays once every
  one has an answer. It refuses
  while any is open and there is no `--force`: a flag that adopted everything
  would answer those three questions by not asking them. Taking an arriving
  answer inserts the `reopen` the store would demand of anybody, so this
  store's answer becomes history rather than being overwritten. **Keeping
  yours records theirs** as *offered and not adopted* — a third kind of edge,
  with no `why` and no `replaced_by`, because nothing was overturned. Without
  it the seam would be a choice between losing an answer and claiming the
  project once believed something it never did.

  **A removal is contested too, where this clone moved the record.** The
  arriving side deleting `D07` while you retitled it, moved its status, or hung
  a fresh question on it is two writers disagreeing about whether `D07` should
  exist — so it is reported and answered like the other three, rather than the
  removal simply landing. Add-wins, and *declared*: the alternative is
  defensible and what it replaced was remove-wins by accident, with the report
  printing `nothing contested` over an edit somebody lost. Where nobody here
  touched the record, a removal is still just a removal.

  An arriving id this store already holds is **not** one of the three: it is
  renamed inside the contribution, where an edge still knows which vertex it
  meant, and reported rather than asked about. Only ids the merge introduces
  move — an established one is cited in commits and docs this store cannot
  reach. And the report ends with the warnings the contribution introduces
  and the records it touched: several of those warnings depend on which side
  integrated first, so they are advisory and nothing should key off them, and
  a clean `dg check` afterwards is not evidence that the work arrived.

  What this does **not** change is isolation inside one clone: the tray is
  still shared and still has no notion of whose ops are whose. Two agents in
  one checkout is the same as it was. Two agents in two checkouts is now a
  mechanism rather than a hand-edit.
- **Whether the store stays per-repo.** Probably: decisions are about a codebase.
  A cross-project view would need a different addressing scheme.
- **Whether the falsifier can be checked rather than merely recorded.** A
  falsifier nobody revisits is a comment. *Read back now, judged not yet:*
  `dg probe` presents every falsifier — and every rule for settling and
  definition of done — beside what it is judged against, and each can carry a
  typed `probe` that installed code could evaluate. Only the `prose` domain
  ships, and it presents without judging, so the verdict is still the command
  you run next. What stays open is whether an evaluating domain is ever
  written against the seam; `reference.md` § *Pre-commitments* has the shape.
- **Whether the unrecorded-decision nag is worth trying behind a flag**, and
  whether `decision-graph.md` should eventually be org rather than markdown.


## Where to go next

- [How it works, and why](how-it-works.md) — including
  [bringing somebody else's work in](how-it-works.md#bringing-somebody-elses-work-in),
  which is the long form of the integration sketched above.
- [`agentic/`](../agentic/README.md) — the fan-out end to end, and the rules it
  rests on.
- [Reference](reference.md) — the model, the commands, and what `dg check`
  enforces.
