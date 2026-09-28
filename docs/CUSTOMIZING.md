# Customising

Everything here is a small, local edit. After editing `rules.json` nothing needs a restart; after editing
`router.py` or `picker.py` the next message uses the new code (each message starts a fresh process).

Test rule and tag changes without calling Jev: `jev-router check "your message"` (or `cx-router check …`).

## Rules cookbook

A rule is `{"match": <regex>, "force"|"min"|"max": <tier or "here">, "note": <label>}`. Tiers, smallest to
largest: `tiny`, `everyday`, `large`, `hardest`. The regex is case-insensitive and searched in the first 20,000
characters of your message. Remember JSON needs backslashes doubled (`\\b`).

> `rules.json` is strict JSON: **remove the `//` comment lines** below when you copy the example, or the whole
> file fails to load (and no rules apply).

```jsonc
{
  "rules": [
    // Floor: infrastructure, money and destructive verbs never run on the cheapest model.
    {"match": "cloudflare|\\bdns\\b|\\bdomains?\\b|\\bdeploy(ing|ment|ed)?\\b|\\binvoices?\\b|\\bdelet(e|ing|ed)\\b",
     "min": "everyday", "note": "infra/money/destructive"},

    // Stronger floor for production work.
    {"match": "\\bprod(uction)?\\b", "min": "large", "note": "production work gets a strong model"},

    // Cap: quick-answer phrasing should never trigger an expensive helper.
    {"match": "^(quick|tl;dr|briefly)\\b", "max": "everyday", "note": "keep quick asks cheap"},

    // Privacy: never send anything mentioning these to Jev at all (router AND skill picker).
    {"match": "client-acme|\\bpassword\\b|\\bcredentials?\\b", "force": "here", "note": "private"},

    // Pin a workflow to a tier, skipping Jev (free and instant).
    {"match": "^weekly report", "force": "everyday", "note": "routine report"}
  ]
}
```

Semantics to remember:

- **Precedence:** a leading tag beats every rule; then `force` rules (first match wins); then Jev's size with
  every matching `min` (highest wins) and `max` (lowest wins) applied. If `min` and `max` conflict, `min` wins.
- **Floors survive uncertainty.** A `min` rule applies even when Jev is unsure; a `max` cap only when Jev is sure.
- **Follow-ups are exempt.** A message Jev recognises as a follow-up stays in the session regardless of rules
  (except `force`, which is decided before Jev is asked).
- **`force: "here"` is the privacy switch.** It keeps the message out of Jev entirely, for both hooks.
- Rules that fail to compile, or name an unknown tier, are skipped silently.

## Adapt the model lineup

The lineup lives in two places that must agree: the `SIZES` table at the top of `router.py`, and the four
helper-agent files.

```python
SIZES = [
    # (tier,      helper agent,    name shown to you, fragment of your default model's name, description Jev sees)
    ("tiny",     "jev-tiny",      "Haiku 4.5",        "haiku",  "A tiny job: a quick lookup, a rename, ..."),
    ("everyday", "jev-everyday",  "Sonnet 5",         "sonnet", "An everyday job: a normal email, ..."),
    ("large",    "jev-large",     "Opus 5.5",         "opus",   "A large job: a multi-step build, ..."),
    ("hardest",  "jev-hardest",   "Fable 5.1",        "fable",  "The hardest job: strategy, ..."),
]
```

- The **fragment** is matched inside the `model` value from your settings to work out your default's tier, so it
  must be a substring that identifies that model (`opus` matches `claude-opus-5-5` and `opus[1m]`).
- **Descriptions are what Jev reads** to size a message. If it mis-sizes your work, rewrite them with examples from
  *your* day ("a migration script", "a one-line regex").
- **Helper agents:** Claude Code (`~/.claude/agents/jev-<tier>.md`) pins the model in front matter (`model: haiku`,
  or a full model ID). Codex (`~/.codex/agents/jev-<tier>.toml`) sets `model` and `model_reasoning_effort`.
- **Fewer than four models?** Keep all four tiers and point two of them at the same model (Codex does this: `large`
  and `hardest` both use `gpt-6-astra`, differing only in reasoning effort). Deleting tiers is not recommended: the
  smallest tier must be named `tiny` for the always-delegate rule, and tags and status lines assume four names.
- **New model names:** just update the display name, fragment and agent files. No other code refers to models.

## Stop tiny jobs from always delegating

By default a `tiny` verdict always goes to the smallest model. To make tiny jobs behave like every other size
(stay in your session when your default already covers them), edit `decide()` in `router.py`. Change

```python
    elif size == "tiny":
        covered = base_rank == 0
    else:
        covered = RANK[size] <= base_rank
```

to

```python
    else:
        covered = RANK[size] <= base_rank
```

(Explicit `@tiny` tags still delegate, because they are handled by the earlier branch.) Do the same in both
routers if you use both tools.

## Tune thresholds and timeouts

See the environment-variable table in the README. Useful moves:

- Jev too twitchy? Raise `JEV_ROUTER_MIN_CONFIDENCE` (say `0.75`): more messages stay in your session.
- Hooks feel slow on a bad network? Lower `JEV_HTTP_TIMEOUT` (say `1.0`).
- Endpoint moved? Set `JEV_ENDPOINT` / `JEV_MODEL`.

## Improve the skill picker

- **Sharpen a one-liner:** copy `overrides.example.json` to `overrides.json` in the picker folder, add
  `"skill-name": "One clear sentence about when to use it, and when not to."`, run `skill-pick --rebuild`.
  Naming what a skill is *not* for ("not for PowerPoint files") is the most effective fix for look-alikes.
- **Hide skills** you never want picked with `"exclude": ["skill-a", "skill-b"]`.
- **Claude Code built-ins:** `builtins.json` lists one-liners for skills that have no file on disk. Edit freely.
- **After installing or removing skills:** `skill-pick --rebuild` (or `cx-skill-pick --rebuild`).
- **Try before you trust:** `skill-pick "the exact thing you would type"` prints the pick, the top three
  candidates and the cost, and loads nothing.

## Change the helpers' behaviour

The helper agents are plain files. Edit the instructions to change tone or add constraints, add tools
restrictions (Claude Code `tools:` front matter) to make a tier read-only, or change the closing "Model:" line
convention. Keep the `name` values (`jev-<tier>`): the router refers to them by name.
