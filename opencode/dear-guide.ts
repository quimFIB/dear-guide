/**
 * opencode adapter for the decision graph.
 *
 * Translation only, exactly like `hooks/brief.py` and `hooks/precommit.py` on
 * the Claude Code side. What is worth saying at the start of a session lives in
 * `dgraph/brief.py`; whether a commit may proceed lives in `dgraph/gate.py`.
 * Both are reached by running `dg`, so this file holds no knowledge of the graph
 * and no policy about commits — which is the only reason supporting a second
 * host costs one file instead of a reimplementation.
 *
 * The skill is shared verbatim: opencode reads `skills/<name>/SKILL.md` with the
 * same `name`/`description` frontmatter, so `skills/dear-guide/SKILL.md` is
 * installed, not ported. See `opencode/README.md`.
 */

import type { Plugin } from "@opencode-ai/plugin"

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

/** The same switch both Claude Code hooks honour. Documented in the README. */
function off(): boolean {
  const v = (process.env.DG_HOOK_OFF ?? "").trim()
  return !["", "0", "false", "no"].includes(v)
}

export const DearGuidePlugin: Plugin = async ({ $, directory }) => {
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
      const r = await $`dg ${args}`.cwd(directory).quiet().nothrow()
      return { code: r.exitCode, out: r.stdout.toString() }
    } catch {
      return { code: null, out: "" }
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

  return {
    /**
     * The brief, once per session. opencode has no session-start hook, so the
     * first user message is the earliest guaranteed point.
     *
     * It edits the text of an existing part rather than synthesising a new one:
     * a `Part` carries ids the runtime owns, and a hand-built one is the kind of
     * thing that works until it silently does not.
     */
    "chat.message": async (input, output) => {
      if (off() || !input.sessionID || briefed.has(input.sessionID)) return
      const text = await brief()
      if (!text) return
      const part: any = output.parts.find((p: any) => p.type === "text")
      if (!part) return
      briefed.add(input.sessionID)
      part.text = `<dear-guide>\n${text}\n</dear-guide>\n\n${part.text}`
    },

    /** Purpose-built for surviving a compaction — the analogue of Claude Code's
     *  `SessionStart` `compact` matcher. */
    "experimental.session.compacting": async (_input, output) => {
      if (off()) return
      const text = await brief()
      if (text) output.context.push(`${COMPACTED}\n\n${text}`)
    },

    event: async ({ event }) => {
      if (event.type === "session.compacted") {
        briefed.delete((event as any).properties?.sessionID)
      }
    },

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
     * Known hole, documented rather than worked around: `tool.execute.before`
     * does not fire for tool calls made by subagents spawned through the `task`
     * tool (opencode issue #5894).
     */
    "tool.execute.before": async (input, output) => {
      if (off() || input.tool !== "bash") return
      const command = String((output.args as any)?.command ?? "")
      // A fast path, not a policy: a command containing none of these cannot
      // earn anything but `allow`, so skipping it can never hide a refusal, and
      // it keeps a subprocess out of every unrelated shell call.
      //
      // This is `gate.TRIGGERS` and must stay equal to it — `dg gate
      // --triggers` prints it, and a test asserts the two agree. It read
      // `["commit"]` for as long as commits were all the gate judged, and
      // stayed that way after `dg rm` was added, so the gate's `ask` on a
      // removal was never reached from here.
      if (!TRIGGERS.some((t) => command.includes(t))) return

      // `run`, not `dg`: that collapses every failure into null, and here the
      // failures differ. `dg gate` is written never to fail open — it catches
      // everything and denies, and always exits 0 — so a nonzero exit is the
      // gate being bypassed from outside rather than a verdict of "allow".
      // `dg` missing (null, or 127) and a `dg` too old to know the subcommand
      // (2) stay silent, for the reason the brief does; anything else says so,
      // because a command that went through unchecked must not look identical
      // to a project that never had a graph.
      const { code, out } = await run("gate", "--command", command, "--json")
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
    },
  }
}
