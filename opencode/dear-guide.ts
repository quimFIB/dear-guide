/**
 * opencode adapter for the decision graph (V2).
 *
 * Translation only, exactly like `hooks/brief.py` and `hooks/precommit.py` on
 * the Claude Code side. What is worth saying at the start of a session lives in
 * `dgraph/brief.py`; whether a commit may proceed lives in `dgraph/gate.py`.
 * Both are reached by running `dg`, so this file holds no knowledge of the graph
 * and no policy about commits — which is the only reason supporting a second
 * host costs one file instead of a reimplementation.
 *
 * V2 registers each hook on the domain that owns it instead of returning a map
 * of string-keyed hooks. The skill is shared verbatim: opencode reads
 * `skills/<name>/SKILL.md` with the same `name`/`description` frontmatter, so
 * `skills/dear-guide/SKILL.md` is installed, not ported. See `opencode/README.md`.
 */

import { Plugin } from "@opencode/plugin"
import { execFile } from "node:child_process"
import { promisify } from "node:util"

const pexec = promisify(execFile)

const COMPACTED =
  "Context was just compacted. Anything settled in what was dropped that is " +
  "not in the graph below still needs recording."

/**
 * `gate.TRIGGERS`, copied because a plugin cannot import the Python package.
 * A shell command containing none of these cannot earn anything but `allow`,
 * so the gate is not consulted for one. Kept equal to the real tuple by a test
 * — it went stale once already, which is what made the removal verdict
 * unreachable from both hosts. `dg gate --triggers` prints the real one.
 */
const TRIGGERS = ["commit", "rm"]

/**
 * How long the gate is told this adapter will wait, in seconds.
 *
 * `dg gate` stopped being a pure function when the consent broker landed: it
 * can block while a person decides. **The caller owns the bound** — the gate
 * answers `deny` before it runs out, rather than the caller deciding the
 * question by giving up.
 *
 * `execFile` has a timeout of its own here, so the number is passed explicitly.
 * Claude Code's hooks gave up at 5 seconds and allowed the write, opencode
 * waited forever, and one rule with two answers by host is what the gate exists
 * to prevent. It is kept equal to `hooks/prewrite.py`'s `DEADLINE` by a test.
 */
const DEADLINE = "100"

/** The same switch both Claude Code hooks honour. Documented in the README. */
function off(): boolean {
  const v = (process.env.DG_HOOK_OFF ?? "").trim()
  return !["", "0", "false", "no"].includes(v)
}

export default Plugin.define({
  id: "dear-guide",

  async setup(ctx) {
    const directory = ctx.location.directory

    /** Sessions already given the brief. Cleared on compaction, since that is
     *  precisely when the context it provided was thrown away. */
    const briefed = new Set<string>()

    /**
     * Run `dg` and hand back both halves of the answer. One spawn site, because
     * a second would be a second set of rules about what counts as failure.
     *
     * `code` is null when `dg` could not be run at all.
     */
    const run = async (
      ...args: string[]
    ): Promise<{ code: number | null; out: string }> => {
      try {
        const { stdout } = await pexec("dg", args, {
          cwd: directory,
          timeout: (Number(DEADLINE) + 10) * 1000,
          maxBuffer: 10 * 1024 * 1024,
        })
        return { code: 0, out: stdout.toString() }
      } catch (error: any) {
        const code =
          typeof error?.code === "number"
            ? error.code
            : typeof error?.exitCode === "number"
              ? error.exitCode
              : null
        return { code, out: error?.stdout ? error.stdout.toString() : "" }
      }
    }

    /**
     * Its stdout, or null for anything at all going wrong — `dg` not installed,
     * no graph in this directory, a version too old to know the subcommand. None
     * of those is a reason to interrupt a session.
     */
    const dg = async (...args: string[]): Promise<string | null> => {
      const { code, out } = await run(...args)
      return code === 0 ? out : null
    }

    const brief = async (): Promise<string | null> => {
      const out = await dg("brief")
      return out && out.trim() ? out.trim() : null
    }

    /**
     * The brief, once per session. V2 has no session-start hook either, so the
     * first prompt is the earliest guaranteed point.
     */
    await ctx.session.hook("prompt", async (event) => {
      if (off() || !event.sessionID || briefed.has(event.sessionID)) return
      const text = await brief()
      if (!text) return
      briefed.add(event.sessionID)
      event.prompt.text = `<dear-guide>\n${text}\n</dear-guide>\n\n${event.prompt.text}`
    })

    /** Purpose-built for surviving a compaction — the analogue of Claude Code's
     *  `SessionStart` `compact` matcher. */
    await ctx.session.hook("compaction", async (event) => {
      if (off()) return
      const text = await brief()
      if (text) event.system.push({ type: "text", text: `${COMPACTED}\n\n${text}` })
    })

    /**
     * The gate: the commit gate, and the sanction on a removal.
     *
     * Throwing is opencode's way to stop a tool call *with an explanation*: the
     * message becomes the tool's error result, so the model reads the reason and
     * can act on it. `permission.ask` can force allow/deny/ask but carries no
     * reason field, and a refusal the model cannot read is a refusal it retries.
     *
     * `ask` therefore degrades to a refusal that says whose call it is. The same
     * information reaches a different decider, which is the honest translation
     * of a verdict opencode cannot express.
     *
     * KNOWN HOLE, documented rather than worked around: `tool.execute.before`
     * does not fire for tool calls made by subagents spawned through the `task`
     * tool (opencode issue #5894).
     */
    await ctx.tool.hook("execute.before", async (event) => {
      if (off()) return

      const input = (event.input ?? {}) as Record<string, any>

      /*
       * The agent write scope, relayed exactly as the commit gate below is.
       * The policy is `dgraph/limits.py` reached through `dg gate --write`, so
       * this host and Claude Code enforce one rule rather than two copies of
       * one — which is the whole reason the gate takes a thing and returns a
       * verdict instead of the adapters each deciding.
       *
       * The fast path is `$DG_AGENT` rather than a word list: a path carries
       * no substring separating an in-scope write from an out-of-scope one,
       * and the scope is never consulted for a supervisor anyway. Reads are
       * absent on purpose — an agent that cannot read the repository it is
       * reasoning about is blindfolded, not constrained.
       *
       * V2 names the target `path`; `filePath` is kept for compatibility with
       * V1-era tool schemas. `patch` is absent: it takes `patchText`, one diff
       * that may touch many files, so there is no single path to hand the gate.
       */
      const WRITERS: Record<string, string> = {
        write: "path",
        edit: "path",
      }
      const field = WRITERS[event.tool]
      if (field && (process.env.DG_AGENT ?? "").trim()) {
        const target = String(input[field] ?? input.filePath ?? "")
        if (target) {
          const { code, out } = await run("gate", "--write", target, "--deadline", DEADLINE, "--json")
          // Silent on every failure, unlike the commit gate below. An
          // unjudged commit may record a contradiction permanently; an
          // unjudged write is an ordinary file operation nobody asked to be
          // consulted about, and a notice on each would train the reader to
          // ignore the one that matters. `gate.write_verdict` fails open for
          // the same reason.
          if (code === 0) {
            let v: { verdict?: string; reason?: string }
            try {
              v = JSON.parse(out)
            } catch {
              return
            }
            if (v.verdict === "ask" || v.verdict === "deny") {
              throw new Error(
                `${v.reason ?? ""}\nThis is the user's call, not yours — ask ` +
                  `them before writing there.`,
              )
            }
          }
        }
      }

      if (event.tool !== "bash" && event.tool !== "shell") return
      const command = String(input.command ?? "")
      // A fast path, not a policy: a command containing none of these cannot
      // earn anything but `allow`, so skipping it can never hide a refusal, and
      // it keeps a subprocess out of every unrelated shell call.
      //
      // This is `gate.TRIGGERS` and must stay equal to it — `dg gate
      // --triggers` prints it, and a test asserts the two agree. It read
      // `["commit"]` for as long as commits were all the gate judged, and
      // stayed that way after `dg rm` was added, so the gate's `ask` on a
      // removal was never reached from here.
      //
      // **An agent skips the fast path entirely**, and that asymmetry is what
      // makes the command gate an allowlist rather than a denylist. Whether an
      // agent may run `cargo` depends on `$DG_EXEC_ALLOW`, not on any word in
      // the command, so no word list can be sound for it — widening one means
      // guessing more words, which is what this project refuses to do with
      // shell. A person is unaffected and pays what they paid before.
      //
      // `$DG_AGENT` is the fast path the write scope above already uses, for
      // the same reason: it is the one thing separating a session the remit
      // applies to from every ordinary use of the host.
      const owned = Boolean((process.env.DG_AGENT ?? "").trim())
      if (!owned && !TRIGGERS.some((t) => command.includes(t))) return

      // `run`, not `dg`: that collapses every failure into null, and here the
      // failures differ. `dg gate` is written never to fail open — it catches
      // everything and denies, and always exits 0 — so a nonzero exit is the
      // gate being bypassed from outside rather than a verdict of "allow".
      // `dg` missing (null, or 127) and a `dg` too old to know the subcommand
      // (2) stay silent, for the reason the brief does; anything else says so,
      // because a command that went through unchecked must not look identical
      // to a project that never had a graph.
      const { code, out } = await run(
        "gate", "--command", command, "--deadline", DEADLINE, "--json",
      )
      if (code === null || code === 127 || code === 2) return
      if (code !== 0) {
        console.error(
          `dg gate exited ${code} — this command was not checked against ` +
            `the development graph.`,
        )
        return
      }
      let verdict: { verdict?: string; reason?: string }
      try {
        verdict = JSON.parse(out)
      } catch {
        return
      }
      // Not a decision: the command runs, and the user is told one thing on the
      // way past — a generated view that has fallen behind its store. Throwing
      // would block it, which is the whole thing `warn` exists not to do, so
      // this uses the same console channel the gate-bypass message above does.
      if (verdict.verdict === "warn") {
        if (verdict.reason) console.error(verdict.reason)
        return
      }
      if (verdict.verdict === "deny") throw new Error(verdict.reason ?? "")
      if (verdict.verdict === "ask") {
        throw new Error(
          `${verdict.reason ?? ""}\nThis is the user's call, not yours — ask ` +
            `them before running it.`,
        )
      }
    })

    const controller = new AbortController()
    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
          if (event.type === "session.compacted") {
            briefed.delete((event as any).data?.sessionID)
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          console.error("dear-guide event subscription failed:", error)
        }
      }
    })()

    return () => controller.abort()
  },
})
