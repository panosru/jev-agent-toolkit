#!/usr/bin/env python3
"""Jev model router - Codex CLI UserPromptSubmit hook.

Codex port of the Claude Code jev-router (~/.claude/jev-router/router.py). Asks Jev
(typesafe/jev-1.13 via OpenRouter) how big each message is. A subagent profile only
gets the job when it needs a BIGGER model than the one this session is already running
on (read live from ~/.codex/config.toml's top-level "model" field) - a fresh subagent
costs its own startup, so handing it a job the session already covers wastes usage.

Fail-open by design: if the router is OFF, or anything is slow or broken, it prints
nothing and exits 0, so the message goes through exactly as if the router weren't there.
No message text is ever written to disk; the log holds only sizes, scores, timings, cost.

Verified empirically against Codex 0.157.1: UserPromptSubmit hooks receive {session_id,
turn_id, transcript_path, cwd, hook_event_name, model, permission_mode, prompt} on
stdin, and a JSON reply with hookSpecificOutput.additionalContext genuinely steers the
model's answer (tested: a hook that injected "reply BANANA instead" changed the model's
reply from "pong" to "BANANA"). That field isn't documented for UserPromptSubmit
specifically (only shown for SubagentStart/PreToolUse/PostToolUse in the docs), but it
works there too.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request


def _env_float(name, default):
    """Read a numeric setting from the environment; a bad value falls back to the default."""
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default

HOME = os.path.expanduser("~")
DIR = os.path.join(HOME, ".codex", "jev-router")
FLAG = os.path.join(DIR, "enabled")
LOG = os.environ.get("JEV_ROUTER_LOG", os.path.join(DIR, "log.jsonl"))
CONFIG_TOML = os.path.join(HOME, ".codex", "config.toml")

ENDPOINT = os.environ.get("JEV_ENDPOINT", "https://openrouter.ai/api/alpha/decisions")
MODEL = os.environ.get("JEV_MODEL", "typesafe/jev-1.13")
HTTP_TIMEOUT = _env_float("JEV_HTTP_TIMEOUT", 1.5)  # seconds; Jev normally answers in 0.4-0.9 s
MIN_CONFIDENCE = _env_float("JEV_ROUTER_MIN_CONFIDENCE", 0.60)  # below this, the session handles the message itself
FOLLOWUP_CUTOFF = _env_float("JEV_FOLLOWUP_CUTOFF", 0.50)  # noul above this = a short in-conversation reply
KEY_CMD_TIMEOUT = _env_float("JEV_KEY_CMD_TIMEOUT", 2.0)  # seconds allowed for the key command
MAX_STATE_CHARS = 6000  # Jev only needs the gist to size a job

# Smallest to biggest, ranked 0-3. One subagent profile per size (~/.codex/agents/*.toml),
# each pinned to a Codex model + reasoning effort. Alias must be a substring unique to
# that model's slug, used to read the live baseline from config.toml.
SIZES = [
    ("tiny", "jev-tiny", "GPT-6-Luna", "luna",
     "A tiny job: a quick lookup, a rename, a one-line answer, a yes/no or a simple fact."),
    ("everyday", "jev-everyday", "GPT-6-Sol", "sol",
     "An everyday job: a normal email, a short document, a small edit or a routine coding/writing question."),
    ("large", "jev-large", "GPT-6-Astra (high effort)", "astra",
     "A large job: a multi-step build, research across several sources, a full report or a long document."),
    ("hardest", "jev-hardest", "GPT-6-Astra (ultra effort)", "astra",
     "The hardest job: strategy, high-stakes judgment, or anything where a wrong call is expensive."),
]
RANK = {name: i for i, (name, *_rest) in enumerate(SIZES)}

def current_baseline():
    """(rank, human model name) of the model this session runs on, read live from
    config.toml's top-level `model = "..."` line (before any [section] header - a
    profile override or per-project config isn't accounted for, matching the Claude
    router's same simplification of reading only the one global default field).
    Unset or unrecognized -> treat as "everyday" (Sol-equivalent), the safer side to
    under-delegate on.
    """
    try:
        for line in open(CONFIG_TOML):
            line = line.strip()
            if line.startswith("["):
                break  # past the top-level table; profile-specific overrides not read
            if line.startswith("model") and "=" in line and "model_reasoning" not in line:
                model = line.split("=", 1)[1].strip().strip('"').strip("'").lower()
                for name, _, model_name, alias, _ in SIZES:
                    if alias in model:
                        return RANK[name], model_name
                break
    except OSError:
        pass
    return RANK["everyday"], "GPT-6-Sol"


def log(entry):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def api_key():
    """Resolve the OpenRouter API key without ever storing it in a file this project owns.

    Order (first hit wins):
      1. $OPENROUTER_API_KEY
      2. $JEV_KEY_CMD - a command line that PRINTS the key (macOS Keychain, pass,
         1Password, sops, ...). The command is not a secret; the key stays in your store.
         It is split with shlex and run WITHOUT a shell, so pipes and redirects are not
         interpreted: put a pipeline in a small script and point the command at that.
      3. The command saved in $JEV_CONFIG_DIR/key_cmd (default ~/.config/jev/key_cmd).
         Also just a command, so it is safe on disk and it works for sessions launched
         from a GUI, which inherit no shell environment.
    The key is never logged, printed or written anywhere by this project.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    cmd = os.environ.get("JEV_KEY_CMD", "").strip()
    if not cmd:
        cfg = os.path.join(os.environ.get("JEV_CONFIG_DIR", os.path.join(HOME, ".config", "jev")), "key_cmd")
        try:
            cmd = open(cfg).read().strip()
        except OSError:
            cmd = ""
    if not cmd:
        raise RuntimeError("no OpenRouter key configured: set OPENROUTER_API_KEY or JEV_KEY_CMD, "
                           "or write a key command to ~/.config/jev/key_cmd")
    import shlex
    try:
        argv = [os.path.expandvars(os.path.expanduser(tok)) for tok in shlex.split(cmd)]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=KEY_CMD_TIMEOUT)
    except (OSError, ValueError) as e:  # missing executable, unbalanced quotes
        raise RuntimeError(f"key command could not be run ({type(e).__name__})")
    key = out.stdout.strip()
    if out.returncode != 0 or not key:
        raise RuntimeError(f"key command failed (exit {out.returncode}) or printed nothing")
    return key


def ask_jev(message, key):
    payload = {
        "model": MODEL,
        "state": {"message": message[:MAX_STATE_CHARS]},
        "questions": {
            "size": {
                "type": "choice",
                "instructions": "What is the smallest AI model size that can do the job asked for in `message` well?",
                "criteria": {name: desc for name, _, _, _, desc in SIZES},
            },
            "followup": {
                "type": "noul",
                "instructions": "Is `message` a short reply that only makes sense inside an ongoing "
                                "conversation, because it refers to something said earlier?",
                "criteria": {
                    "true": "It points back at earlier messages, e.g. 'yes do that', 'make it shorter', "
                            "'the second one', 'try again', 'ok go ahead'.",
                    "false": "It is a self-contained request that a newcomer could act on without the "
                             "earlier conversation.",
                },
            },
        },
    }
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-Title": "Codex CLI - Jev router",
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())


RULES_FILE = os.environ.get("JEV_ROUTER_RULES", os.path.join(DIR, "rules.json"))
MAX_MATCH_CHARS = 20000   # rules only look at the start of very long pasted messages
EXTRA_ALIASES = {"ultra": "hardest"}
NOUN = "subagent"
SIZE_WORDS = {"tiny": "a tiny job", "everyday": "an everyday job",
              "large": "a large job", "hardest": "the hardest kind of job"}


def delegate_text(agent, model_name):
    return (f"Spawn the `{agent}` subagent (defined in ~/.codex/agents/{agent}.toml, runs on {model_name}) "
            "to handle the whole job: give it the user's request verbatim plus any context from this "
            "conversation it needs (paths, earlier decisions, constraints). Don't do the work yourself "
            "first. When it returns, relay its result to the user as-is. If a [Jev skill picker] note "
            "names a skill, tell the subagent to use that skill first instead of using it yourself. If "
            "the subagent fails or clearly can't do the job, do it yourself and say so.")


# ---------------------------------------------------------------- user overrides
# Precedence: 1) a leading @tag / [tag] in the message, 2) a matching `force` rule,
# 3) Jev's own sizing, with any matching `min` / `max` rules applied on top.
# A tag or force rule needs no Jev call at all: free, instant, and works offline.

def tag_aliases():
    """Words accepted in a leading @tag / [tag]: the four size names, the model names from
    SIZES (first size wins where one model covers two, e.g. Codex's astra), EXTRA_ALIASES,
    and 'here' (= keep this message in the current session, never delegate)."""
    aliases = {name: name for name, *_rest in SIZES}
    for name, _, _, alias, _ in SIZES:
        aliases.setdefault(alias, name)
    aliases.update(EXTRA_ALIASES)
    aliases["here"] = "here"
    return aliases


TAG_RE = re.compile(r"^\s*(?:@([A-Za-z]+)|\[([A-Za-z]+)\])(?=\s|$)")


def parse_tag(message):
    """(word, size-or-'here') if the message STARTS with a recognised tag, else None.
    Unknown words (@src, [WIP]) are left alone and treated as ordinary text."""
    m = TAG_RE.match(message)
    if not m:
        return None
    word = (m.group(1) or m.group(2)).lower()
    size = tag_aliases().get(word)
    return (word, size) if size else None


def load_rules():
    """rules.json -> [{"match": regex, "force": size|"here", "min": size, "max": size, "note": text}].
    Bad entries are skipped silently: a typo in the file must never break a message."""
    try:
        raw = json.load(open(RULES_FILE)).get("rules", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []
    rules = []
    for r in raw:
        try:
            rx = re.compile(r["match"], re.I)
            force = r.get("force") if r.get("force") in (set(RANK) | {"here"}) else None
            lo = r.get("min") if r.get("min") in RANK else None
            hi = r.get("max") if r.get("max") in RANK else None
            if force or lo or hi:
                rules.append({"rx": rx, "force": force, "min": lo, "max": hi,
                              "note": str(r.get("note") or r["match"])[:60]})
        except Exception:
            continue
    return rules


def matching_rules(message):
    text = message[:MAX_MATCH_CHARS]
    return [r for r in load_rules() if r["rx"].search(text)]


def reason_text(d):
    via, size = d.get("via"), d["size"]
    if via == "tag":
        return (f"The user explicitly asked for the '{size}' tier with a leading {d['tag']} tag "
                "(leave that tag out of the prompt you pass on).")
    if via == "rule-force":
        return f"A user-defined routing rule ({d['note']}) fixed this message to the '{size}' tier."
    if via in ("rule-min", "rule-max"):
        return (f"Jev sized this message as '{d['jev_size']}' (confidence {d['confidence']:.2f}), and a "
                f"user-defined routing rule ({d['note']}) moved it to '{size}'.")
    return f"Jev sized this message as '{size}' (confidence {d['confidence']:.2f})."


def decide(d, size, via=None, note=None):
    """Turn a settled size into an outcome. `via` is None when Jev's own call decided it."""
    base_rank, base_name = current_baseline()
    d["size"], d["baseline"] = size, base_name
    if via:
        d["via"], d["note"] = via, note
    if size == "here":
        d["outcome"] = "self:here"
        return d, None
    if via in ("tag", "rule-force"):
        covered = RANK[size] == base_rank  # an explicit ask is honoured literally, up or down
    elif size == "tiny":
        # "tiny" always goes to the weakest model, even when the baseline is more capable
        # (e.g. Sonnet) - the user wants genuinely trivial jobs on the cheapest model.
        covered = base_rank == 0
    else:
        covered = RANK[size] <= base_rank  # otherwise stay local if the baseline already covers it
    if covered:
        d["outcome"] = "self:covered"
        return d, None
    _, agent, model_name, _, _ = next(s for s in SIZES if s[0] == size)
    d["outcome"], d["agent"], d["model_name"] = "delegated", agent, model_name
    return d, "[Jev router] " + reason_text(d) + " " + delegate_text(agent, model_name)


def route(message):
    """Return (decision dict, context text or None). Raises on any failure."""
    d = {"confidence": 1.0, "followup": 0.0, "ms": 0, "cost_usd": 0}
    tag = parse_tag(message)
    if tag:
        d["tag"] = "@" + tag[0]
        return decide(d, tag[1], "tag")
    rules = matching_rules(message)
    for r in rules:
        if r["force"]:
            return decide(d, r["force"], "rule-force", r["note"])

    start = time.perf_counter()
    data = ask_jev(message, api_key())
    d["ms"] = round((time.perf_counter() - start) * 1000)
    size = data["answers"]["size"]
    followup = data["answers"]["followup"]["noul"]
    d.update({
        "size": size["choice"],
        "confidence": round(size.get("confidence", 0.0), 3),
        "followup": round(followup, 3),
        "cost_usd": data.get("usage", {}).get("cost"),
        "jev_model": data.get("model"),
    })
    if followup > FOLLOWUP_CUTOFF:
        d["outcome"] = "self:followup"
        return d, None

    size, sure = d["size"], d["confidence"] >= MIN_CONFIDENCE
    lo = max((r for r in rules if r["min"]), key=lambda r: RANK[r["min"]], default=None)
    hi = min((r for r in rules if r["max"]), key=lambda r: RANK[r["max"]], default=None)
    via = note = None
    if sure and hi and RANK[size] > RANK[hi["max"]]:
        size, via, note = hi["max"], "rule-max", hi["note"]
    # A floor is a promise, so it also applies when Jev is unsure: run at the floor itself.
    if lo and (not sure or RANK[size] < RANK[lo["min"]]):
        size, via, note = lo["min"], "rule-min", lo["note"]
    if via:
        d["jev_size"] = d["size"]
        return decide(d, size, via, note)
    if not sure:
        d["outcome"] = "self:unsure"
        return d, None
    return decide(d, size)


def describe(d):
    """One human-readable line: what happened, why, and what it means for handling."""
    via = d.get("via")
    if via:
        size_phrase = SIZE_WORDS.get(d["size"], d["size"])
        if via == "tag":
            why = ("you asked to keep this here" if d["size"] == "here"
                   else f"you asked for {size_phrase}") + f" ({d['tag']})"
        elif via == "rule-force":
            why = f"rule '{d['note']}' set this to " + ("staying here" if d["size"] == "here" else size_phrase)
        else:
            pct = round(d["confidence"] * 100)
            if d["jev_size"] == d["size"]:  # Jev's guess already sat on the floor, it just wasn't sure
                why = f"rule '{d['note']}' holds this at {size_phrase} or above (Jev was only {pct}% sure)"
            else:
                verb = "raised" if via == "rule-min" else "capped"
                guess = SIZE_WORDS.get(d["jev_size"], d["jev_size"])
                why = f"rule '{d['note']}' {verb} this to {size_phrase} (Jev guessed {guess}, {pct}%)"
        if d["outcome"] == "delegated":
            tail = f"sent to the {d['model_name']} {NOUN}"
        elif d["outcome"] == "self:here":
            tail = f"kept in this session ({d['baseline']})"
        else:
            tail = f"answered here ({d['baseline']} covers it)"
        return f"Jev (model): {why} → {tail}"
    pct, fpct = round(d["confidence"] * 100), round(d["followup"] * 100)
    size_phrase = SIZE_WORDS.get(d["size"], d["size"])
    if d["outcome"] == "delegated":
        return f"Jev (model): {size_phrase} ({pct}% sure) → sent to the {d['model_name']} {NOUN}"
    if d["outcome"] == "self:followup":
        return f"Jev (model): looks like a follow-up to our conversation ({fpct}% sure) → answered here as usual"
    if d["outcome"] == "self:unsure":
        return f"Jev (model): not sure how big this is (best guess: {size_phrase}, {pct}%) → answered here as usual"
    if d["outcome"] == "self:covered":
        return f"Jev (model): {size_phrase} ({pct}% sure), already covered by {d['baseline']} → answered here"
    return f"Jev (model): {d['outcome']}"


def main():
    if not os.path.exists(FLAG) and not os.environ.get("JEV_ROUTER_FORCE"):
        return  # OFF: no network, no output, no log (tags and rules are inert too)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        data = json.load(sys.stdin)
        entry["session"] = (data.get("session_id") or "")[:8]
        message = (data.get("prompt") or "").strip()
        if not message or message[0] in "/!":
            entry["outcome"] = "skipped"
            log(entry)
            return
        decision, ctx = route(message)
        entry.update(decision)
        log(entry)
        out = {"systemMessage": describe(decision)}
        if ctx:
            out["hookSpecificOutput"] = {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}
        print(json.dumps(out))
    except Exception as e:  # fail open: never block or delay the message
        entry["outcome"] = "error"
        entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        log(entry)


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
