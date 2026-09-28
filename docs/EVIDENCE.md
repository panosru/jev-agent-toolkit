# Evidence

Everything measured while building this, with its limits. These are **small, hand-made samples on one
person's real installs**: they show the design behaves sensibly, they are not a benchmark, and your numbers
will differ (different skills, different wording, different day). Model behaviour is not deterministic.

Jev model used: `typesafe/jev-1.13` through OpenRouter's alpha Decisions route. Costs are the `usage.cost`
value each response reported.

## 1. Router sizing (10 hand-written messages)

The expected tier was written down *before* running. "Jev's verdict" is the tier with its confidence.

| # | Message (paraphrased) | Expected | Jev's verdict | Result |
|---|---|---|---|---|
| 1 | a one-line general-knowledge question | tiny | tiny, 100% | match |
| 2 | rename a file | tiny | tiny, 100% | match |
| 3 | email to a client moving a meeting | everyday | everyday, 99% | match |
| 4 | a short social post | everyday | everyday, 100% | match |
| 5 | build a script that reconciles invoices with bank exports | large | large, 86% | match |
| 6 | research five tools and write a comparison report | large | large, 99% | match |
| 7 | decide whether to move client hosting between two providers, weighing cost, risk, downtime, contract | hardest | hardest, 64% | match |
| 8 | design next year's pricing strategy | hardest | hardest, **58%** | right tier, below the 60% bar, so the router correctly stayed put |
| 9 | "yes do that but make it shorter" | follow-up | follow-up, 96% | match |
| 10 | "ok go with the second option" | follow-up | follow-up, 98% | match |

Total Jev cost for the ten calls: **$0.000232**. Latency 349 to 755 ms per call.

### Variance: the same text, different runs

The invoice-script message (row 5) scored **large at 86% and 88%** in two runs and **"not sure, best guess large,
35%"** in another, with identical text and the same Jev version. Treat confidence as a signal, not a guarantee, and
use tags (`@large`) when you need certainty.

### Wording sensitivity

The same request, nudged. Baseline session default: everyday tier.

| Message | Verdict |
|---|---|
| "Create a DNS A record for `app.example.com` pointing to `1.2.3.4` in Cloudflare" | tiny, 66% |
| … + "This is production and a mistake would be expensive, so be careful." | unsure, 21% |
| … + "Think this through carefully." | unsure, 55% |
| … + ", verify it propagated, update our runbook and tell the team." | everyday, 71% |
| "Quick one: …" prefix | tiny, 73% |

Short, vague phrasings of the same intent ("Create a domain in Cloudflare", "Add example.com to Cloudflare")
landed on *unsure* (48%, 33%), while specific one-shot operations (a DNS record, a storage bucket) landed on
*tiny* (63 to 67%). Jev sizes by wording, not by consequence: that is the reason floors and tags exist.

## 2. Skill picker (10 realistic requests, one 143-skill catalog)

One person's catalog (their own skills, plugins and built-ins). Expected skills were set beforehand; for the
slide-deck request either of two look-alike skills was accepted.

| # | Request (paraphrased) | Jev's pick | Confidence |
|---|---|---|---|
| 1 | redeploy a container stack and show its logs | a user-installed container-management skill | 1.00 |
| 2 | which pages are close to page one in search results | a user-installed search-performance skill | 0.84 |
| 3 | a 10-slide pitch deck | `slides` (runner-up `pptx` 0.16) | 0.78 |
| 4 | a service returns 502 since an upgrade, find why | `systematic-debugging` | 0.65 |
| 5 | put invoices into a spreadsheet with totals | `xlsx` | 1.00 |
| 6 | a dashboard page charting backup sizes | `dataviz` | 0.98 |
| 7 | stop asking permission every time a command runs | `fewer-permission-prompts` | 0.97 |
| 8 | "use Jev to sort these 40 support emails" | `jev` | 1.00 |
| 9 | check a repo's dependencies for risky or abandoned packages | `supply-chain-risk-auditor` | 1.00 |
| 10 | "what time is it in Tokyo?" | `none_of_these` | 0.97 |

**10 of 10 correct.** Total cost **$0.0026** (about $0.00026 each); latency 418 to 1,038 ms.

### Rewriting one-liners helped where picks were shaky

After sharpening five descriptions (mainly by stating what the look-alike is *not* for):

| Request | Before | After |
|---|---|---|
| the slide deck | `slides` 0.78 (`pptx` 0.16) | `pptx` **0.96** |
| the 502 investigation | `systematic-debugging` 0.66 | `systematic-debugging` **0.98** |
| stop permission prompts | `fewer-permission-prompts` 0.97 | `fewer-permission-prompts` 0.88 (`update-config` 0.11) |

The third moved down and stayed correct: two skills can both fit a request, and no wording removes that.

### Grouped mode is less sure

When forced to split the catalog into three groups of 50 plus a final round (the fallback for catalogs over 200
skills), the container request stayed at 0.99 and the Tokyo question at 1.00 ("none"), but the slide-deck
request fell to 0.54. Prefer a catalog that fits in one request, and prune what you never use with `exclude`.

### Known misses are documented, not hidden

Two wrong-ish picks appeared during real use. A question *about which Claude model to choose* was matched to an
API-building skill at 95% until that skill's one-liner said it was not for Claude Code's own settings; after
the rewrite it moved to a closer (but still imperfect) neighbour. A request to make a *different* tool behave like
Claude Code was matched to the settings skill at 64%. Both illustrate the same point: **the picker is
advisory.** The instruction it injects includes "if it clearly does not fit, say so and continue".

## 3. What one Jev call costs and how long it takes

| Call | Input | Latency | Cost |
|---|---|---|---|
| Router: size + follow-up | ~540 tokens (derived from the reported cost) | 0.35 to 0.76 s | ~$0.000023 |
| Skill picker, 143 skills in one request | tens of thousands of characters | 0.4 to 1.0 s | ~$0.00026 |
| Skill picker, 81 skills (Codex) | | ~0.4 to 0.5 s | ~$0.00015 |
| Three questions about a sample sales email (`jev` skill) | 756 tokens | 0.53 s | $0.0000318 |
| Batch of three short emails, parallel | | 0.86 s total | ~$0.00005 |

At Jev 1.13's published price ($0.042 per million input tokens, output free) about **1,000 short items cost 3
cents**. Router plus picker together were roughly $0.0003 per message in our runs.

## 4. The helper cold-start cost (the number that qualifies "tiny always delegates")

A standalone run of the Haiku helper for one trivial question (`claude -p --agent jev-tiny`, the closest proxy
for a cold helper) answered correctly, reported `Model: Haiku 4.5 (claude-haiku-4-5-20251001)`, and cost about
**$0.05 at list prices**. A fresh session loads its own system prompt and tool definitions, which dwarfs a
one-line answer. Answering the same question inside an already-warm conversation is far cheaper. That is why
the router is **baseline-aware**, why follow-ups are never delegated, and why `tiny` always delegating is a
documented, reversible choice ([how to change it](CUSTOMIZING.md#stop-tiny-jobs-from-always-delegating)).
On a subscription plan this counts against your usage limits rather than being billed per call.

## 5. Delegation end to end

**Codex.** A forced `[tiny]` message. The router log recorded `via: tag`, `agent: jev-tiny`; the session
transcript then showed the injected instruction, a `spawn_agent` call with `agent_type: "jev-tiny"`, a
`SubAgentActivity` "started" and "completed" pair, and a separate sub-agent thread whose model was `gpt-6-luna`
(parent: `gpt-6-sol`). The answer to the (trivial) question came back correctly.

**Claude Code.** The injected `additionalContext` reached the assistant in live sessions and was followed or
consciously declined with a stated reason; the helpers run on the model in their front matter (verified with the
standalone run above).

An earlier Codex "hardest" attempt stopped at the account's own usage limit after the model had begun
multi-agent coordination, which is why the forced-tag test above was added: tags make the delegation path cheap
to test.

## 6. Automated tests

44 offline tests run every script against a temporary `$HOME` (no network, no key, no real config) on Linux and
macOS in CI: tags and aliases, false-positive tag shapes, explicit-downgrade semantics, `force`/`min`/`max` rules
and precedence, bad and malformed rules, the follow-up exemption, baseline detection for both tools, key
resolution order and its no-shell property, error messages that never contain the key, environment overrides
and bad numbers, catalog building and overrides, `@here` privacy, the hook protocol, fail-open on every error,
logs that never contain message text (canary string), and the installer (merge, idempotency, preservation of
existing hooks and settings, uninstall, dry run, refusal to touch invalid JSON).

## 7. Reproduce it

```bash
jev-router doctor                          # one live call, cost and latency reported
jev-router check "@large summarise this"   # offline: what a tag/rule would do
skill-pick "make a 10-slide deck"          # top candidates, latency and cost; loads nothing
python3 -m unittest discover -s tests -t . -v
```
