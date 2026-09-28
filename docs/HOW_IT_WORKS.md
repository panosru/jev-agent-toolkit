# How it works

This page explains the moving parts precisely enough to debug, extend or audit them.

## 1. The hook contract

Both Claude Code and the Codex CLI run `UserPromptSubmit` hooks: a command that receives a JSON object on
**stdin** before your message is processed, and may print a JSON object on **stdout**.

| | Claude Code | Codex CLI |
|---|---|---|
| Configured in | `~/.claude/settings.json` → `hooks.UserPromptSubmit` | `~/.codex/hooks.json` → `hooks.UserPromptSubmit` |
| Fields we read from stdin | `prompt`, `session_id` | `prompt`, `session_id` (also sent: `turn_id`, `transcript_path`, `cwd`, `hook_event_name`, `model`, `permission_mode`) |
| We print | `systemMessage` (a status line shown to you) and `hookSpecificOutput.additionalContext` (text the assistant receives) | the same shape |

Both hooks always exit 0. If they print nothing, the message proceeds untouched.

> **Codex note.** The Codex documentation lists `hookSpecificOutput.additionalContext` for some events but not
> explicitly for `UserPromptSubmit`. We verified empirically that it works there: a throwaway hook that
> injected "reply with the word BANANA" changed the model's answer to a plain prompt from `pong` to `BANANA`.
> Codex also requires you to review hooks you did not write (`/hooks`), and `codex exec` in a script should be
> given `</dev/null` so it does not wait on stdin.

## 2. What we ask Jev

**Router** (one request, two questions, so the input is billed once and the answers come back together):

```json
{
  "model": "typesafe/jev-1.13",
  "state": {"message": "<first 6,000 characters of your message>"},
  "questions": {
    "size": {"type": "choice",
             "instructions": "What is the smallest AI model size that can do the job asked for in `message` well?",
             "criteria": {"tiny": "...", "everyday": "...", "large": "...", "hardest": "..."}},
    "followup": {"type": "noul",
                 "instructions": "Is `message` a short reply that only makes sense inside an ongoing conversation ...?",
                 "criteria": {"true": "It points back at earlier messages ...", "false": "It is self-contained ..."}}
  }
}
```

`POST https://openrouter.ai/api/alpha/decisions`. The reply carries, per question, the chosen option, a
probability per option and (for `choice`) a `confidence`; a `noul` returns only a probability of "yes".
`usage.cost` gives the price of the call in dollars.

**Skill picker** (one request, one question): a `choice` whose options are *every skill name* with its one-line
description as the criterion, plus a `none_of_these` option so Jev is never forced to guess. If the catalog
is very large (more than 200 skills or roughly 60,000 characters) it is split into groups of 50, all groups are
asked in one request, and a final round picks between the group winners.

### Why the questions are written the way they are

Jev 1.13 has documented weak spots, and the wording works around them:

| Weak spot | What we do |
|---|---|
| Reads instructions literally | Each question is one narrow, literal condition; tier descriptions are concrete ("a rename, a one-line answer") |
| Poor at maths, counting and date comparison | Nothing numeric is delegated to Jev; thresholds and comparisons are ordinary code |
| Gets worse with irrelevant context | Only the message (and, for the picker, one line per skill) is sent, and it is truncated |
| Noul and Choice are not interchangeable | The router uses a choice for the size and a noul only for the follow-up test, and never compares them |
| Confidence exists for choice/score, not noul | The follow-up test uses a simple probability cutoff (0.5) |
| Can be steered by adversarial text | Its answers are advisory; floors, `force` rules and tags override it |

## 3. The router's decision procedure

In order:

1. **Off?** No `enabled` file → print nothing.
2. **Slash command or `!`?** Skipped.
3. **Leading tag?** `@tiny`, `[large]`, `@here`, `@opus`… → use that tier, no Jev call. `here` = keep in this session.
4. **Matching `force` rule?** Same, no Jev call.
5. **Ask Jev** (size + follow-up).
6. **Follow-up** (probability above `JEV_FOLLOWUP_CUTOFF`) → stay in this session.
7. **Apply `min` / `max` rules** to Jev's size. `max` only when Jev is sure; a `min` floor also applies when Jev is
   unsure (a floor is a promise, so the message runs at the floor).
8. **Unsure** (confidence below `JEV_ROUTER_MIN_CONFIDENCE`) with no floor → stay in this session.
9. **Is it already covered?**
   - explicit tag or `force` rule: covered only if the tier equals your default's tier (honoured literally, up *or* down);
   - `tiny` from Jev: never covered unless your default *is* the smallest tier (tiny jobs always go to the smallest model);
   - anything else: covered if the tier is at or below your default's tier.
10. Not covered → print the status line and inject an instruction telling the assistant to hand the whole job to
    the matching helper agent, to leave the tag out of the brief, to pass the result back verbatim, and to do it
    itself if the helper fails.

### Reading your default model (the "baseline")

- **Claude Code:** the `model` field of `~/.claude/settings.json`, matched by substring (`sonnet`, `claude-opus-5-5`,
  `opus[1m]` …). Unset or unrecognised is treated as the everyday tier.
- **Codex:** the top-level `model = "…"` line of `~/.codex/config.toml` (only the part before the first `[table]`).
  Matched by the fragments in the `SIZES` table (`luna`, `sol`, `astra`). Unset or unrecognised is the everyday tier.

The baseline is read on every message, so `/model` changes take effect immediately.

## 4. The helper agents

| | Claude Code | Codex |
|---|---|---|
| Location | `~/.claude/agents/jev-<tier>.md` | `~/.codex/agents/jev-<tier>.toml` |
| Format | Markdown with `name`, `description`, `model` front matter | TOML: `name`, `description`, `developer_instructions`, `model`, `model_reasoning_effort` |
| How the assistant starts one | the Agent tool with `subagent_type: jev-<tier>` | `spawn_agent` with `agent_type: jev-<tier>` |

The instruction the hook injects is ordinary prose ("hand the whole job to the `jev-large` helper…"). Both
assistants act on it. We confirmed the Codex path end to end: a forced `[tiny]` message produced a
`spawn_agent` call with `agent_type: "jev-tiny"`, a completed sub-agent activity, and a sub-agent thread running
on `gpt-6-luna` while the parent ran on `gpt-6-sol`.

A helper is a **fresh session**: it does not see your conversation, so the assistant briefs it with the request
and whatever context it needs. That is also why follow-ups are never delegated.

## 5. The skill picker's flow

1. Build the **catalog**: name plus a one-line description for each skill the assistant can load. Claude Code:
   `~/.claude/skills`, synced skills, enabled plugins (skills and slash commands) and a built-in list
   (`builtins.json`). Codex: `~/.codex/skills` (including the bundled `.system` skills) and a scan of the plugin
   cache. Skills flagged user-only (`disable-model-invocation: true`) are excluded, because the assistant could not
   load them anyway. Duplicates collapse to one entry.
2. On each message: skip if OFF, a slash command, `@here`, or a `force: here` rule (nothing is sent).
3. Ask Jev which single skill fits. At or above `JEV_SKILL_MIN_CONFIDENCE` (0.60) and not `none_of_these`,
   tell the assistant to load it first and to say so in one short line. Otherwise say nothing and let the
   assistant choose as usual.
4. If both hooks fire and the router delegates, the injected notes tell the assistant to have the **helper**
   load the skill, not to load it itself.

## 6. Logs

`log.jsonl` next to each script, one JSON object per message. Router fields: `ts`, `session` (first 8
characters), `size`, `confidence`, `followup`, `ms`, `cost_usd`, `jev_model`, `outcome`
(`delegated`, `self:covered`, `self:unsure`, `self:followup`, `self:here`, `skipped`, `error`), plus `agent`,
`baseline`, `via` and `note` when relevant. Picker fields: `skill`, `confidence`, `rounds`, `ms`, `cost_usd`,
`outcome` (`picked`, `none`, `unsure`, `private`, `skipped`, `error`). **Message text and keys are never
written**: tests use a canary string to make sure of it.

## 7. Design decisions and why

| Decision | Reason |
|---|---|
| Hooks **fail open** | A router that can break a session is worse than none |
| **OFF** after install | Turning it on sends your prompts to third parties; that should be a deliberate act |
| Delegate to helpers rather than "switch the model" | A hook runs after the session model is fixed; it can only instruct |
| **Baseline-aware** | Delegating work your session already handles wastes a cold start |
| Follow-ups never delegated | The helper would lack the context |
| `tiny` always delegates | The smallest model for trivial work was an explicit product choice (with a documented cost trade-off) |
| Floors apply when Jev is unsure | A rule you wrote should not silently lapse on an ambiguous message |
| Explicit tags are literal | If you say `@sonnet`, you mean Sonnet, even when your default is bigger |
| Key resolved at call time, command run without a shell | No secret at rest in this project; no injection surface in the key command |
| Standard-library Python, one file per hook | Hooks must start fast and cannot depend on an install step |
