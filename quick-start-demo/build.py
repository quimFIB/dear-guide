#!/usr/bin/env python3
"""Build index.html from the recipes' transcripts and snapshots.

Nothing on the page is typed in by hand except the prose in RECIPES below:
every command, every line of output and every graph picture comes from
out/, which run.sh produced by running the recipes against the seed. The
highlighted lines are the same regexes tests/test_quick_start_demo.py
asserts, so a line the page points at is a line the test checks for.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
RECIPE_DIR = HERE / "recipes"
SEED = HERE / "seed"

# --------------------------------------------------------------------------
# The prose. `hl` entries are (regex, margin note) pairs; a regex is matched
# against every line of that layer's transcript, and the test requires each
# one to match at least once.
# --------------------------------------------------------------------------

PARTS = {
    "build": ("Build", "Adding, linking and removing vertices and edges"),
    "ask": ("Ask", "Frontiers, reasoning, and the backlog"),
    "agents": ("Agents", "Letting agents develop a task from the graph"),
    "extra": ("Beyond", "Two more things people ask for"),
    "annex": ("Annex", "What would settle it, written down first; what a reader shows of the tray; and an answer checked against two measurements"),
}

RECIPES: dict[str, dict] = {
    "01-start": dict(
        problem="It is May, and notelit is not much more than a README. The first real argument is "
                "about storage: should notes be plain markdown files that any editor can open, or "
                "rows in a database that notelit manages? Almost everything later depends on that "
                "choice, from how search works to what a backup is. Before anyone answers it, the "
                "team wants the question written down somewhere that will still be accurate in a "
                "month, with a note saying why it matters.",
        solved="notelit now has its two record files, one for decisions and one for work, and the "
               "storage question is the first record, D01. Nothing was written the moment the "
               "question was added: it waited in a staging tray until <code>dg apply</code>, so a "
               "change can be read before it lands. <code>dg</code> on its own lists what can be "
               "decided, and D01, resting on nothing, is decidable now.",
        read_full="Some teams already have their questions in a file before they hear of "
                  "<code>dg</code>. A store you wrote elsewhere is adopted, not converted, and adoption "
                  "refuses a file that would break an invariant rather than letting <code>dg "
                  "check</code> find out later. The export carries the derived blocks too, so a graph "
                  "copied between machines comes back byte-identical.",
        hl_quick=[(r"^staged add D01", "into the tray, not the store"),
                  (r"^✓ applied 1 op", "now it is written"),
                  (r"decidable now", "nothing above it, so it can be answered")],
        hl_full=[(r'has "owner", which a decision does not have', "the schema, named at the door"),
                 (r"^✓ imported 1 vertices", None),
                 (r'"derived"', "recomputed on import, never trusted")],
    ),
    "02-add": dict(
        problem="One of notelit's open questions is how two machines share one notes folder: someone "
                "keeps notes on a laptop and on a desktop, and both copies have to stay in step. That "
                "question is D08. While discussing it, someone raises a narrower one: when the folder "
                "is synced, which files should be left out? The narrower question only makes sense "
                "once D08 is answered. If notelit ends up leaving sync to Dropbox or git, the list "
                "belongs to those tools. Filed as a loose to-do, it would look answerable today.",
        solved="The new question, D09, sits under D08 in the tree, and cannot be decided until D08 "
               "is. <code>--after D08</code> recorded that as a link between the two questions, and "
               "before anything was written the tray showed both parts, the question and the link, "
               "as one change.",
        read_full="Further in: changing your mind before anything is written, a mistyped area, a "
                  "question that rests on nothing, and labels. <code>--after</code> is the whole of "
                  "dependency: an edge, never a field. The tray shows the vertex and the edge as two "
                  "ops staged together. An act is staged whole and dropped whole, so half a question "
                  "can never land. Areas are not declared up front; they accumulate, and the only guard "
                  "is against a near-miss of one already in use. A question resting on nothing is "
                  "allowed and flagged: an unconnected decision is a smell, an unconnected task is "
                  "ordinary. Tags are free words beside the area, in either store, and stand under the "
                  "same guard; <code>dg tags</code> counts them, since a tag is held by the records "
                  "that carry it and registered nowhere.",
        hl_quick=[(r"add_edge\s+D08\s+→ D09", "the dependency, as an edge"),
                  (r"└── D09", None)],
        hl_full=[(r"was staged together with 2 other op", "an act is dropped whole"),
                 (r"^\$ dg drop 2 --group", None),
                 (r"close to areas already in use", "the typo guard"),
                 (r"\[no_orphans\]", "allowed, and said"),
                 (r"close to tags already in use", "the same guard, for tags"),
                 (r"^│ perf\s+│\s+2 │\s+2 │\s+4 │", "held by the records, both stores")],
    ),
    "03-decide": dict(
        problem="The sync question, D08, has been open since May. After a long discussion the team "
                "settles it: notelit will not sync anything itself, because people already use "
                "Dropbox, Syncthing or git for that. What usually gets lost is not an answer like "
                "this but its conditions. Everyone in the room knows the answer would be wrong if "
                "users started asking notelit to fix conflicts it never caused. Nobody writes that "
                "down, and a year later the answer looks unconditional.",
        solved="D08 is decided, and carries three things: the answer, where the reasoning lives, and "
               "a falsifier, the observation that would prove the answer wrong. The falsifier was "
               "written now, before any such user exists, so nobody can move the goalposts later. "
               "The answer raises no further questions, so it is marked terminal.",
        read_full="Two harder cases. The corruption question, D06, gets an answer that raises a new "
                  "question. And the index location, D04, was answered in June before anyone measured "
                  "anything; the measurement arrives now. An answer that opens further questions must "
                  "name them and must carry a falsifier. Deciding while the evidence task is still "
                  "outstanding is allowed and noted; when the result lands later, the check asks for it "
                  "to be read against the answer, and <code>dg confirm --against</code> is how you say "
                  "it held.",
        hl_quick=[(r"^staged 1 op\(s\) — review", None),
                  (r"opens\s+TERMINAL", "nothing follows from it"),
                  (r"falsifier\s+a user asks", "written before the evidence")],
        hl_full=[(r"^missing --falsifier", "refused without one"),
                 (r"^note: D06 is being settled while T04 is still outstanding", "allowed, and remembered"),
                 (r"\[evidence_after_deciding\].*D04", "June's answer, September's evidence"),
                 (r"^staged D04 read against T12", "the reading is dated and kept"),
                 (r"read 2026-\d\d-\d\d — Under a tenth", None)],
    ),
    "04-add-task": dict(
        problem="Whenever two machines edit the same note, a sync leaves a conflict copy behind, and "
                "someone wants to write the rule notelit uses to recognise those copies. That is "
                "work, not a question. But the rule depends on how the still-open sync question, D08, "
                "is answered: whether notelit syncs by itself or leaves it to other tools. It should "
                "also wait until the README's sync section, task T08, is written. On an ordinary "
                "to-do list it would look ready to start today.",
        solved="The recognition rule is task T12, filed because of D08 and after T08, and <code>dg "
               "task</code> lists it as waiting on both. Nobody marked it blocked: <code>dg</code> "
               "works that out from the links, and T12 becomes ready by itself once D08 is decided "
               "and T08 is done.",
        read_full="Three tasks, each linked to a question in a different way, because each link means "
                  "something different. <code>--because</code> is the seam between the two graphs: this "
                  "work exists because of that answer. Since the answer is still open, the task waits "
                  "on it, and that is derived, never stored. Three links make three different claims. "
                  "<em>because</em> makes the work wait on the answer; <em>evidence-for</em> makes the "
                  "answer wait on the work; <em>discovered-during</em> is provenance and makes nothing "
                  "wait at all.",
        hl_quick=[(r"^staged T12 after T08", "a prerequisite, as an edge"),
                  (r"T12\s+TODO.*waits T08, D08 \(undecided\)", "blocked by a question and by work")],
        hl_full=[(r"^staged T14 discovered during T06", "provenance"),
                 (r"T14.*startable", "…which blocks nothing"),
                 (r"T13\s+TODO.*evidence for D08", "the answer waits on this"),
                 (r"turned up doing it", None),
                 (r"starting it now\s*$", "the premise is a bet")],
    ),
    "05-link": dict(
        problem="Connections between records are usually noticed late, after both records exist. Two "
                "turn up in the same week. First, someone realises that the sync question, D08, "
                "depends on where the index file lives, D04: the index sits inside the notes folder, "
                "so any sync tool will copy it between machines as well. Second, someone working on "
                "T11, which makes <code>[[wikilinks]]</code> in search results show the notes they "
                "point to, realises the work assumes notelit prints plain text. That was decided in "
                "D05, when the team ruled out an interactive screen. Neither connection is recorded "
                "anywhere.",
        solved="D08 now rests on D04 as well as on the storage decision, and T11 is linked to D05. "
               "<code>dg why T11</code> shows the chain behind the wikilinks work, from the storage "
               "decision through D05. If D05 is ever reversed, T11 is among the work <code>dg</code> "
               "flags.",
        read_full="Links added after the fact come off the same way, and on the work side there are "
                  "three kinds. Adding a premise to an answered question is additive, so it is allowed; "
                  "the answer, its source and its falsifier all still stand. On the task side, every "
                  "relation names its kind, because both kinds can hold between the same pair.",
        hl_quick=[(r"^staged D08 rests on D04", None),
                  (r"^staged T11 linked", None),
                  (r"CHAIN\s+D01 → D05 → T11", "the work now has a chain behind it")],
        hl_full=[(r"opens\s+D06, D08", "the answer's targets follow the graph"),
                 (r"^staged D08 no longer rests on D04", None),
                 (r"^staged T05 discovered during T02", None),
                 (r"found doing T01, T02", "two origins, both kept"),
                 (r"T09\s+TODO.*waits D06 \(undecided\)", "released from T04; still waits on the question")],
    ),
    "06-remove": dict(
        problem="A new contributor files a task: check the index for corruption when opening it. That "
                "task already exists as T04, <em>Detect a corrupted index on open</em>. Keeping both "
                "means two people may do the same work, and any links would be split across two "
                "records. This is the rare case where deleting is right, because the record should "
                "never have been written. Most corrections are not like this: a wrong answer, or work "
                "that will not be done, belongs on the record.",
        solved="The duplicate, T12, is gone, and its link was folded into T04. <code>dg</code> "
               "agreed to remove it only once the record was committed to git: removal keeps no "
               "history of its own, so it insists git has a copy.",
        read_full="Questions can be removed too: one filed by mistake, one sitting between two others, "
                  "and one that work still depends on. Three shapes: sever drops the edges, splice "
                  "joins what the vertex sat between, into moves its edges onto another. A decision "
                  "that work still names cannot go until the work points elsewhere. Anything else, a "
                  "wrong answer or work that will not be done, is a reopen or a drop, which keep the "
                  "history.",
        hl_quick=[(r"^\$ git commit", "git is the record of what removal takes away"),
                  (r"^remove T12 \(into\)", None),
                  (r"after\s+T01\s+→ —", "its edge moved onto T04")],
        hl_full=[(r"has uncommitted changes, so `git log -p` would not recover", "refused until committed"),
                 (r"^remove D09 \(splice\)", None),
                 (r"asserts D04 → D10", "an edge nobody wrote, asserted on purpose"),
                 (r"2 task\(s\) name D06: T04, T09", "work still rests on it")],
    ),
    "07-frontier": dict(
        problem="It is Monday and someone has an afternoon to settle one of notelit's open questions. "
                "Four are open, but not all of them can be answered yet. One waits on an evaluation "
                "that is still running, another on a detector nobody has written, another on a "
                "different open question. A plain list of open issues does not say which is which, so "
                "people pick one up and discover halfway through that it cannot be finished.",
        solved="<code>dg</code> with no arguments lists the four open questions and what each is "
               "waiting on. Only D08, the sync question, is decidable now. D03, the embedding model, "
               "waits on evidence from T06; D06, the corruption question, waits on T04; and D07, "
               "ranking, waits on D03.",
        read_full="The same set as a table, as bare ids for a pipe, and as the tree it sits in. "
                  "<code>dg brief</code> is what a coding agent is handed at the start of a session: "
                  "the frontier, anything resting on a premise under review, and what is staged.",
        hl_quick=[(r"D08.*decidable now", "nothing stands in its way"),
                  (r"D07.*waits D03", "a premise first"),
                  (r"D03.*evidence T06", "work in progress will settle it")],
        hl_full=[(r"^D08$", "ids alone"),
                 (r"^FRONTIER \(4\)", None),
                 (r"^TASKS\s+11:.*\(3 ready, 2 blocked\)", "readiness, computed")],
    ),
    "08-why": dict(
        problem="A new contributor is asked to settle D06: what should notelit do when its index file "
                "is corrupted? Before proposing an answer they need to know what the question already "
                "takes for granted: that notes are plain files, how the index is built, where it "
                "lives, and whether any of that could still change. They also notice T09, <em>Rebuild "
                "the index automatically when it is corrupt</em>, and wonder why nobody has started "
                "it.",
        solved="<code>dg why D06</code> shows the answers the question rests on, from the storage "
               "decision through search to the index location, all settled. <code>dg why T09</code> "
               "shows that the work waits on D06 itself, so starting it now would be betting on an "
               "answer nobody has given.",
        read_full="One command, two questions: why a decision is where it is, and where a piece of work "
                  "comes from. The chain is the graph, read upward. <code>--full</code> prints every "
                  "premise's answer, evidence and falsifier, which is the form to paste into a prompt. "
                  "<code>dg node</code> shows one decision with its reversals; <code>dg path</code> the "
                  "chain of evidence between two.",
        hl_quick=[(r"^CHAIN\s+D01 → D02 → D04 → D06", "the premises, nearest last"),
                  (r"^→ this work waits on D06", "the premise is not settled")],
        hl_full=[(r"^\s+falsifier:", "what would overturn it"),
                 (r"^│ Superseded", "answered twice; both kept"),
                 (r"why\s+p95 hit 340 ms", "the reason for the reversal")],
    ),
    "09-tasks": dict(
        problem="Someone has a day for notelit and asks what they can work on. There are eleven "
                "tasks, and several look available but are not. The automatic index rebuild, T09, "
                "needs the corruption detector, T04, to exist first, and it also depends on a "
                "question nobody has answered. On a board where people mark tasks blocked by hand, "
                "those labels drift out of date within a week.",
        solved="<code>dg task</code> says T04, T05 and T11 are ready. They start T04, finish it with "
               "a note of what they did, and the list is recomputed. T09 is still not ready: its "
               "prerequisite is done, but the question it rests on, D06, is still open.",
        read_full="Not all work gets finished. Ready is computed from prerequisites and premises, so "
                  "there is no status to keep up to date, and stopping work is where that matters most. "
                  "Two ways to stop, differing only downstream. Parking holds what waited; dropping "
                  "releases it, and asks for a verdict on each released piece now, while the reason is "
                  "in mind. Every stop and every completion is appended, never cleared.",
        hl_quick=[(r"^ready T04, T05, T11", "computed, not stored"),
                  (r"T09\s+TODO.*waits D06 \(undecided\)", "T04 is done; D06 is not"),
                  (r"^ready T05, T11$", "T09 still waits on D06")],
        hl_full=[(r"^1 task\(s\) need a verdict before T06 can be dropped", "decide the fallout now"),
                 (r"\[released_by_drop\]", "the check keeps asking"),
                 (r"\[evidence_dropped\]", "D03 has no evidence coming"),
                 (r"^note: T07 waited on T06, which was abandoned", "said to whoever starts it"),
                 (r"^│ Stopped", "history, never cleared")],
    ),
    "10-find": dict(
        problem="Someone remembers the team talking about index corruption but not where, and "
                "searching the chat history returns fifty messages. They want every question and task "
                "that mentions corruption. Separately, they want to see just the tasks they could "
                "start today.",
        solved="A word search found the corruption question, D06, and the two tasks about it. "
               "<code>is:ready</code> returned T04, T05 and T11, the same set <code>dg task</code> "
               "reports, because search and the listings share one definition of ready.",
        read_full="A bare word searches prose; <code>is:</code> asks a derived question and gets the "
                  "same answer the frontier commands give. Nothing is ranked, nothing fuzzy. Superseded "
                  "answers are searched too, since a reversal is often the only place a rejected "
                  "approach is written down. Exit 1 means nothing in the store says that, a fact worth "
                  "trusting; exit 2 means the question could not be answered as asked. "
                  "<code>tags:</code> matches a tag exactly, in both stores at once. "
                  "<code>--subgraph</code> turns the matches into a seed and prints the slice they "
                  "induce as two stores: <code>boundary</code> names what the slice still mentions and "
                  "no longer holds, per record, and <code>--derived</code> adds what the whole graph "
                  "says of each record &mdash; beside the stores, never inside them, so a loaded slice "
                  "cannot mistake a whole-graph fact for one of its own fields.",
        hl_quick=[(r"D06\s+OPEN\s+What happens when the index is corrupted", "matched on its title"),
                  (r"^TASKS\s+3 match", None)],
        hl_full=[(r"superseded answer:", "the rejected approach, still findable"),
                 (r"^\[exit 1\]", "nothing matched"),
                 (r"tags: perf · search", "one word, both stores"),
                 (r"^no predicate `is:nonsense`", None),
                 (r"^\[exit 2\]", "could not be asked that way"),
                 (r'"boundary"', "what the slice names and does not hold, and from where"),
                 (r'"outside"', "the whole graph's fact, beside the store")],
    ),
    "11-reopen": dict(
        problem="In July the team settled how search works, D02: one SQLite full-text table for all "
                "notebooks. They also wrote down what would prove it wrong: search getting slow at "
                "100,000 notes, which they defined as at least one search in twenty taking longer "
                "than 200 ms. In September a user imports exactly that many notes and measures 260 "
                "ms. The answer is now in doubt, and that is not the worst of it. Other answers were "
                "decided on top of it, such as where the index file lives, and work was planned "
                "around it. Nobody has a list of what depended on D02.",
        solved="Reopening D02 keeps its answer as history and marks everything decided on top of it "
               "as provisional. Here that is D04, the index location, and <code>dg</code> found it, "
               "not a person. D02 is back among the open questions, and the listing now also shows "
               "what rests on a premise under review.",
        read_full="A bigger shock: the storage decision underneath everything is challenged, by three "
                  "users editing one note on two machines in a day. Reopen the root and three answers "
                  "are provisional at once, along with the work standing on them. <code>dg "
                  "confirm</code> is the honest exit: re-examined, it holds. Reaching for reopen and "
                  "decide instead would file a reversal that never happened.",
        hl_quick=[(r"become PROVISIONAL", "computed by the reopen"),
                  (r"^RESTING ON A PREMISE UNDER REVIEW", None),
                  (r"D04\s+PROVISIONAL", None)],
        hl_full=[(r"3 decided descendant", "one fact, three answers under review"),
                 (r"^D04 still rests on D01", "no exit until the premise is settled"),
                 (r"\[stale_provisional\]", "premise settled again; re-examine"),
                 (r"back to DECIDED", "confirmed, no reversal invented"),
                 (r"^│ Superseded", "the root's first answer, kept"),
                 (r"settled again since this was decided \(by the dates, a heuristic\)", "what moved under it, labelled as the heuristic it is")],
    ),
    "12-supersede": dict(
        problem="Same starting point as the previous example: a user with 100,000 notes measures "
                "searches at 260 ms, exactly what the search decision, D02, said would prove it "
                "wrong. This time the team already has a fix, and has measured it: split the index "
                "into several tables of 20,000 notes each. They need to replace the answer without "
                "losing the reasons behind the old ones, and without leaving what was built on D02 "
                "silently resting on an answer that no longer stands.",
        solved="D02 now carries the split-table answer and a new falsifier, and both earlier answers "
               "stay in its history with the reason each was replaced. <code>dg check</code> pointed "
               "at D04, the index location, as needing a second look. The team decided it still "
               "holds, and <code>dg confirm</code> recorded that without pretending the answer "
               "changed.",
        read_full="The June answer about where the index lives meets users who sync their notes folder, "
                  "which is exactly what its falsifier named. A falsifier that comes true is the case "
                  "the record was built for. Reopen with the fact as the reason, answer again with a "
                  "new falsifier, and everything downstream reads the new answer from then on, while "
                  "the old one stays findable with the reason it was overturned.",
        hl_quick=[(r"1 decided descendant\(s\) rest on it and become PROVISIONAL", "D04 was decided on top of it"),
                  (r"\[stale_provisional\].*D04", "premise settled again; re-examine D04"),
                  (r"D04 back to DECIDED", "it holds; no reversal invented"),
                  (r"^│ Superseded", "both earlier answers, kept"),
                  (r"why\s+a user imported 100k notes", "and why each was overturned")],
        hl_full=[(r"^\$ dg reopen D04 --why", None),
                 (r"D04\s+REOPENED", None),
                 (r"superseded answer: Beside the notes", "the overturned answer, still searchable"),
                 (r"^\$ dg decide D04", "answered again, with a new falsifier"),
                 (r"^│ Answer\s+│$|^│ In the user's cache directory", None),
                 (r"^\s+D04\s+DECIDED\s+Where does the index live\?", "D06's chain now carries the new answer")],
    ),
    "13-check": dict(
        problem="The corruption detector, T04, was written to answer one question, D06: what should "
                "notelit do when its index is corrupted? The code gets merged and the task is closed. "
                "What usually happens next is nothing. The result sits in a pull request, and the "
                "question stays open for months because nobody went back to it.",
        solved="Before the task was closed, <code>dg check</code> was clean. Afterwards it warns "
               "that T04 was evidence for D06 and D06 is still unsettled, and it keeps warning until "
               "someone decides D06 or removes the link. It is the same check the commit hook and "
               "every coding-agent session run.",
        read_full="The check is what turns drift into a failure. The fuller example settles D06 to "
                  "clear the warning, then breaks the graph by hand the way a bad merge might. Settling "
                  "the question clears the finding. A store a merge broke, reopened with nothing "
                  "propagated, is caught by the same check, and <code>dg repair</code> stages exactly "
                  "the propagation the reopen would have.",
        hl_quick=[(r"^✓ 8 vertices, 6 edges; 11 tasks, all invariants hold$", "clean"),
                  (r"\[evidence_unharvested\]", "the work reported; the answer was never recorded")],
        hl_full=[(r"^\$ dg decide D06", "harvest it"),
                 (r"\[propagation\]", "the merge left a contradiction"),
                 (r"^deny:", "and the commit gate refuses"),
                 (r"DECIDED → PROVISIONAL, resting on D02", "repair stages what the reopen would have")],
    ),
    "14-agent-loop": dict(
        problem="The team wants a coding agent to take on some of notelit's backlog: pick a ready "
                "task, do it, and record what it did. Two things worry them. The agent might pick "
                "work that is not ready, or work someone else already holds. And it might settle open "
                "questions on a hunch, which is exactly where the team wants a person, or finished "
                "evidence, behind the answer.",
        solved="The agent got a name of its own, read the ready list, and took T04 under that name. "
               "It finished T04 and then settled D06, which it was allowed to do only because its "
               "own finished task was the evidence for that question: with "
               "<code>DG_DECIDE=evidence</code>, an answer without finished evidence behind it is "
               "refused. <code>dg-agent list</code> shows who holds what.",
        read_full="The limits, tested: an answer with no evidence behind it, a write outside the "
                  "project, and an agent that runs out of time. That last agent is a four-line shell "
                  "script standing in for a coding agent, which <code>dg-agent run</code> starts like "
                  "any other command. Every policy is an environment variable and every one is a "
                  "refusal, not a habit. A budget is real when the launcher is the agent's parent: the "
                  "child is stopped and what it held is parked under its own name, for a person to "
                  "land.",
        hl_quick=[(r"^\$ name=\$\(dg-agent claim\)", "a name from the tool, never invented"),
                  (r"▸ \$ dg apply --mine", "publish the claim so others see it"),
                  (r"DG_DECIDE=evidence dg decide D06", "allowed: T04 finished and backs it"),
                  (r"^ready T05, T09, T11", "T09 was released by the answer")],
        hl_full=[(r"^DG_DECIDE\s+evidence", "what is in force, named"),
                 (r"D03's evidence has not finished \(T06\)", "the refusal"),
                 (r"^ask: \$DG_WRITE=launch", "outside the scope: stop and ask"),
                 (r"was stopped at its budget", None),
                 (r"^staged park of T05 as", "parked under the agent's own name"),
                 (r"T05\s+PARKED.*budget spent", None)],
    ),
    "15-fanout": dict(
        problem="Now the team wants several agents working on notelit at once. Two ready tasks can "
                "still clash. The corruption detector, T04, and a new measurement of how often an "
                "index really corrupts, T13, are both evidence for the same question, D06, so two "
                "agents working on them would each try to answer D06 their own way. And with several "
                "agents writing to one graph, a person needs to review each agent's changes on their "
                "own rather than all at once.",
        solved="<code>dg task independent</code> held T13 back because it shares D06 with T04, and "
               "setup would launch three agents for the four asked. Both agents' changes went to one "
               "tray, each marked with its name. A plain <code>dg apply</code> refused while another "
               "writer's changes were in it, and <code>dg apply --agent</code> took one agent's "
               "changes after review.",
        read_full="<code>dg-agent setup</code> writes the prompt, the launcher and the remit they were "
                  "both generated from. Each agent is assigned a first task, chosen so that no two "
                  "agents' tasks collide at the seam. Most of the prompt is the graph: the chain behind "
                  "each focus id, pasted verbatim, which a fresh context could not reconstruct. Only "
                  "three answers are yours. The <code>--settings</code> on each launch line is Claude "
                  "Code's own sandbox, the <code>host</code> floor. Asked for <code>--host "
                  "opencode</code>, setup refuses that floor, because opencode has no sandbox to hand "
                  "it to, and asks for <code>--floor bwrap</code> instead.",
        hl_quick=[(r"^INDEPENDENT  3 of 4 ready can run side by side", "the set a fan-out hands out"),
                  (r"^  T13 cannot join: shares D06 with T04", "the pair, and the decision they meet on"),
                  (r"^· assigned T04, T05, T11 — one agent each", "one task per agent"),
                  (r"^· 4 agents asked for, 3 independent task\(s\) ready: launching 3", "fewer agents, not a collision"),
                  (r"by agile-bearing", "every op says who staged it"),
                  (r"^✗ nothing written — 4 staged op\(s\) belong to", "the refusal is the review"),
                  (r"^\$ dg apply --agent", "take one proposal, leave the rest")],
        hl_full=[(r"^contributor", "one word for six policies"),
                 (r"^INDEPENDENT  3 of 3 ready can run side by side", "no two of these move a decision the other stands on"),
                 (r"^· assigned T04, T05 — one agent each", "one task per agent, from that set"),
                 (r"^for t in T04 T05; do", "the launcher: one child per assigned task"),
                 (r"^wrote fanout/scout.md", None),
                 (r"^## Why this work exists", "pasted from the graph"),
                 (r'"decide": "evidence"', "the remit, as data"),
                 (r"dg-agent run --floor-applied --plan fanout/env.json", "process mode: one child per agent, every rule enforced"),
                 (r"^! T04 and T13 may collide: both name D06, and each would move it", "a hand roster: obeyed, and the pair named"),
                 (r"^! mode session: the agents are spawned by the session, so these are advisory", "session mode: what becomes advisory"),
                 (r"the confinement floor — no `dg-agent run` parent", None),
                 (r"^cleared 2 op\(s\) staged by", "turned down; nothing else touched")],
    ),
    "16-session": dict(
        problem="A coding agent starts every session knowing nothing about notelit's history. Pasting "
                "the context in by hand gets forgotten half the time, and an agent that does not know "
                "where the index lives was settled in June will happily argue it again. Agents also "
                "commit, and a commit can record the graph in a state that contradicts itself.",
        solved="With the plugin installed, <code>dg brief</code> is added to the start of every "
               "Claude Code or opencode session: the open questions, what each waits on, the work, "
               "and whether the check is clean. Before a commit the plugin asks <code>dg "
               "gate</code>. Here the graph is consistent, so the answer is allow.",
        read_full="The plugin turns habits into mechanisms. The fuller example breaks the graph and "
                  "tries to commit. A slash command is three lines around a <code>dg</code> call, the "
                  "same file on both hosts. The gate is host-neutral too: one verdict, relayed by every "
                  "adapter.",
        hl_quick=[(r"^FRONTIER \(4\)", "what the session reads first"),
                  (r"^CHECK: clean", None),
                  (r"^allow$", "a clean graph may be committed")],
        hl_full=[(r"^!`dg brief`", "the whole command"),
                 (r"^deny: The decision graph is not valid", "quoting the rule and the fix"),
                 (r"^allow$", "repaired; committable again"),
                 (r"^commit$", "the words a host watches for")],
    ),
    "17-integrate": dict(
        problem="A colleague cloned notelit's repository to work offline for a week, and in their "
                "copy they added a question under the sync question: which files should sync ignore? "
                "Now their branch has to come back. Letting git merge the decisions file as text "
                "would work this time, but a text merge can silently combine two different answers to "
                "one question, or produce a file that breaks the graph's rules, and nobody would "
                "notice for months.",
        solved="<code>dg integrate</code> read the colleague's branch as a list of changes, checked "
               "each against this graph, and held them in quarantine without writing anything. After "
               "a look they were adopted into the tray and applied like local changes.",
        read_full="The harder case: both copies answered the sync question, differently. When both "
                  "clones answered the same question, the op is contested and nothing lands until "
                  "somebody picks: theirs, ours, or split into two questions that were worded as one.",
        hl_quick=[(r"^2 op\(s\) from colleague/main", None),
                  (r"^quarantined in .dgraph-incoming.json", "read before anything is yours"),
                  (r"^adopted 2 op\(s\)", None)],
        hl_full=[(r"contested", "two answers to one question"),
                 (r"--take <ref>.*--keep <ref>", "the three ways out")],
    ),
    "18-precommit": dict(
        problem="notelit runs a search when you press Enter. Someone asks whether it could search as "
                "you type instead, updating the results on every keystroke. That depends on speed: if "
                "a search takes a third of a second, typing stutters. So the team agrees to measure "
                "first. The risk is a familiar one. Once the number arrives, it is tempting to "
                "reinterpret what fast enough meant, so that the result supports what people wanted "
                "all along.",
        solved="The rule for deciding went into the question before anyone measured: under 100 ms "
               "means search as you type, over means wait for Enter. The benchmark task carried its "
               "own definition of done. When the result came in at 340 ms, <code>dg</code> showed "
               "each commitment back at the moment it could be bent: the definition of done as the "
               "task was closed, the rule beside the result, and the rule again above the answer "
               "being written. The team answered <em>wait for Enter</em>, with a falsifier saying "
               "when that should change. Nothing here judged; a person did.",
        read_full="<b>Months later a faster index lands, and the benchmark drops to 60 ms.</b> The "
                  "falsifier said that would overturn <em>wait for Enter</em>, but a falsifier is a "
                  "sentence, and a sentence only works if somebody rereads it. A <em>probe</em> is the "
                  "same condition written so a program can check it: here, a pattern that matches a "
                  "result under 100 ms in <code>bench/search.md</code>. The program that checks a kind "
                  "of probe is called a <em>domain</em>, a small plugin found by the kind's prefix, "
                  "<code>grep</code> in <code>grep.matches</code>. <code>dg</code> ships only "
                  "<code>prose</code>, which shows a condition without judging it; the "
                  "<code>grep</code> domain is forty lines kept with this page, and a model for writing "
                  "your own. The graph stores a file and a pattern, never a command to run. <code>dg "
                  "probe</code> reports the verdict and exits non-zero when a falsifier has come true, "
                  "and does nothing else: reopening stays a person's call. What can run unattended is "
                  "the check itself. The pytest plugin, under <code>--decision-graph-probe</code>, "
                  "turns a fired probe into a failing test, so the benchmark job fails and names the "
                  "decision. Three guards come with it. A probe on an open question is appended and "
                  "dated, never edited, so a rule rewritten to fit the evidence stays visible. A kind "
                  "no installed domain handles is shown rather than refused. And <code>dg check</code> "
                  "never runs a probe, so a commit hook's verdict does not depend on what that machine "
                  "has installed.",
        hl_quick=[(r"^│ Time a search over the 50k-note set\. p95 under 100 ms", "the fork, before any evidence"),
                  (r"^done when: the p95 over 50k notes", "read back before the outcome is asked for"),
                  (r"^\s+T12 DONE — p95 340 ms", "the evidence, beside the rule"),
                  (r"^\s+verdict\s+unjudged\s*$", "nothing here judges; you do"),
                  (r"^│ rule for settling: Time a search over", "read back above the answer"),
                  (r"--falsifier 'p95 under 100 ms", "the third pre-commitment, on the answer")],
        hl_full=[(r"verdict\s+holds\s+no line of bench/search\.md matches", "judged: 340 is three digits"),
                 (r"^p95: 60 ms  prefix", "the world moved"),
                 (r"verdict\s+fired\s+bench/search\.md: p95: 60 ms", "the falsifier came true"),
                 (r"^1 presented · fired 1", "a non-zero exit and no op: the reopen is yours"),
                 (r"^FAILED tests/test_graph\.py::test_decision_graph_probe\[probe_fired\]", "the benchmark job fails, naming the decision"),
                 (r"entry 2; the earlier 1 stay", "appended, never edited"),
                 (r"\(live\)", "the last entry is the one that applies"),
                 (r"^--probe: a probe's kind is <domain>\.<name>", "the shape, refused at the door"),
                 (r"no installed domain claims `pytest\.`", "presented, not evaluated, never an error")],
    ),
    "19-staged-readers": dict(
        problem="Someone proposes a new question under the sync question: when two machines change "
                "the same note, whose edit wins? They stage it without applying it, because they want "
                "the team to look first. If a proposal were invisible until applied, nobody could "
                "review it where it belongs in the graph. If it looked exactly like a settled record, "
                "people would treat it as decided.",
        solved="The tree, the single-record view and the list of open questions all show the "
               "proposed question, D09, in place, and each marks it as staged. Nothing is in the "
               "stored graph yet: <code>dg apply</code> would write it, and dropping it would leave "
               "no trace.",
        read_full="Nothing reaches a store until <code>dg apply</code>. Until then every reader — the "
                  "frontier, the tree, one node, a path, a search — shows the store <em>plus</em> the "
                  "tray and marks what came from it, because a proposal has to be visible as one. Two "
                  "things read the store alone, on purpose. <code>dg export</code> is what <code>dg "
                  "import</code> reads back, so a proposal must never arrive in another project as a "
                  "stored fact. <code>dg check</code> is what the commit gate runs, so a staged fix can "
                  "never make a broken store pass; <code>--staged</code> is how you ask what the tray "
                  "would leave, as a diff.",
        hl_quick=[(r"D09 .*\(staged\)", "in the tree, marked"),
                  (r"staged — proposed in the tray, not applied", "and on the node itself"),
                  (r"D09\s+OPEN.*staged", "and in the frontier")],
        hl_full=[(r"D09 .*\(staged\)", "the last hop of the chain"),
                 (r"^unknown vertex D09", "export: the record alone"),
                 (r"^✓ .*all invariants hold", "the store, as the gate sees it"),
                 (r"^✓ as staged, all invariants hold", "the graph the tray would produce, judged"),
                 (r"^✓ applied", None)],
    ),
    "20-domains": dict(
        problem="notelit runs a search when you press Enter. Suppose the team has since made search "
                "much faster and switched to searching as you type, so results update on every "
                "keystroke. They wrote down what would make them switch back: a search taking 100 ms "
                "or more, because then typing stutters. <b>That has to stay true in two places.</b> A "
                "nightly benchmark on the build server searches a test collection of 50,000 notes, "
                "and a second one runs on the slowest laptop anyone on the team uses, because that is "
                "where a slow search is felt first. Each writes last night's result to a file. If "
                "either number reaches 100 ms, someone's typing stutters and the decision should be "
                "revisited. In practice nobody reads those files against a decision made months ago, "
                "so a slowdown can go unnoticed for weeks. <code>dg</code> can attach the condition "
                "to the decision in a form a program checks, so it is checked every night along with "
                "the benchmarks.",
        solved="The decision carried a <em>probe</em>: its falsifier written as a check a program "
               "can run. A probe names its kind, such as <code>grep.matches</code>, and the plugin "
               "that runs that kind is called a <em>domain</em>; the <code>grep</code> domain here "
               "is forty lines kept with this page. This probe has two halves, one per benchmark "
               "file, each asking whether the file shows a search of 100 ms or more, and "
               "<code>core.all_of</code>, the one kind <code>dg</code> understands by itself, joins "
               "them: the answer holds only while both halves hold. The first night both machines "
               "were fast. Three weeks later the laptop crossed the line while the server stayed "
               "fast, and <code>dg probe</code> reported that the answer no longer holds, named the "
               "laptop's file, and exited with an error, which is what fails the nightly job. "
               "Reopening the decision is still a person's call.",
        read_full="<b>Not every check is as cheap as reading a file.</b> A second answer, that results "
                  "are ranked by how recently each note changed, rests on the laptop staying fast and "
                  "on a ranking test that keeps passing. Reading a file takes milliseconds, so those "
                  "checks can run on every commit. Running the test suite takes minutes and belongs in "
                  "the nightly job, and the plugin that runs tests is not installed on every machine. "
                  "So the team needs to run only some kinds of check at a time, and to know what "
                  "happens to a check a machine cannot perform. <code>--domain</code> runs only the "
                  "probes one domain handles. A probe made of two halves is still one condition, so it "
                  "is included whenever either half's domain is asked for. A half that no installed "
                  "domain can check leaves the whole probe <em>unjudged</em>, never passed, and the "
                  "missing plugin is named once at the bottom. Each domain runs in its own process "
                  "under a deadline, so a plugin that hangs cannot hang the job: <code>grep</code> "
                  "declares five seconds, the default is sixty, and <code>--timeout</code> overrides "
                  "both. And a script that names a domain nothing uses, or passes an empty name because "
                  "a variable was unset, is refused rather than quietly checking nothing or everything.",
        hl_quick=[(r"^p95: 85 ms", "the slower machine, still under 100 ms"),
                  (r"verdict\s+holds\s+no line of bench/server\.md", "both halves clean, so the answer holds"),
                  (r"^p95: 140 ms", "the laptop slipped"),
                  (r"^bench/laptop\.md: p95: 140 ms", "one half is enough, and it is named"),
                  (r"^1 presented · fired 1", "a non-zero exit fails the nightly job")],
        hl_full=[(r"^2 presented · fired 0 · holds 1 · unjudged 1", "--domain grep reaches both decisions"),
                 (r"verdict\s+unjudged\s+no line of bench/laptop\.md", "one half checked, one not: unjudged, never holds"),
                 (r"^`pytest\.` not judged here — 1 record\(s\)", "the missing domain, named once"),
                 (r"did not answer within 0\.001s", "the deadline at the door wins"),
                 (r"^no pre-commitment is under `rocq\.`; prefixes here", "refused, naming what the graph does use"),
                 (r"Invalid value for .--domain.: a blank name", "an empty name checks nothing, never everything")],
    ),
}


PROJECT_INTRO = "<div class=\"intro\" id=\"project\">\n<p class=\"read\"><b>Every example happens in one imaginary project, notelit.</b> notelit is a small command-line tool for searching your notes: you point it at a folder of markdown files, it builds a search index, and you ask it questions from the terminal. A few people have been building it since May.</p>\n<p class=\"read\">By September they have answered four questions. Notes stay plain markdown files, one file per note, so the folder is the database. Search uses SQLite's built-in full-text engine, FTS5, an answer they already had to change once, when the first design got slow with many notebooks. The index file sits next to the notes, is rebuilt on demand, and is never synced. And notelit has no interactive screen: every command prints plain text. Four questions are still open: how two machines share one notes folder, what notelit should do when its index file is corrupted, which embedding model should power a smarter <em>semantic</em> search, and how results are ranked once it exists. Eleven pieces of work hang off those answers: two done, one in progress, one parked, one abandoned, and six still to do.</p>\n<p class=\"read\"><b>Their problem is the one every long-lived project has.</b> The answers live in chat threads, pull requests and people's memory. Nobody can say which answer another one depends on, what evidence would make the team change its mind, or which work is quietly waiting on a question nobody has answered. When an answer changes, what was built on it does not change with it. <code>dg</code> keeps the questions, the answers, what each rests on and the work that follows as two small files beside the code, where a tool can check them.</p>\n<p class=\"read\">Each example below starts from this same notelit, describes one situation the team runs into, and shows what <code>dg</code> does about it. Every record is listed under <a href=\"#seed\">the seed project</a> at the end.</p>\n</div>\n"

PART_INTRO = {
    "agents": """
<div class="intro" id="agents-overview">
<p class="read">An agent is a process running <code>dg</code> with <code>DG_AGENT</code> set to a name the tool handed out. Everything it stages is stamped with that name and lands in the shared tray; nothing reaches a store until somebody applies it. Around that one fact sit four roles and two ways of running, and the recipes below assume you can tell them apart.</p>
<p class="read"><b>Which of the team's agents is actually held in?</b> Most of notelit's agents run in Claude Code, one teammate prefers opencode, and someone wants a plain script to stage benchmark results. <code>dg-agent setup</code> expects a coding agent, a language model working through Claude Code or opencode: it writes a prompt only a model reads and a launcher that starts one of the two, and the model and its login come from the host, never from <code>dg</code>. What holds an agent in comes in three layers. <code>dg</code>'s own rules bind every process, because every stage and every apply goes through <code>dg</code>. The gate, meaning the write scope, the allowed commands and the commit check, runs only when a host's hook asks <code>dg gate</code> before a tool call. The floor is an operating-system sandbox under the tools, and only Claude Code has one a launcher can configure. opencode's permission system approves or refuses tool calls, which is the gate's layer rather than a floor, so an opencode agent is confined only by bubblewrap around its process, on Linux.</p>
<table class="tbl"><tr><th></th><th>Claude Code</th><th>opencode</th><th>a script of your own</th></tr>
<tr><td><code>dg</code>'s own rules</td><td>yes</td><td>yes</td><td>yes</td></tr>
<tr><td>the budget</td><td>yes</td><td>yes</td><td>yes</td></tr>
<tr><td>the gate</td><td>every tool call</td><td>every tool call, except <code>task</code> subagents and <code>patch</code> targets</td><td>only if the script calls <code>dg gate</code></td></tr>
<tr><td>the floor</td><td><code>host</code> or <code>bwrap</code></td><td><code>bwrap</code>, Linux only</td><td><code>bwrap</code>, Linux only</td></tr>
</table>
<p class="read">A script runs under <code>dg-agent run -- ./script</code>, as the stand-in in <a href="#14-agent-loop">14</a> does, but <code>setup</code> does not generate that, and the script has to keep to its remit by itself: a request the remit forbids fails, and the script should expect it to. An agent spawned in <code>--mode session</code> has no floor on either host. And native Windows has no floor at all: Claude Code's sandbox runs on macOS, Linux and WSL2, and bubblewrap only on Linux.</p>
<table class="tbl roles">
<tr><th>role</th><th>what it is</th><th>governs</th><th>if absent</th></tr>
<tr><td><b>agent</b></td><td>a process with <code>DG_AGENT=&lt;name&gt;</code>, the name from <code>dg-agent claim</code> or given by <code>dg-agent run</code>. Every policy below is a refusal for it</td><td>its own proposal</td><td>—</td></tr>
<tr><td><b>supervisor</b></td><td>whoever has <em>no</em> <code>DG_AGENT</code>: you at a terminal, or the session that launched the run. Not started; simply is</td><td>the <em>record</em>: reads the tray afterwards, <code>dg apply --agent &lt;name&gt;</code> one proposal at a time</td><td>ops wait in the tray; nothing is lost</td></tr>
<tr><td><b>gate</b></td><td><code>dg gate</code>: one verdict per shell command or write, <em>allow · warn · ask · deny</em>, relayed by the Claude Code and opencode adapters with no policy of their own</td><td>the <em>machine</em>, one action at a time</td><td>—</td></tr>
<tr><td><b>broker</b></td><td>a process you start beside the run, <code>dg-agent broker</code>. During the run it answers the <em>ask</em> verdicts an agent blocks on, at a terminal or relayed into a session</td><td>the <em>machine</em>: consent, never the tray</td><td>every <em>ask</em> becomes a refusal nobody chose</td></tr>
<tr><td><b>floor</b></td><td>an operating-system sandbox under the tools that makes both stores read-only for the agent: Claude Code's own sandbox (<code>host</code>), or bubblewrap around the whole process, on Linux (<code>bwrap</code>). The one boundary that is not cooperative</td><td>what the agent can touch at all</td><td>the gate is all that judges, and a relayed verdict is logged <code>relayed</code>, not <code>person</code></td></tr>
</table>
<figure class="flows">
<svg viewBox="0 0 980 300" width="980" height="300" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="process mode and session mode">
<defs><marker id="fl-ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto"><path d="M0,1 L9,5 L0,9 z" fill="#6b7280"/></marker></defs>
<g class="lane">
<text class="lt" x="16" y="22">process mode — the default: every rule enforced</text>
<rect class="fb you" x="16" y="40" width="150" height="40" rx="7"/><text x="91" y="57" text-anchor="middle">you, or a session</text><text class="sub" x="91" y="72" text-anchor="middle">the supervisor</text>
<rect class="fb" x="16" y="110" width="150" height="40" rx="7"/><text x="91" y="127" text-anchor="middle">dg-agent setup</text><text class="sub" x="91" y="142" text-anchor="middle">scout.md · launch.sh · env.json</text>
<rect class="fb" x="16" y="180" width="150" height="40" rx="7"/><text x="91" y="197" text-anchor="middle">./fanout/launch.sh</text><text class="sub" x="91" y="212" text-anchor="middle">checks env, asks for a broker</text>
<rect class="fb run" x="206" y="180" width="160" height="40" rx="7"/><text x="286" y="197" text-anchor="middle">dg-agent run  ×N</text><text class="sub" x="286" y="212" text-anchor="middle">parent: name · floor · budget · scope</text>
<rect class="fb ag" x="406" y="180" width="130" height="40" rx="7"/><text x="471" y="197" text-anchor="middle">agent</text><text class="sub" x="471" y="212" text-anchor="middle">claude -p · opencode run</text>
<rect class="fb" x="406" y="250" width="130" height="36" rx="7"/><text x="471" y="272" text-anchor="middle">dg gate → ask?</text>
<rect class="fb br" x="206" y="250" width="160" height="36" rx="7"/><text x="286" y="266" text-anchor="middle">dg-agent broker</text><text class="sub" x="286" y="280" text-anchor="middle">allow / deny, during the run</text>
<rect class="fb tray" x="406" y="110" width="130" height="40" rx="7"/><text x="471" y="127" text-anchor="middle">the tray</text><text class="sub" x="471" y="142" text-anchor="middle">every op stamped by name</text>
<path class="fe" d="M91,80 L91,110"/><path class="fe" d="M91,150 L91,180"/><path class="fe" d="M166,200 L206,200"/><path class="fe" d="M366,200 L406,200"/>
<path class="fe" d="M471,180 L471,150"/><path class="fe" d="M471,220 L471,250"/><path class="fe" d="M406,268 L366,268"/>
<path class="fe" d="M406,130 C300,130 200,60 166,60"/><text class="sub" x="290" y="92" text-anchor="middle">dg pending · dg apply --agent</text>
</g>
<g class="lane">
<text class="lt" x="576" y="22">session mode — for /dg:fanout: the rules become advisory</text>
<rect class="fb you" x="576" y="40" width="150" height="40" rx="7"/><text x="651" y="57" text-anchor="middle">the session</text><text class="sub" x="651" y="72" text-anchor="middle">supervisor and launcher</text>
<rect class="fb" x="576" y="110" width="150" height="40" rx="7"/><text x="651" y="127" text-anchor="middle">setup --mode session</text><text class="sub" x="651" y="142" text-anchor="middle">same prompt; no launcher</text>
<rect class="fb ag adv" x="576" y="180" width="150" height="40" rx="7"/><text x="651" y="197" text-anchor="middle">subagents  ×N</text><text class="sub" x="651" y="212" text-anchor="middle">spawned by the session's own tool</text>
<rect class="fb tray" x="796" y="110" width="150" height="40" rx="7"/><text x="871" y="127" text-anchor="middle">the tray</text><text class="sub" x="871" y="142" text-anchor="middle">only if DG_AGENT is prefixed</text>
<path class="fe" d="M651,80 L651,110"/><path class="fe" d="M651,150 L651,180"/><path class="fe" d="M726,190 C760,190 780,150 796,140"/>
<text class="warn" x="576" y="248">advisory here: the name, the floor, the budget,</text>
<text class="warn" x="576" y="264">and a relayed verdict's claim to be a person's.</text>
<text class="sub" x="576" y="284">Prefer process mode with a relayed broker unless you have a reason.</text>
</g>
</svg>
</figure>
<p class="read"><b>Where each agent begins is computed, not written.</b> Two ready tasks never block each other, but they can meet at the seam between the stores: both are evidence for one decision, or one is evidence for a decision the other rests on. Two agents sent there collide &mdash; the second <code>close</code> is refused at the tray, or one agent's finished work turns <em>provisional</em> under the decision the other moved. So <code>dg-agent setup</code> takes the ready tasks in id order, keeps each one that collides with nothing already kept, and assigns one per agent; <code>dg task independent</code> shows that set on its own, with the pair holding every task out. Fewer independent tasks than agents launches fewer agents and names the pair to break; nothing ready launches the agents to read the frontier. The set is <em>maximal</em>, not maximum: that nothing can be added is a fact a reader checks by reading pairs, and that no larger set exists is not. A task is where an agent <em>starts</em>: when it is done the agent reads the frontier and carries on, so the run still absorbs a queue that moves. <code>--roster</code> names the tasks by hand instead, and is obeyed, with a colliding pair said rather than refused.</p>
<p class="read">What an agent may do is a handful of environment variables, each read by <code>dg</code> at stage time and each a refusal rather than a habit. A <em>preset</em> is one word that sets them all; <code>dg-agent env</code> prints what is actually in force, naming a fallback as a fallback.</p>
<table class="tbl policies">
<tr><th>variable</th><th>values</th><th>what it limits</th><th>scout</th><th>contributor</th><th>maintainer</th></tr>
<tr><td><code>DG_DECIDE</code></td><td>open · evidence · never</td><td>what it may <em>settle</em>: anything, only what a finished evidence task backs, or nothing</td><td>never</td><td>evidence</td><td>open</td></tr>
<tr><td><code>DG_APPLY</code></td><td>own · never</td><td>whether it writes its own staged ops, or only proposes for a person to apply</td><td>never</td><td>own</td><td>own</td></tr>
<tr><td><code>DG_WRITE</code></td><td>open · launch</td><td>where it may write without asking: anywhere, or the project and /tmp</td><td>launch</td><td>launch</td><td>launch</td></tr>
<tr><td><code>DG_EXEC_ALLOW</code></td><td>program names</td><td>what it may run unasked; anything else is an <em>ask</em> for the broker</td><td>readers only</td><td>+ build tools</td><td>+ build tools</td></tr>
<tr><td><code>DG_BUDGET</code></td><td>30m · 1800 · infinite</td><td>how long before what it holds is parked and handed back</td><td colspan="3">set per run, not by the remit</td></tr>
<tr><td><code>DG_TERSE</code> · <code>DG_AREA</code></td><td>on/off · open/strict</td><td>how long a field may be; whether it may file under a new area</td><td colspan="3">on · open</td></tr>
<tr><td><code>DG_CONFINE</code></td><td>require · off</td><td>whether a floor must be present below the tool layer</td><td colspan="3">require, where one is available</td></tr>
</table>
<p class="read">The tool's own words for all of this are in the recipes: <code>dg task independent</code>, the assignment, <code>dg-agent presets</code> and both <code>setup</code> modes in <a href="#15-fanout">15</a>, <code>dg-agent env</code> and the refusals in <a href="#14-agent-loop">14</a>, the gate in <a href="#16-session">16</a>.</p>
</div>
""",
}

STATUS_LEGEND = [
    ("decision", "DECIDED", "answered; carries a falsifier"),
    ("decision", "OPEN", "not yet answered"),
    ("decision", "PROVISIONAL", "answered, but a premise is under review"),
    ("decision", "REOPENED", "answer superseded; kept"),
    ("task", "TODO", "outstanding; ready when prerequisites and premises are settled"),
    ("task", "DOING", "somebody holds it"),
    ("task", "DONE", "finished; records an outcome"),
    ("task", "PARKED", "put down; everything downstream still waits"),
    ("task", "DROPPED", "given up; what waited on it is released"),
]

# --------------------------------------------------------------------------
# Transcripts
# --------------------------------------------------------------------------

CMD_RE = re.compile(r"^(?:(?P<who>[a-z]+-[a-z]+) ▸ )?\$ (?P<cmd>.*)$")
NOTE_RE = re.compile(r"^# (?P<text>.*)$")
ID_RE = re.compile(r"\b([DT]\d\d)\b")


@dataclass
class Step:
    cmd: str
    who: str | None
    note: str | None
    lines: list[str] = field(default_factory=list)
    snap: int = 0            # snapshot index after this command
    exit: int = 0
    ids: set[str] = field(default_factory=set)
    mode: str = "none"       # diff | query | none


def parse_transcript(text: str) -> list[Step]:
    steps: list[Step] = []
    pending_note = None
    n = 0
    for line in text.splitlines():
        m = CMD_RE.match(line)
        if m:
            n += 1
            steps.append(Step(cmd=m.group("cmd"), who=m.group("who"), note=pending_note, snap=n))
            pending_note = None
            continue
        m = NOTE_RE.match(line)
        if m and _is_note(line, steps):
            pending_note = (pending_note + " " if pending_note else "") + m.group("text")
            continue
        if steps:
            steps[-1].lines.append(line)
    for s in steps:
        for ln in s.lines:
            em = re.match(r"^\[exit (\d+)\]$", ln)
            if em:
                s.exit = int(em.group(1))
            s.ids.update(ID_RE.findall(ln))
    return steps


def _is_note(line: str, steps: list[Step]) -> bool:
    """A `# ` line at the top level is narration; inside a `cat` of a file it is content."""
    if not steps:
        return True
    last = steps[-1]
    if last.cmd.startswith(("cat ", "sed ")):
        return False
    return True


# --------------------------------------------------------------------------
# Snapshots and the pictures drawn from them
# --------------------------------------------------------------------------

@dataclass
class State:
    decisions: dict
    tasks: dict

    def key(self) -> str:
        d = self.decisions or {}
        t = self.tasks or {}
        canon = json.dumps({
            "v": [(v["id"], v["status"], v["title"]) for v in d.get("vertices", [])],
            "e": [(e["from"], tuple(e["to"]), bool(e.get("active"))) for e in d.get("edges", [])],
            "t": [(x["id"], x["status"], x["title"], tuple(x.get("because", [])), x.get("evidence_for"))
                  for x in t.get("tasks", [])],
            "te": [(e["from"], tuple(e["to"]), e["kind"]) for e in t.get("edges", [])],
            "r": [(k, bool(v.get("ready"))) for k, v in sorted((t.get("derived") or {}).items())],
        }, sort_keys=True)
        return hashlib.sha1(canon.encode()).hexdigest()[:10]


def load_snapshots(snapdir: Path) -> dict[int, State]:
    out: dict[int, State] = {}
    for f in sorted(snapdir.glob("*.decisions.json")):
        n = int(f.name.split(".")[0])
        out.setdefault(n, State({}, {})).decisions = json.loads(f.read_text())
    for f in sorted(snapdir.glob("*.tasks.json")):
        n = int(f.name.split(".")[0])
        out.setdefault(n, State({}, {})).tasks = json.loads(f.read_text())
    return out


def _present(state: State, ids: set[str]) -> set[str]:
    have = {v["id"] for v in (state.decisions or {}).get("vertices", [])} | \
           {t["id"] for t in (state.tasks or {}).get("tasks", [])}
    return ids & have


def seed_state() -> State:
    return State(json.loads((SEED / "decisions.json").read_text()),
                 json.loads((SEED / "tasks.json").read_text()))


NODE_W, NODE_H, COL_GAP, ROW_GAP = 186, 42, 40, 12
ARROWS = {"dep": "#6b7280", "new": "#17a34a", "gone": "#dc2626", "because": "#9aa3af", "evidence": "#0e9f8e"}


def arrow_defs() -> str:
    """One arrowhead per edge colour, defined once for the whole page.

    Chrome does not draw `context-stroke`, and a marker defined inside each
    inline SVG is found by id in the *first* one — which is a hidden frame,
    and a marker in a display:none subtree is not rendered. So the page
    carries one visible, zero-height SVG of definitions that every picture
    refers to.
    """
    return ('<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>' + "".join(
        f'<marker id="ah-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" '
        f'markerUnits="userSpaceOnUse" orient="auto"><path d="M0,1 L9,5 L0,9 z" fill="{colour}"/></marker>'
        for name, colour in ARROWS.items()) + '</defs></svg>')


def _depths(ids: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    succ: dict[str, list[str]] = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for a, b in edges:
        if a in succ and b in indeg:
            succ[a].append(b)
            indeg[b] += 1
    depth = {i: 0 for i in ids}
    order = [i for i in ids if indeg[i] == 0]
    seen = set()
    while order:
        i = order.pop(0)
        if i in seen:
            continue
        seen.add(i)
        for j in succ[i]:
            depth[j] = max(depth[j], depth[i] + 1)
            indeg[j] -= 1
            if indeg[j] == 0:
                order.append(j)
    return depth


def _layout(ids: list[str], edges: list[tuple[str, str]], y0: int) -> tuple[dict[str, tuple[int, int]], int]:
    depth = _depths(ids, edges)
    cols: dict[int, list[str]] = {}
    for i in sorted(ids):
        cols.setdefault(depth[i], []).append(i)
    pos = {}
    height = 0
    for c, members in cols.items():
        for r, i in enumerate(members):
            pos[i] = (10 + c * (NODE_W + COL_GAP), y0 + r * (NODE_H + ROW_GAP))
        height = max(height, len(members) * (NODE_H + ROW_GAP))
    return pos, height


def _wrap(s: str, n: int = 19) -> list[str]:
    """Two lines of about `n` characters; the second ends in an ellipsis if cut."""
    words = s.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= n:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
            if len(lines) == 2:
                break
    if len(lines) < 2 and cur:
        lines.append(cur)
    if len(lines) == 2 and " ".join(lines) != s:
        lines[1] = (lines[1][: n - 1].rstrip() if len(lines[1]) > n - 1 else lines[1]) + "…"
    for k, ln in enumerate(lines):
        if len(ln) > n + 2:
            lines[k] = ln[: n + 1] + "…"
    return lines


def _task_ready(t: dict, derived: dict) -> bool:
    d = derived.get(t["id"]) or {}
    return bool(d.get("ready"))


def draw(state: State, before: State | None, lit: set[str] | None) -> str:
    """One SVG of both graphs and the seam between them.

    `before` marks what changed since it: added records get a green ring,
    removed ones are drawn as ghosts, changed statuses an amber ring, new and
    gone edges a glow — and everything the step did not touch is faded, so
    the change is the picture rather than a detail in it.
    `lit` dims everything not in it — the query as a selection.
    """
    d = state.decisions or {"vertices": [], "edges": []}
    t = state.tasks or {"tasks": [], "edges": [], "derived": {}}
    bd = (before.decisions if before else None) or {"vertices": [], "edges": []}
    bt = (before.tasks if before else None) or {"tasks": [], "edges": [], "derived": {}}

    dv = {v["id"]: v for v in d.get("vertices", [])}
    bdv = {v["id"]: v for v in bd.get("vertices", [])}
    tv = {x["id"]: x for x in t.get("tasks", [])}
    btv = {x["id"]: x for x in bt.get("tasks", [])}
    dedges = [(e["from"], to, bool(e.get("active", True))) for e in d.get("edges", []) for to in e["to"]]
    active_now = {(a, b) for a, b, act in dedges if act}
    bdedges = {(e["from"], to) for e in bd.get("edges", []) for to in e["to"] if e.get("active", True)}
    tedges = [(e["from"], to, e["kind"]) for e in t.get("edges", []) for to in e["to"]]
    cur_t = set(tedges)
    btedges = {(e["from"], to, e["kind"]) for e in bt.get("edges", []) for to in e["to"]}
    derived = t.get("derived") or {}
    bderived = bt.get("derived") or {}

    # ---- what changed, before anything is drawn ---------------------------
    ghosts_d = {i: v for i, v in bdv.items() if i not in dv}
    ghosts_t = {i: v for i, v in btv.items() if i not in tv}
    marks: dict[str, str] = {}
    new_d, gone_d, new_t, gone_t = set(), set(), set(), set()
    seam_new: set[tuple[str, str, str]] = set()
    seam_gone: set[tuple[str, str, str]] = set()
    if before:
        for i, v in dv.items():
            if i not in bdv:
                marks[i] = "added"
            elif bdv[i]["status"] != v["status"]:
                marks[i] = "changed"
        for i, x in tv.items():
            if i not in btv:
                marks[i] = "added"
            elif btv[i]["status"] != x["status"]:
                marks[i] = "changed"
            elif x["status"] == "TODO" and _task_ready(x, derived) != _task_ready(btv[i], bderived):
                marks[i] = "changed"
        for i in list(ghosts_d) + list(ghosts_t):
            marks[i] = "removed"
        new_d = {(a, b) for a, b in active_now if (a, b) not in bdedges}
        gone_d = {(a, b) for a, b in bdedges if (a, b) not in active_now}
        new_t = {e for e in cur_t if e not in btedges}
        gone_t = {e for e in btedges if e not in cur_t}
        for i, x in tv.items():
            was = btv.get(i) or {}
            for dd in x.get("because", []):
                if dd not in was.get("because", []):
                    seam_new.add((i, dd, "because"))
            for dd in was.get("because", []):
                if dd not in x.get("because", []):
                    seam_gone.add((i, dd, "because"))
            if x.get("evidence_for") != was.get("evidence_for"):
                if x.get("evidence_for"):
                    seam_new.add((i, x["evidence_for"], "evidence"))
                if was.get("evidence_for"):
                    seam_gone.add((i, was["evidence_for"], "evidence"))
    focus = set(marks)
    for a, b in new_d | gone_d:
        focus |= {a, b}
    for a, b, _k in new_t | gone_t | seam_new | seam_gone:
        focus |= {a, b}

    def fade(i: str) -> str:
        if lit is not None:
            return "" if i in lit else " dim"
        if before and focus:
            return "" if i in focus else " ctx"
        return ""

    # ---- layout ------------------------------------------------------------
    all_d = list(dv) + list(ghosts_d)
    all_t = list(tv) + list(ghosts_t)
    dpos, dh = _layout(all_d, sorted(active_now | bdedges), 26)
    band_gap = 44
    ty0 = 26 + max(dh, NODE_H + ROW_GAP) + band_gap
    tpos, th = _layout(all_t, [(a, b) for a, b, k in tedges] + [(a, b) for a, b, k in sorted(btedges)], ty0)
    width = max([x + NODE_W + 10 for x, _ in list(dpos.values()) + list(tpos.values())] + [320])
    height = ty0 + max(th, NODE_H + ROW_GAP) + 4

    out = []
    out.append(f'<svg class="graph" viewBox="0 0 {width} {height}" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" '
               f'role="img" aria-label="the two graphs after this step">')

    out.append(f'<text class="band" x="10" y="16">decisions</text>')
    out.append(f'<text class="band" x="10" y="{ty0 - 12}">tasks</text>')
    out.append(f'<line class="bandline" x1="0" y1="{ty0 - 26}" x2="{width}" y2="{ty0 - 26}"/>')

    def edge(p1, p2, cls, horizontal=True, tip=""):
        (x1, y1), (x2, y2) = p1, p2
        if horizontal:
            sx, sy = x1 + NODE_W, y1 + NODE_H / 2
            ex, ey = x2, y2 + NODE_H / 2
            c = (ex - sx) / 2
            dpath = f"M{sx},{sy} C{sx + c},{sy} {ex - c},{ey} {ex},{ey}"
        else:
            sx, sy = x1 + NODE_W / 2, y1
            ex, ey = x2 + NODE_W / 2, y2 + NODE_H
            c = (sy - ey) / 2
            dpath = f"M{sx},{sy} C{sx},{sy - c} {ex},{ey + c} {ex},{ey}"
        glow = ""
        if " new" in cls or " gone" in cls:
            glow = f'<path class="glow{" gone" if " gone" in cls else ""}" d="{dpath}"/>'
        kind = ("new" if " new" in cls else "gone" if " gone" in cls
                else "because" if "because" in cls else "evidence" if "evidence" in cls else "dep")
        if " new" in cls:
            tip += '<small class="chg">added in this step</small>'
        elif " gone" in cls:
            tip += '<small class="chg">removed in this step</small>'
        hit = f'<path class="hit" d="{dpath}" data-tip="{html.escape(tip, quote=True)}"/>' if tip else ""
        return f'{glow}<path class="{cls}" d="{dpath}" marker-end="url(#ah-{kind})"/>{hit}'

    payload = {}
    for e in d.get("edges", []):
        if e.get("active", True):
            for to in e["to"]:
                payload[(e["from"], to)] = e
    E = lambda s: html.escape(str(s))

    def dep_tip(a, b):
        e = payload.get((a, b), {})
        head = f'<b>{a} → {b}</b>dependency: {b} rests on {a}'
        if "answer" in e:
            body = (f'<div class="kv"><i>answer</i>{E(e["answer"])}</div>'
                    f'<div class="kv"><i>falsifier</i>{E(e.get("falsifier", "—"))}</div>'
                    f'<div class="kv"><i>source</i>{E(e.get("source", "—"))} · {E(e.get("date", ""))}</div>')
        else:
            body = '<small>bare: the premise is not settled yet, so this edge carries no answer</small>'
        return head + body

    # decision edges: active, then the ones that are gone
    for a, b, act in dedges:
        if act and a in dpos and b in dpos:
            new = (a, b) in new_d
            out.append(edge(dpos[a], dpos[b], "e dep" + (" new" if new else fade(a) + fade(b)), tip=dep_tip(a, b)))
    for a, b in sorted(gone_d):
        if a in dpos and b in dpos:
            out.append(edge(dpos[a], dpos[b], "e dep gone", tip=f'<b>{a} → {b}</b>dependency: {b} rested on {a}'))
    # task edges
    def task_tip(a, b, k):
        if k == "precedes":
            return f'<b>{a} → {b}</b>prerequisite: {a} must be resolved before {b} can start'
        return f'<b>{a} ⇢ {b}</b>provenance: doing {a} turned {b} up. Blocks nothing'
    for a, b, k in tedges:
        if a in tpos and b in tpos:
            new = (a, b, k) in new_t
            base = "e " + ("pre" if k == "precedes" else "prov")
            out.append(edge(tpos[a], tpos[b], base + (" new" if new else fade(a) + fade(b)), tip=task_tip(a, b, k)))
    for a, b, k in sorted(gone_t):
        if a in tpos and b in tpos:
            out.append(edge(tpos[a], tpos[b], "e gone " + ("pre" if k == "precedes" else "prov"), tip=task_tip(a, b, k)))
    # the seam
    def seam_tip(i, dd, k):
        if k == "because":
            st = dv.get(dd, {}).get("status", "")
            waits = " — and waits on it while the question is open" if st not in ("DECIDED",) else ""
            return f'<b>{i} ⇠ {dd}</b>because: {i} exists because of {dd}\'s answer{waits}'
        return f'<b>{i} → {dd}</b>evidence for: what {i} produces will settle {dd}'
    for i, x in tv.items():
        for dd in x.get("because", []):
            if dd in dpos:
                new = (i, dd, "because") in seam_new
                out.append(edge(tpos[i], dpos[dd], "e because" + (" new" if new else fade(i) + fade(dd)), horizontal=False, tip=seam_tip(i, dd, "because")))
        ev = x.get("evidence_for")
        if ev and ev in dpos:
            new = (i, ev, "evidence") in seam_new
            out.append(edge(tpos[i], dpos[ev], "e evidence" + (" new" if new else fade(i) + fade(ev)), horizontal=False, tip=seam_tip(i, ev, "evidence")))
    for i, dd, k in sorted(seam_gone):
        if i in tpos and dd in dpos:
            out.append(edge(tpos[i], dpos[dd], f"e {k} gone", horizontal=False, tip=seam_tip(i, dd, k)))

    areas = {**{v["id"]: v.get("area", "") for v in list(dv.values()) + list(ghosts_d.values())},
             **{x["id"]: x.get("area", "") for x in list(tv.values()) + list(ghosts_t.values())}}

    def node_tip(i, kind, status, title, ghost):
        tip = f'<b>{i}</b>{E(title)}<small>{E(status)}{" · removed in this step" if ghost else ""}'
        tip += f' · {E(areas[i])}</small>' if areas.get(i) else '</small>'
        if kind == "d":
            e = next((e for (a, _b), e in payload.items() if a == i and "answer" in e), None)
            if e:
                tip += (f'<div class="kv"><i>answer</i>{E(e["answer"])}</div>'
                        f'<div class="kv"><i>falsifier</i>{E(e.get("falsifier", "—"))}</div>'
                        f'<div class="kv"><i>source</i>{E(e.get("source", "—"))} · {E(e.get("date", ""))}</div>')
            hist = (d.get("derived") or {}).get(i, {}).get("history") or []
            if hist:
                tip += f'<div class="kv"><i>superseded</i>{len(hist)} earlier answer{"s" if len(hist) > 1 else ""}: “{E(hist[-1].get("answer", ""))}”</div>'
        else:
            x = tv.get(i) or btv.get(i) or {}
            dd = derived.get(i) or {}
            if x.get("because"):
                tip += f'<div class="kv"><i>because</i>{", ".join(x["because"])}</div>'
            if x.get("evidence_for"):
                tip += f'<div class="kv"><i>evidence for</i>{x["evidence_for"]}</div>'
            if dd.get("waiting_on") or (dd.get("cross") or {}).get("gating"):
                w = list(dd.get("waiting_on") or []) + [g["id"] if isinstance(g, dict) else str(g) for g in (dd.get("cross") or {}).get("gating") or []]
                tip += f'<div class="kv"><i>waits on</i>{E(", ".join(w))}</div>'
            if x.get("outcome"):
                tip += f'<div class="kv"><i>outcome</i>{E(x["outcome"])} · {E(x.get("done", ""))}</div>'
            if x.get("stops"):
                st = x["stops"][-1]
                tip += f'<div class="kv"><i>stopped</i>{E(st.get("why", ""))} · {E(st.get("date", ""))}</div>'
        return tip

    def node(i, x, y, kind, status, title, extra_cls, ghost=False):
        cls = f"n {kind} s-{status.lower()}{extra_cls}"
        if ghost:
            cls += " ghost"
        lines = _wrap(title)
        label = "".join(f'<text class="t" x="40" y="{18 + k * 14}">{html.escape(ln)}</text>' for k, ln in enumerate(lines))
        out.append(f'<g class="{cls}" transform="translate({x},{y})" data-id="{i}" '
                   f'data-tip="{html.escape(node_tip(i, kind, status, title, ghost), quote=True)}">'
                   f'<rect class="ring" x="-3" y="-3" width="{NODE_W + 6}" height="{NODE_H + 6}" rx="8"/>'
                   f'<rect class="box" width="{NODE_W}" height="{NODE_H}" rx="6"/>'
                   f'<text class="id" x="8" y="18">{i}</text>{label}</g>')

    for i, v in dv.items():
        x, y = dpos[i]
        node(i, x, y, "d", v["status"], v["title"], (" " + marks[i] if i in marks else "") + fade(i))
    for i, v in ghosts_d.items():
        x, y = dpos[i]
        node(i, x, y, "d", v["status"], v["title"], " removed", ghost=True)
    for i, x_ in tv.items():
        x, y = tpos[i]
        st = x_["status"]
        extra = ""
        if st == "TODO":
            extra += " ready" if _task_ready(x_, derived) else " blocked"
        node(i, x, y, "t", st, x_["title"], extra + (" " + marks[i] if i in marks else "") + fade(i))
    for i, v in ghosts_t.items():
        x, y = tpos[i]
        node(i, x, y, "t", v["status"], v["title"], " removed", ghost=True)
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

def esc(s: str) -> str:
    return html.escape(s, quote=False)


def render_lines(lines: list[str], hl: list[tuple[re.Pattern, str | None]], hits: set[int]) -> str:
    out = []
    for ln in lines:
        cls = "ln"
        mark = ""
        if re.match(r"^\[exit \d+\]$", ln):
            cls += " exit"
        for k, (rx, note) in enumerate(hl):
            if rx.search(ln):
                if " hl" not in cls:
                    cls += " hl"
                    if note:
                        mark = f'<em class="mark">{esc(note)}</em>'
                hits.add(k)
        out.append(f'<span class="{cls}">{esc(ln) if ln else " "}{mark}</span>')
    return "\n".join(out)


def render_layer(slug: str, layer: str, hl_spec: list[tuple[str, str | None]]) -> tuple[str, list[str]]:
    text = (OUT / f"{slug}.{layer}.txt").read_text()
    steps = parse_transcript(text)
    snaps = load_snapshots(OUT / f"{slug}.{layer}")
    hl = [(re.compile(rx), note) for rx, note in hl_spec]
    hits: set[int] = set()

    # pictures: one per distinct (before, after) pair
    svgs: dict[str, str] = {}
    step_pics: list[tuple[str, str]] = []   # (svg key, mode) per step
    prev = snaps.get(0)
    for s in steps:
        cur = snaps.get(s.snap, prev)
        if cur is None:
            step_pics.append(("", "none"))
            continue
        changed = prev is not None and cur.key() != prev.key()
        if changed:
            mode = "diff"
            key = f"{prev.key()}-{cur.key()}"
            if key not in svgs:
                svgs[key] = draw(cur, prev, None)
        elif (named := _present(cur, s.ids | set(ID_RE.findall(s.cmd)))) and not s.cmd.startswith(("cat", "sed", "ls", "git")):
            # a query: light what the step named, if the store holds it. A
            # staged-only record is not in the store yet, and a picture with
            # everything dimmed says nothing.
            mode = "query"
            key = f"{cur.key()}-q-" + hashlib.sha1(" ".join(sorted(named)).encode()).hexdigest()[:8]
            if key not in svgs:
                svgs[key] = draw(cur, None, named)
        else:
            mode = "none"
            key = cur.key()
            if key not in svgs:
                svgs[key] = draw(cur, None, None)
        s.mode = mode
        step_pics.append((key, mode))
        prev = cur
    base_key = ""
    if snaps.get(0) is not None:
        base_key = snaps[0].key()
        if base_key not in svgs:
            svgs[base_key] = draw(snaps[0], None, None)

    # which step is shown first
    initial = 0
    diff_steps = [i for i, (_, m) in enumerate(step_pics) if m == "diff"]
    query_steps = [i for i, (_, m) in enumerate(step_pics) if m == "query"]
    if diff_steps:
        initial = diff_steps[-1]
    elif query_steps:
        initial = query_steps[0]
    elif steps:
        initial = len(steps) - 1

    parts = [f'<div class="layer {layer}" data-layer="{slug}-{layer}">']
    parts.append('<div class="pane pic">')
    parts.append(f'<div class="picbar"><button type="button" class="stepbtn" data-go="-1" title="previous step">‹</button>'
                 f'<span class="picwhich"></span><button type="button" class="stepbtn" data-go="1" title="next step">›</button></div>')
    parts.append('<div class="pics">')
    parts.append('<div class="picframe empty" data-key="" hidden>no store yet</div>')
    if base_key:
        parts.append(f'<div class="picframe" data-key="{base_key}" data-step="-1">{svgs[base_key]}</div>')
    for key, svg in svgs.items():
        if key == base_key:
            continue
        parts.append(f'<div class="picframe" data-key="{key}" hidden>{svg}</div>')
    parts.append('</div></div>')

    parts.append('<div class="pane term"><pre class="tx">')
    for i, s in enumerate(steps):
        key, mode = step_pics[i] if i < len(step_pics) else ("", "none")
        who = f'<b class="who">{esc(s.who)} ▸</b> ' if s.who else ""
        note = f'<span class="note">{esc(s.note)}</span>\n' if s.note else ""
        raw_cmd = (f"{s.who} ▸ " if s.who else "") + "$ " + s.cmd
        cmd_cls, cmd_mark = "cmd", ""
        for k, (rx, mnote) in enumerate(hl):
            if rx.search(raw_cmd):
                if " hl" not in cmd_cls:
                    cmd_cls += " hl"
                    if mnote:
                        cmd_mark = f'<em class="mark">{esc(mnote)}</em>'
                hits.add(k)
        parts.append(f'<div class="step{" current" if i == initial else ""}" data-key="{key}" data-mode="{mode}" data-i="{i}">'
                     f'{note}<span class="{cmd_cls}">{who}<b class="ps">$</b> {esc(s.cmd)}{cmd_mark}</span>\n'
                     + render_lines(s.lines, hl, hits) + '</div>')
    parts.append('</pre></div></div>')
    missing = [rx for k, (rx, _) in enumerate(hl_spec) if k not in hits]
    return "".join(parts), missing


def recipe_meta(slug: str) -> tuple[str, str]:
    src = (RECIPE_DIR / f"{slug}.sh").read_text()
    q = re.search(r"^# q: (.*)$", src, re.M).group(1)
    part = re.search(r"^# part: (.*)$", src, re.M).group(1)
    return q, part


def command_index(slugs: list[str]) -> dict[str, set[str]]:
    idx: dict[str, set[str]] = {}
    for slug in slugs:
        for layer in ("quick", "full"):
            for s in parse_transcript((OUT / f"{slug}.{layer}.txt").read_text()):
                toks = s.cmd.split()
                toks = [t for t in toks if "=" not in t or t.startswith("'")]
                if not toks:
                    continue
                if toks[0].startswith("name=$(") or toks[0].startswith(("a=$(", "b=$(")):
                    toks = [toks[0].split("(", 1)[1]] + toks[1:]
                if toks[0] == "dg" and len(toks) > 1:
                    name = "dg " + toks[1]
                    if toks[1] == "task" and len(toks) > 2 and not toks[2].startswith("-"):
                        name += " " + toks[2]
                elif toks[0] == "dg-agent" and len(toks) > 1:
                    name = "dg-agent " + toks[1].rstrip(")")
                elif toks[0] == "dg":
                    name = "dg"
                else:
                    continue
                idx.setdefault(name, set()).add(slug)
    return idx


CSS = r"""
:root{--ink:#1f2328;--mut:#667085;--line:#e3e6ea;--bg:#fbfbfa;--card:#fff;--acc:#2a5db0;
--dec:#2e7d4f;--decbg:#e3f3e8;--open:#b7791f;--openbg:#fff6e0;--prov:#7c4dff;--provbg:#efe8ff;--reo:#c62828;--reobg:#ffe3e3;
--todo:#4a6fa5;--doing:#1e5bc6;--doingbg:#dde9ff;--done:#2e7d4f;--donebg:#e3f3e8;--park:#b7791f;--parkbg:#fff6e0;--drop:#8a8f98;--dropbg:#f0f1f3;
--new:#17a34a;--chg:#f59e0b;--gone:#dc2626;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
*{box-sizing:border-box}

body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--sans)}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
code{font:.92em var(--mono);background:#eef0f3;padding:0 .3em;border-radius:4px}
.wrap{display:grid;grid-template-columns:250px minmax(0,1fr);gap:0;min-height:100vh}
nav{position:sticky;top:0;height:100vh;overflow:auto;border-right:1px solid var(--line);background:#fff;padding:20px 16px;font-size:13.5px}
nav h1{font-size:17px;margin:0 0 2px}nav .tag{color:var(--mut);font-size:12px;margin-bottom:14px}
nav .part{margin:14px 0 4px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut)}
nav ol{list-style:none;margin:0;padding:0}nav li{margin:0}nav li a{display:block;padding:4px 8px;border-radius:6px;color:var(--ink);line-height:1.3}
nav li a:hover{background:#f2f4f7;text-decoration:none}nav li.active a{background:#e7eefc;color:var(--acc)}
nav li a .n{color:var(--mut);font-variant-numeric:tabular-nums;margin-right:6px;font-size:12px}
nav li.overview a{font-style:italic;color:#3b4149}
nav .rail{position:relative;height:4px;background:#eef0f3;border-radius:2px;margin:10px 0 4px}nav .rail i{position:absolute;left:0;top:0;bottom:0;background:var(--acc);border-radius:2px;width:0}
nav .eta{color:var(--mut);font-size:12px}
main{padding:28px 40px 80px;max-width:1100px}
header.top{margin-bottom:26px}
header.top h1{font-size:30px;margin:0 0 6px;letter-spacing:-.01em}header.top p{max-width:70ch;margin:.3em 0;color:#3b4149}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;margin:14px 0 0;font-size:12.5px;color:var(--mut)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{display:inline-block;width:22px;height:12px;border-radius:4px;border:1.6px solid}
.sw.dec{background:var(--decbg);border-color:var(--dec)}.sw.open{background:var(--openbg);border-color:var(--open);border-style:dashed}
.sw.prov{background:var(--provbg);border-color:var(--prov)}.sw.reo{background:var(--reobg);border-color:var(--reo)}
.sw.todo{background:#fff;border-color:var(--todo)}.sw.blocked{background:#fff;border-color:#a3acb9;border-style:dashed}
.sw.doing{background:var(--doingbg);border-color:var(--doing)}.sw.done{background:var(--donebg);border-color:var(--done)}
.sw.park{background:var(--parkbg);border-color:var(--park)}.sw.drop{background:var(--dropbg);border-color:var(--drop)}
.sw.new{border-color:var(--new);box-shadow:0 0 0 2px #bbf7d0}.sw.chg{border-color:var(--chg);box-shadow:0 0 0 2px #fde68a}.sw.gone{border-color:var(--gone);border-style:dashed;opacity:.6}
.eline{display:inline-block;width:26px;border-top:2px solid #6b7280;vertical-align:middle}.eline.prov{border-top-style:dotted}
.eline.because{border-top:2px dashed #9aa3af}.eline.evidence{border-top:2px dashed #0e9f8e}
section.recipe{margin:0 0 44px;padding-top:8px;border-top:1px solid var(--line)}
section.recipe .eyebrow{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);margin:14px 0 2px}
section.recipe h2{font-size:22px;margin:0 0 10px;letter-spacing:-.01em}
.read{max-width:78ch;margin:0 0 14px;color:#2b3138}
.situation,.solved{max-width:78ch;margin:4px 0 14px;padding:10px 14px 10px 16px;border-radius:0 10px 10px 0;color:#2b3138}
.situation{border-left:3px solid #d8b86a;background:#fcf8ee}.solved{border-left:3px solid #6cbf8a;background:#f2faf5;margin-top:14px}
.situation p,.solved p{margin:0}.lab{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);margin:0 0 4px}
.layer{display:grid;grid-template-columns:minmax(0,1fr);gap:12px;align-items:start}
.pane{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.pic{position:sticky;top:8px;z-index:2;box-shadow:0 6px 18px -12px rgba(0,0,0,.35)}
.picbar{display:flex;align-items:center;gap:6px;padding:6px 8px;border-bottom:1px solid var(--line);font-size:12px;color:var(--mut)}
.picwhich{flex:1;text-align:center;font-family:var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stepbtn{border:1px solid var(--line);background:#fff;border-radius:6px;width:24px;height:22px;cursor:pointer;color:var(--ink);font-size:15px;line-height:1}
.stepbtn:hover{background:#f2f4f7}
.pics{padding:8px 10px}
.picframe svg{max-width:100%;width:auto;height:auto;max-height:calc(45vh - 44px);display:block;margin:0 auto}
.picframe.empty{color:var(--mut);font:12px var(--mono);text-align:center;padding:24px 0}
pre.tx{margin:0;padding:8px 0;font:11.5px/1.45 var(--mono);white-space:pre;overflow:auto;tab-size:4;max-height:calc(55vh - 40px);overscroll-behavior:contain}
/* Picture and transcript together fit one viewport, so both can sit at its top: the picture is
   capped, the transcript scrolls inside its box, and the wheel at that box's end does not carry
   on into the page (overscroll-behavior). Wide enough, the two sit side by side instead. */
@media (min-width:1600px){main{max-width:none}.layer{grid-template-columns:minmax(0,700px) minmax(0,1fr)}.pane.term{order:1}.pane.pic{order:2}.picframe svg{max-height:calc(100vh - 66px)}pre.tx{max-height:calc(100vh - 18px)}}
.step{padding:4px 12px 6px;border-left:3px solid transparent;cursor:pointer;margin:2px 0}
.step:hover{background:#fafbfc}.step.current{border-left-color:var(--acc);background:#f6f9ff}
.step .cmd{display:block;color:#0f172a;font-weight:600;margin:2px 0;white-space:pre-wrap;word-break:break-word}.step .ps{color:var(--acc)}.who{color:#7c4dff;font-weight:700}
.step .note{display:block;color:#6d5b17;background:#fff9e6;border-radius:4px;padding:2px 6px;margin:2px 0 4px;white-space:pre-wrap;font-family:var(--sans);font-size:12.5px}
.ln{display:block;color:#3b4149;min-height:1.45em}
.ln.hl{background:#fff3c4;color:#1f2328;border-radius:2px;margin-left:-6px;padding-left:6px}
.ln.exit{color:#b91c1c}
.mark{display:block;text-align:right;color:#8a6d00;font-style:normal;font-family:var(--sans);font-size:11.5px;line-height:1.3;padding:0 6px 2px 0}
.mark::before{content:"↑ "}
details.fold{margin-top:14px;border:1px dashed #cfd4da;border-radius:10px;padding:0 14px}
details.fold>summary{cursor:pointer;padding:10px 0;font-weight:600;color:#2b3138;list-style:none}
details.fold>summary::before{content:"▸ ";color:var(--mut)}details.fold[open]>summary::before{content:"▾ "}
details.fold>summary small{font-weight:400;color:var(--mut);margin-left:8px}
details.fold .read{margin-top:2px}
details.fold .layer{margin-bottom:14px}
details.src{margin:8px 0 14px}details.src summary{cursor:pointer;color:var(--mut);font-size:13px}
details.src pre{font:11.5px/1.45 var(--mono);background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px 12px;overflow-x:auto;margin:6px 0}
.repro{font-size:13px;color:var(--mut);margin:8px 0 0}
/* the graph */
svg.graph text{font:11.5px var(--mono)}svg.graph .band{fill:var(--mut);font:600 10.5px var(--sans);letter-spacing:.08em;text-transform:uppercase}
svg.graph .bandline{stroke:var(--line)}
svg.graph .n .box{fill:#fff;stroke:#a3acb9;stroke-width:1.5}svg.graph .n .ring{fill:none;stroke:none;stroke-width:3}
svg.graph .n .id{font-weight:700;fill:#1f2328}svg.graph .n .t{fill:#3b4149}
svg.graph .n.d.s-decided .box{fill:var(--decbg);stroke:var(--dec)}
svg.graph .n.d.s-open .box{fill:var(--openbg);stroke:var(--open);stroke-dasharray:4 3}
svg.graph .n.d.s-provisional .box{fill:var(--provbg);stroke:var(--prov)}
svg.graph .n.d.s-reopened .box{fill:var(--reobg);stroke:var(--reo)}
svg.graph .n.d[class*="s-blocked"] .box{fill:#f6f7f9;stroke:#a3acb9;stroke-dasharray:4 3}
svg.graph .n.t.s-todo .box{fill:#fff;stroke:var(--todo)}svg.graph .n.t.s-todo.blocked .box{stroke:#a3acb9;stroke-dasharray:4 3}
svg.graph .n.t.s-doing .box{fill:var(--doingbg);stroke:var(--doing)}
svg.graph .n.t.s-done .box{fill:var(--donebg);stroke:var(--done)}
svg.graph .n.t.s-parked .box{fill:var(--parkbg);stroke:var(--park)}
svg.graph .n.t.s-dropped .box{fill:var(--dropbg);stroke:var(--drop)}svg.graph .n.t.s-dropped text{fill:#8a8f98;text-decoration:line-through}
svg.graph .n.added .ring{stroke:var(--new)}svg.graph .n.changed .ring{stroke:var(--chg)}
svg.graph .n.ghost .box{fill:none;stroke:var(--gone);stroke-dasharray:3 3}svg.graph .n.ghost text{fill:var(--gone);opacity:.7}
svg.graph .n.dim{opacity:.22}svg.graph .e.dim{opacity:.15}
svg.graph .n.ctx{opacity:.28}svg.graph .e.ctx{opacity:.16}
svg.graph .glow{fill:none;stroke:#86efac;stroke-width:9;stroke-linecap:round;opacity:.75}svg.graph .glow.gone{stroke:#fecaca}
svg.graph .n.added .ring,svg.graph .n.changed .ring{stroke-width:4}
svg.graph .e{fill:none;stroke:#6b7280;stroke-width:1.4}
svg.graph .e.prov{stroke-dasharray:2 3}svg.graph .e.because{stroke:#9aa3af;stroke-dasharray:5 4;stroke-width:1.2}
svg.graph .e.evidence{stroke:#0e9f8e;stroke-dasharray:5 4;stroke-width:1.2}
svg.graph .e.new{stroke:var(--new);stroke-width:2.6}svg.graph .e.gone{stroke:var(--gone);stroke-dasharray:3 3;opacity:.7}
#tip{position:fixed;z-index:50;pointer-events:none;background:#1f2328;color:#fff;font:12.5px/1.4 var(--sans);padding:7px 10px;border-radius:7px;max-width:380px;box-shadow:0 8px 24px -8px rgba(0,0,0,.5)}
#tip b{font-family:var(--mono);margin-right:6px}#tip small{display:block;color:#c9ced6;margin-top:2px}
#tip .kv{margin-top:5px;padding-top:5px;border-top:1px solid #3a3f47;color:#e8eaee}#tip .kv i{display:block;font-style:normal;color:#9aa3af;font-size:11px;letter-spacing:.04em;text-transform:uppercase}
#tip .chg{color:#86efac}
svg.graph .hit{fill:none;stroke:transparent;stroke-width:12;pointer-events:stroke;cursor:default}
svg.graph .n{cursor:default}
/* part intros */
.intro{border:1px solid var(--line);border-radius:12px;background:#fff;padding:14px 18px 6px;margin:10px 0 26px}
.intro .read{max-width:90ch}
.intro table.tbl{font-size:13px}.intro table.tbl td:first-child{white-space:nowrap}
.intro table.roles td:nth-child(2){max-width:46ch}
.intro figure.flows{margin:14px 0;overflow-x:auto}
.intro figure.flows svg{display:block;max-width:100%;height:auto;font:12px var(--sans)}
.flows .lt{font:600 12.5px var(--sans);fill:var(--ink)}.flows .sub{font:10.5px var(--sans);fill:var(--mut)}.flows .warn{font:600 11.5px var(--sans);fill:#b45309}
.flows .fb{fill:#f8fafc;stroke:#a3acb9;stroke-width:1.3}.flows .fb.you{fill:#fff7e0;stroke:#b7791f}.flows .fb.run{fill:#e7eefc;stroke:#2a5db0}
.flows .fb.ag{fill:#efe8ff;stroke:#7c4dff}.flows .fb.ag.adv{stroke-dasharray:4 3}.flows .fb.br{fill:#e3f3e8;stroke:#2e7d4f}.flows .fb.tray{fill:#fff;stroke:#1f2328}
.flows text{fill:var(--ink)}.flows .fe{fill:none;stroke:#6b7280;stroke-width:1.4;marker-end:url(#fl-ah)}
/* appendix */
.appendix{margin-top:40px;border-top:2px solid var(--line);padding-top:12px}
.appendix h2{font-size:22px}.appendix h3{font-size:16px;margin:22px 0 6px}
table.tbl{border-collapse:collapse;font-size:13px;width:100%;margin:8px 0}
table.tbl th,table.tbl td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
table.tbl th{color:var(--mut);font-weight:600;font-size:12px}
.chip{display:inline-block;font:600 10.5px/1.6 var(--mono);padding:0 6px;border-radius:5px;border:1.2px solid}
.chip.decided,.chip.done{background:var(--decbg);border-color:var(--dec);color:#1d5a37}
.chip.open{background:var(--openbg);border-color:var(--open);color:#7a4f0f}.chip.todo{background:#fff;border-color:var(--todo);color:#2c4a75}
.chip.doing{background:var(--doingbg);border-color:var(--doing);color:#123f8f}.chip.parked{background:var(--parkbg);border-color:var(--park);color:#7a4f0f}
.chip.dropped{background:var(--dropbg);border-color:var(--drop);color:#555}.chip.provisional{background:var(--provbg);border-color:var(--prov);color:#4b2ba6}
.chip.reopened{background:var(--reobg);border-color:var(--reo);color:#8b1a1a}
.seedpic{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px;max-width:980px}
.cmdidx{columns:2;column-gap:28px;font-size:13.5px}.cmdidx div{break-inside:avoid;padding:2px 0;border-bottom:1px solid #f0f1f3}
.cmdidx code{background:none;padding:0;font-weight:600}.cmdidx .to{color:var(--mut);font-size:12px;margin-left:8px}
@media (max-width:1000px){.wrap{grid-template-columns:1fr}nav{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line)}.pic{position:static}main{padding:20px}}
"""

JS = r"""
(function(){
  function show(layer, step){
    var steps = layer.querySelectorAll('.step');
    var pics = layer.querySelectorAll('.picframe');
    var which = layer.querySelector('.picwhich');
    var key, label;
    if (step < 0){ key = pics[0] && pics[0].dataset.step === '-1' ? pics[0].dataset.key : ''; label = 'before anything ran'; }
    else { var s = steps[step]; key = s.dataset.key; label = 'after step ' + (step+1) + ' of ' + steps.length + ' · ' + (s.dataset.mode==='diff'?'what changed':(s.dataset.mode==='query'?'what it named':'no change')); }
    steps.forEach(function(s,i){ s.classList.toggle('current', i===step); });
    var found=false;
    pics.forEach(function(p){ var on = p.dataset.key===key && !found; if(on) found=true; p.hidden = !on; });
    if(!found && pics.length){ pics[0].hidden=false; }
    layer.dataset.step = step;
    if(which) which.textContent = label;
  }
  document.querySelectorAll('.layer').forEach(function(layer){
    var steps = layer.querySelectorAll('.step');
    var init = -1;
    steps.forEach(function(s,i){ if(s.classList.contains('current')) init=i; });
    show(layer, init);
    steps.forEach(function(s,i){ s.addEventListener('click', function(){ show(layer,i); }); });
    layer.querySelectorAll('.stepbtn').forEach(function(b){
      b.addEventListener('click', function(){
        var cur = parseInt(layer.dataset.step||'-1',10) + parseInt(b.dataset.go,10);
        cur = Math.max(-1, Math.min(steps.length-1, cur)); show(layer, cur);
      });
    });
  });
  // hover a node: the whole title, its status and area
  var tip = document.createElement('div'); tip.id = 'tip'; tip.hidden = true; document.body.appendChild(tip);
  document.addEventListener('mouseover', function(e){
    var n = e.target.closest && e.target.closest('svg.graph [data-tip]'); if(!n) return;
    tip.innerHTML = n.dataset.tip;
    tip.hidden = false;
  });
  document.addEventListener('mousemove', function(e){
    if(tip.hidden) return;
    var x = e.clientX + 14, y = e.clientY + 14;
    if (x + tip.offsetWidth > innerWidth - 8) x = e.clientX - tip.offsetWidth - 10;
    if (y + tip.offsetHeight > innerHeight - 8) y = e.clientY - tip.offsetHeight - 10;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  });
  document.addEventListener('mouseout', function(e){
    var n = e.target.closest && e.target.closest('svg.graph [data-tip]'); if(n && !(e.relatedTarget && n.contains(e.relatedTarget))) tip.hidden = true;
  });
  // sidebar: which recipe is on screen, and how far along the quick path we are
  var links = {}; document.querySelectorAll('nav li[data-for]').forEach(function(li){ links[li.dataset.for]=li; });
  var secs = Array.prototype.slice.call(document.querySelectorAll('section.recipe, .intro[id]'));
  var rail = document.querySelector('nav .rail i');
  function update(){
    var y = window.scrollY + 120, cur = null;
    secs.forEach(function(s){ if (s.offsetTop <= y) cur = s; });
    Object.keys(links).forEach(function(k){ links[k].classList.toggle('active', cur && cur.id===k); });
    var recipes = secs.filter(function(s){ return s.classList.contains('recipe'); });
    if (cur && rail){ var i = recipes.indexOf(cur); if (i >= 0) rail.style.width = Math.round(100*(i+1)/recipes.length) + '%'; }
  }
  window.addEventListener('scroll', update, {passive:true}); update();
})();
"""


def page() -> str:
    slugs = sorted(RECIPES)
    problems: list[str] = []
    nav = []
    body = []
    by_part: dict[str, list[str]] = {}
    metas = {}
    for slug in slugs:
        q, part = recipe_meta(slug)
        metas[slug] = (q, part)
        by_part.setdefault(part, []).append(slug)

    nav.append('<h1>dear-guide, by example</h1><div class="tag">worked examples · every line was run</div>')
    nav.append('<div class="rail"><i></i></div><div class="eta">the quick path: about 30 minutes</div>')
    nav.append('<ol><li data-for="project" class="overview"><a href="#project"><span class="n">—</span>The project, and its problem</a></li></ol>')
    for part, (title, sub) in PARTS.items():
        if part not in by_part:
            continue
        nav.append(f'<div class="part">{title}</div><ol>')
        if part in PART_INTRO:
            nav.append(f'<li data-for="{part}-overview" class="overview"><a href="#{part}-overview"><span class="n">—</span>Who is who: roles, the two flows, what an agent may do</a></li>')
        for slug in by_part[part]:
            q, _ = metas[slug]
            nav.append(f'<li data-for="{slug}"><a href="#{slug}"><span class="n">{slug[:2]}</span>{esc(q)}</a></li>')
        nav.append('</ol>')
    nav.append('<div class="part">Appendix</div><ol>'
               '<li><a href="#seed">The seed project</a></li>'
               '<li><a href="#commands">Command index</a></li>'
               '<li><a href="#glossary">Statuses and edges</a></li>'
               '<li><a href="#made">How this page was made</a></li></ol>')

    for part, (title, sub) in PARTS.items():
        if part not in by_part:
            continue
        body.append(f'<h2 class="parthead" id="part-{part}" style="margin:36px 0 4px;font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:var(--mut)">{title} <span style="letter-spacing:0;text-transform:none;font-weight:400">— {sub}</span></h2>')
        if part in PART_INTRO:
            body.append(PART_INTRO[part])
        for slug in by_part[part]:
            q, _ = metas[slug]
            spec = RECIPES[slug]
            quick_html, miss_q = render_layer(slug, "quick", spec["hl_quick"])
            full_html, miss_f = render_layer(slug, "full", spec["hl_full"])
            for rx in miss_q:
                problems.append(f"{slug} quick: no line matches /{rx}/")
            for rx in miss_f:
                problems.append(f"{slug} full: no line matches /{rx}/")
            src = (RECIPE_DIR / f"{slug}.sh").read_text()
            body.append(f'<section class="recipe" id="{slug}">')
            body.append(f'<div class="eyebrow">{title} · {slug[:2]}</div><h2>{esc(q)}</h2>')
            body.append(f'<div class="situation"><div class="lab">The situation</div><p>{spec["problem"]}</p></div>')
            body.append(quick_html)
            body.append(f'<div class="solved"><div class="lab">What changed</div><p>{spec["solved"]}</p></div>')
            body.append(f'<details class="fold"><summary>The fuller example<small>same project, further in</small></summary>')
            body.append(f'<p class="read">{spec["read_full"]}</p>')
            body.append(full_html)
            body.append(f'<details class="src"><summary>The script that produced both transcripts</summary><pre>{esc(src)}</pre></details>')
            body.append(f'<p class="repro">Reproduce: <code>./quick-start-demo/run.sh {slug[:2]}</code> — the transcript lands in <code>out/{slug}.quick.txt</code> and <code>out/{slug}.full.txt</code>, and the project it ran against in <code>/tmp/dg-quick-start/notelit</code>.</p>')
            body.append('</details></section>')

    # appendix
    seed = seed_state()
    app = ['<div class="appendix">']
    app.append('<h2 id="seed">The seed project</h2>'
               '<p class="read">Every recipe starts from a fresh copy of <em>notelit</em>, an imaginary CLI that indexes a folder '
               'of markdown notes: eight decisions, one of them reversed, and eleven tasks covering every status. '
               'It was built with <code>dg</code> commands by <code>seed.sh</code>, then given a history by dating its records; '
               'the dates are the only hand-written data in it.</p>')
    app.append(f'<div class="seedpic">{draw(seed, None, None)}</div>')
    app.append('<h3>Decisions</h3><table class="tbl"><tr><th>id</th><th>status</th><th>question</th><th>answer · falsifier</th></tr>')
    ans = {}
    for e in seed.decisions["edges"]:
        if e.get("active") and "answer" in e:
            ans[e["from"]] = e
    for v in sorted(seed.decisions["vertices"], key=lambda v: v["id"]):
        e = ans.get(v["id"])
        a = f'{esc(e["answer"])}<br><span style="color:var(--mut)">falsifier: {esc(e["falsifier"])}</span>' if e else "—"
        app.append(f'<tr><td>{v["id"]}</td><td><span class="chip {v["status"].lower()}">{v["status"]}</span></td><td>{esc(v["title"])}</td><td>{a}</td></tr>')
    app.append('</table><h3>Tasks</h3><table class="tbl"><tr><th>id</th><th>status</th><th>work</th><th>links</th><th>record</th></tr>')
    for x in sorted(seed.tasks["tasks"], key=lambda x: x["id"]):
        links = []
        if x.get("because"):
            links.append("because " + ", ".join(x["because"]))
        if x.get("evidence_for"):
            links.append("evidence for " + x["evidence_for"])
        rec = []
        if x.get("outcome"):
            rec.append(f'{x.get("done", "")} — {esc(x["outcome"])}')
        for s in x.get("stops", []):
            rec.append(f'{s["date"]} — stopped: {esc(s["why"])}')
        app.append(f'<tr><td>{x["id"]}</td><td><span class="chip {x["status"].lower()}">{x["status"]}</span></td><td>{esc(x["title"])}</td><td>{"; ".join(links) or "—"}</td><td>{"<br>".join(rec) or "—"}</td></tr>')
    app.append('</table>')
    app.append(f'<details class="src"><summary>seed.sh</summary><pre>{esc((HERE / "seed.sh").read_text())}</pre></details>')

    idx = command_index(slugs)
    app.append('<h2 id="commands">Command index</h2><p class="read">Every command that appears above, and where.</p><div class="cmdidx">')
    for name in sorted(idx):
        to = " ".join(f'<a href="#{s}">{s[:2]}</a>' for s in sorted(idx[name]))
        app.append(f'<div><code>{esc(name)}</code><span class="to">{to}</span></div>')
    app.append('</div>')

    app.append('<h2 id="glossary">Statuses and edges</h2><table class="tbl"><tr><th>store</th><th>status</th><th>meaning</th></tr>')
    for store, st, meaning in STATUS_LEGEND:
        app.append(f'<tr><td>{store}</td><td><span class="chip {st.lower()}">{st}</span></td><td>{meaning}</td></tr>')
    app.append('</table><table class="tbl"><tr><th>edge</th><th>between</th><th>meaning</th></tr>'
               '<tr><td><span class="eline"></span> dependency</td><td>decision → decision</td><td>B rests on A. It gains the answer, falsifier, source and date when A is decided; <code>active: false</code> is a superseded answer, kept forever</td></tr>'
               '<tr><td><span class="eline"></span> precedes</td><td>task → task</td><td>a prerequisite: the task it points from must be resolved first</td></tr>'
               '<tr><td><span class="eline prov"></span> prompted</td><td>task → task</td><td>provenance: doing that task turned this one up. Blocks nothing</td></tr>'
               '<tr><td><span class="eline because"></span> because</td><td>task → decision</td><td>the work exists because of that answer, and waits on it while it is open</td></tr>'
               '<tr><td><span class="eline evidence"></span> evidence for</td><td>task → decision</td><td>the work\'s outcome will settle that question; the answer waits on it</td></tr>'
               '</table>')
    app.append('<p class="read">In the pictures: <span class="sw new"></span> added since the previous step, '
               '<span class="sw chg"></span> status changed, <span class="sw gone"></span> removed. '
               'A dashed task outline means blocked; solid means ready. Click any command in a transcript to see the graph as it stood after it.</p>')

    app.append('<h2 id="made">How this page was made</h2>'
               '<p class="read">Twenty scripts under <code>quick-start-demo/recipes/</code>, each two functions, <code>quick</code> and <code>full</code>, '
               'run by <code>run.sh</code> against a fresh copy of the seed. The runner captures each function\'s transcript and exports both stores after every command; '
               '<code>build.py</code> turns those into this file, so no command, output line or picture here was typed by hand. '
               'The highlighted lines are regular expressions that <code>tests/test_quick_start_demo.py</code> asserts against a fresh run, '
               'so if <code>dg</code> rewords a message the page points at, the test fails rather than the page going quietly wrong.</p>'
               '<p class="read">Two things the page shows but does not run: the launcher written by <code>dg-agent setup</code> is printed, not executed, '
               'because running it needs a coding-agent host; and <code>dg serve</code>, the web app, is not shown at all. '
               'Two more are stand-ins. The benchmark results in the annex are written by the recipe scripts rather than timed, since notelit is imaginary, '
               'and the <code>grep</code> domain that checks them is forty lines under <code>quick-start-demo/grep-domain/</code>, on the path for the recipes and installed nowhere. '
               'Everything else on this page ran.</p>')
    app.append('</div>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>dear-guide, by example</title><style>{CSS}</style></head>
<body>{arrow_defs()}<div class="wrap"><nav>{''.join(nav)}</nav><main>
<header class="top"><h1>dear-guide, by example</h1>
<p><code>dg</code> keeps a project's development as two linked graphs: the decisions it has settled, with what each rests on and what would overturn it, and the work that follows from them. This page is a reference of worked examples for the question <em>how do I do that with dear-guide?</em></p>
<p>Each example opens on a situation the team behind one small, imaginary project runs into. Then it shows what <code>dg</code> does about it, as a real transcript with a picture of the graph beside it, and says what changed. Read the short example of each; open <em>the fuller example</em> only where you want more. Click a command to see the graph after it.</p>
<div class="legend">
<span><i class="sw dec"></i>decided</span><span><i class="sw open"></i>open</span><span><i class="sw prov"></i>provisional</span><span><i class="sw reo"></i>reopened</span>
<span style="margin-left:10px"><i class="sw todo"></i>todo, ready</span><span><i class="sw blocked"></i>todo, blocked</span><span><i class="sw doing"></i>doing</span><span><i class="sw done"></i>done</span><span><i class="sw park"></i>parked</span><span><i class="sw drop"></i>dropped</span>
<span style="margin-left:10px"><i class="sw new"></i>added</span><span><i class="sw chg"></i>changed</span><span><i class="sw gone"></i>removed</span>
</div></header>
{PROJECT_INTRO}
{''.join(body)}
{''.join(app)}
</main></div><script>{JS}</script></body></html>
""", problems


if __name__ == "__main__":
    html_text, problems = page()
    (HERE / "index.html").write_text(html_text)
    print(f"wrote index.html ({len(html_text) // 1024} KB)")
    for p in problems:
        print("!", p)
    raise SystemExit(1 if problems else 0)
