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
 * same `name`/`description` frontmatter, so `skills/decisions/SKILL.md` is
 * installed, not ported. See `opencode/README.md`.
 */

import type { Plugin } from "@opencode-ai/plugin"

const COMPACTED =
  "Context was just compacted. Anything settled in what was dropped that is " +
  "not in the graph below still needs recording."

/** The same switch both Claude Code hooks honour. Documented in the README. */
function off(): boolean {
  const v = (process.env.DG_HOOK_OFF ?? "").trim()
  return !["", "0", "false", "no"].includes(v)
}

export const DecisionGraphPlugin: Plugin = async ({ $, directory }) => {
  /** Sessions already given the brief. Cleared on compaction, since that is
   *  precisely when the context it provided was thrown away. */
  const briefed = new Set<string>()

  /**
   * Run `dg` and return its stdout, or null for anything at all going wrong —
   * `dg` not installed, no graph in this directory, a version too old to know
   * the subcommand. None of those is a reason to interrupt a session.
   */
  const dg = async (...args: string[]): Promise<string | null> => {
    try {
      const r = await $`dg ${args}`.cwd(directory).quiet().nothrow()
      return r.exitCode === 0 ? r.stdout.toString() : null
    } catch {
      return null
    }
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
      part.text = `<decision-graph>\n${text}\n</decision-graph>\n\n${part.text}`
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
     * The commit gate.
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
      // A fast path, not a policy: `dg gate` requires the literal token, so a
      // command without it cannot be a commit. Keeps a subprocess out of every
      // unrelated shell call.
      if (!command.includes("commit")) return

      const raw = await dg("gate", "--command", command, "--json")
      if (!raw) return
      let verdict: { verdict?: string; reason?: string }
      try {
        verdict = JSON.parse(raw)
      } catch {
        return
      }
      if (verdict.verdict === "deny") throw new Error(verdict.reason ?? "")
      if (verdict.verdict === "ask") {
        throw new Error(
          `${verdict.reason ?? ""}\nThis is the user's call, not yours — ask ` +
            `them before committing.`,
        )
      }
    },
  }
}
