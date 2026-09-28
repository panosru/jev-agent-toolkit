#!/usr/bin/env python3
"""Jev skill picker: Jev points at the one skill that should handle a request.

Jev only points. It never runs a skill and never writes anything.

  skill-pick "request text"        try it on any request (one short line + details)
  skill-pick --list                show the catalog: every skill, one line each
  skill-pick --rebuild             rebuild the catalog after adding/removing skills
  skill-pick on | off | status     the every-message hook switch (OFF by default)
  picker.py --hook                 UserPromptSubmit hook mode (reads Claude Code's JSON on stdin)
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
DIR = os.path.join(HOME, ".claude", "skill-picker")
CATALOG = os.path.join(DIR, "catalog.json")
BUILTINS = os.path.join(DIR, "builtins.json")
OVERRIDES = os.path.join(DIR, "overrides.json")
FLAG = os.path.join(DIR, "enabled")
RULES_PATH = os.path.join(HOME, ".claude", "jev-router", "rules.json")  # shared with the router
LOG = os.environ.get("SKILL_PICKER_LOG", os.path.join(DIR, "log.jsonl"))

ENDPOINT = os.environ.get("JEV_ENDPOINT", "https://openrouter.ai/api/alpha/decisions")
MODEL = os.environ.get("JEV_MODEL", "typesafe/jev-1.13")
THRESHOLD = _env_float("JEV_SKILL_MIN_CONFIDENCE", 0.60)  # below this, the assistant picks the skill itself
NONE = "none_of_these"
MAX_OPTIONS = 200           # Jev allows 255 options; beyond this, split into groups
MAX_CRITERIA_CHARS = 60000  # about 15k tokens; keeps the question well inside Jev's 32k window
GROUP_SIZE = 50             # skills per group when splitting
MAX_REQUEST_CHARS = 4000
HOOK_HTTP_TIMEOUT = _env_float("JEV_HTTP_TIMEOUT", 1.5)  # every-message mode gives up fast
CLI_HTTP_TIMEOUT = 15
KEY_CMD_TIMEOUT = _env_float("JEV_KEY_CMD_TIMEOUT", 2.0)  # seconds allowed for the key command


# ---------------------------------------------------------------- catalog

def _frontmatter(path):
    try:
        text = open(path, encoding="utf-8").read(8000)
    except OSError:
        return {}
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    fm, key, out = m.group(1), None, {}
    for line in fm.splitlines():
        km = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if km:
            key, val = km.group(1), km.group(2).strip()
            out[key] = "" if val in (">", "|", ">-", "|-") else val
        elif key and line.startswith((" ", "\t")):
            out[key] = (out[key] + " " + line.strip()).strip()
    return {k: v.strip().strip('"').strip("'") for k, v in out.items()}


def one_line(desc, limit=180):
    """First sentence or two of a skill description, as one line."""
    desc = re.sub(r"\s+", " ", desc or "").strip()
    sentences = re.split(r"(?<=[.!?])\s+", desc)
    line = sentences[0] if sentences else ""
    if len(line) < 50 and len(sentences) > 1:
        line += " " + sentences[1]
    if len(line) > limit:
        line = line[:limit].rsplit(" ", 1)[0] + "…"
    return line


def _load_json(path):
    """Read a JSON file, returning {} when it is missing or unreadable (a fresh install has none)."""
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def build_catalog():
    found = {}  # name -> (line, source)

    def add(name, fm, source):
        if fm.get("disable-model-invocation", "").lower() == "true":
            return  # user-invoked only; Claude can't load it anyway
        if fm.get("description"):
            found[name] = (one_line(fm["description"]), source)

    skills_dir = os.path.join(HOME, ".claude", "skills")
    for name in sorted(os.listdir(skills_dir)) if os.path.isdir(skills_dir) else []:
        p = os.path.join(skills_dir, name, "SKILL.md")
        if not name.startswith(".") and name != "synced" and os.path.exists(p):
            add(_frontmatter(p).get("name") or name, _frontmatter(p), "skills")
    synced = os.path.join(skills_dir, "synced")  # os.walk on a missing dir just yields nothing
    for root, dirs, files in os.walk(synced):
        if "SKILL.md" in files:
            fm = _frontmatter(os.path.join(root, "SKILL.md"))
            add("anthropic-skills:" + os.path.basename(root), fm, "claude.ai")  # loaded by folder name

    settings = _load_json(os.path.join(HOME, ".claude", "settings.json"))
    enabled = {k for k, v in (settings.get("enabledPlugins") or {}).items() if v}
    installed = _load_json(os.path.join(HOME, ".claude", "plugins", "installed_plugins.json"))
    for pid, entries in installed.get("plugins", {}).items():
        if pid not in enabled or not entries:
            continue
        plugin, path = pid.split("@")[0], entries[0]["installPath"]
        for root, dirs, files in os.walk(os.path.join(path, "skills")):
            if "SKILL.md" in files:
                fm = _frontmatter(os.path.join(root, "SKILL.md"))
                add(f"{plugin}:{fm.get('name') or os.path.basename(root)}", fm, "plugin")
        cmd_dir = os.path.join(path, "commands")
        if os.path.isdir(cmd_dir):
            for f in sorted(os.listdir(cmd_dir)):
                if f.endswith(".md"):
                    add(f"{plugin}:{f[:-3]}", _frontmatter(os.path.join(cmd_dir, f)), "plugin")

    for name, line in _load_json(BUILTINS).items():
        if not name.startswith("_"):
            found.setdefault(name, (line, "built-in"))

    # The same skill shipped twice (e.g. pdf and anthropic-skills:pdf): keep the plain name,
    # otherwise Jev splits its certainty between two identical options.
    merged = {}
    for name in list(found):
        if ":" in name:
            base = name.split(":", 1)[1]
            if base in found and ":" not in base:
                merged[name] = base
                del found[name]

    ov = _load_json(OVERRIDES)
    for name in ov.get("exclude", []):
        found.pop(name, None)
    for name, line in ov.get("lines", {}).items():
        if name in found:
            found[name] = (line, found[name][1])

    catalog = {
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "merged_duplicates": merged,
        "skills": [{"name": n, "line": l, "source": s} for n, (l, s) in sorted(found.items())],
    }
    os.makedirs(os.path.dirname(CATALOG), exist_ok=True)
    with open(CATALOG, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=1, ensure_ascii=False)
    return catalog


def load_catalog():
    if not os.path.exists(CATALOG):
        return build_catalog()
    return json.load(open(CATALOG))


# ---------------------------------------------------------------- Jev

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


def _call(request, questions, key, timeout):
    payload = {"model": MODEL, "state": {"request": request[:MAX_REQUEST_CHARS]}, "questions": questions}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "X-Title": "Claude Code - Jev skill picker"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _question(skills):
    criteria = {s["name"]: s["line"] for s in skills}
    criteria[NONE] = ("No skill in this list fits: ordinary conversation, a quick question, or a task "
                      "none of these skills is made for.")
    return {"type": "choice",
            "instructions": "Which one skill should handle `request`? Pick the skill whose purpose "
                            "matches what the user is asking for.",
            "criteria": criteria}


def pick(request, force_groups=None, timeout=CLI_HTTP_TIMEOUT, deadline=None):
    """Returns a dict: skill, confidence, top, rounds, ms, cost_usd, groups."""
    skills = load_catalog()["skills"]
    key = api_key()
    start = time.perf_counter()
    cost = 0.0
    chars = sum(len(s["name"]) + len(s["line"]) for s in skills)
    size = force_groups or (GROUP_SIZE if (len(skills) > MAX_OPTIONS or chars > MAX_CRITERIA_CHARS) else None)

    if not size:
        data = _call(request, {"pick": _question(skills)}, key, timeout)
        cost += data.get("usage", {}).get("cost") or 0
        ans, rounds, groups = data["answers"]["pick"], 1, 1
    else:
        chunks = [skills[i:i + size] for i in range(0, len(skills), size)]
        data = _call(request, {f"group_{i}": _question(c) for i, c in enumerate(chunks)}, key, timeout)
        cost += data.get("usage", {}).get("cost") or 0
        winners = [data["answers"][f"group_{i}"]["choice"] for i in range(len(chunks))]
        winners = [w for w in winners if w != NONE]
        groups = len(chunks)
        if not winners:
            ans = {"choice": NONE, "confidence": 1.0, "probabilities": {NONE: 1.0}}
        else:
            if deadline and time.perf_counter() > deadline:
                raise TimeoutError("no time left for the final round")
            by_name = {s["name"]: s for s in skills}
            data = _call(request, {"final": _question([by_name[w] for w in winners])}, key, timeout)
            cost += data.get("usage", {}).get("cost") or 0
            ans = data["answers"]["final"]
        rounds = 2 if winners else 1

    probs = ans.get("probabilities", {})
    top = sorted(probs.items(), key=lambda kv: -kv[1])[:3]
    return {"skill": ans["choice"], "confidence": round(ans.get("confidence", 0.0), 3),
            "top": [(k, round(v, 3)) for k, v in top], "rounds": rounds, "groups": groups,
            "ms": round((time.perf_counter() - start) * 1000), "cost_usd": cost,
            "jev_model": data.get("model")}


def verdict(r):
    """One human-readable line: what Jev thought, how sure, and what happened as a result.
    Branching matches hook()'s picked/none/unsure exactly, so this text never contradicts the log."""
    pct = round(r["confidence"] * 100)
    if r["confidence"] >= THRESHOLD and r["skill"] != NONE:
        return f"Jev (skill): picked `{r['skill']}` ({pct}% sure) → loading it"
    if r["skill"] == NONE:
        return f"Jev (skill): no skill fits this ({pct}% sure) → answering directly"
    return f"Jev (skill): not sure which skill fits (best guess: `{r['skill']}`, {pct}%) → picking the normal way"


# ---------------------------------------------------------------- modes

def log(entry):
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _is_private(message):
    """True when the user asked to keep this message out of Jev entirely: a leading @here / [here]
    tag, or a matching `force: here` rule in the router's rules.json. Such a message is never sent."""
    if re.match(r"^\s*(?:@here|\[here\])(?=\s|$)", message, re.I):
        return True
    path = os.environ.get("JEV_ROUTER_RULES", RULES_PATH)
    try:
        for r in json.load(open(path, encoding="utf-8")).get("rules", []):
            if r.get("force") == "here" and re.search(r["match"], message[:20000], re.I):
                return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError, re.error):
        pass
    return False


def hook():
    if not os.path.exists(FLAG) and not os.environ.get("SKILL_PICKER_FORCE"):
        return  # OFF: nothing leaves the machine
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        data = json.load(sys.stdin)
        request = (data.get("prompt") or "").strip()
        if not request or request[0] in "/!":
            entry["outcome"] = "skipped"
            return log(entry)
        if _is_private(request):  # @here or a force:here rule: this message never leaves the machine
            entry["outcome"] = "private"
            return log(entry)
        r = pick(request, timeout=HOOK_HTTP_TIMEOUT, deadline=time.perf_counter() + 2.5)
        entry.update({k: r[k] for k in ("skill", "confidence", "rounds", "ms", "cost_usd")})
        line = verdict(r)
        out = {"systemMessage": line}
        if r["confidence"] >= THRESHOLD and r["skill"] != NONE:
            entry["outcome"] = "picked"
            out["hookSpecificOutput"] = {"hookEventName": "UserPromptSubmit", "additionalContext": (
                f"[Jev skill picker] Jev picked the skill `{r['skill']}` for this request "
                f"({r['confidence'] * 100:.0f}% sure). Load it with the Skill tool before anything else, "
                f"and tell the user in one short line: \"{line}\" If, once loaded, it clearly doesn't fit "
                "what the user asked, say so and continue the normal way. If a [Jev router] note hands this job "
                "to a helper agent, don't load the skill yourself: tell the helper to load it first.")}
        else:
            entry["outcome"] = "none" if r["skill"] == NONE else "unsure"
        log(entry)
        print(json.dumps(out))
    except Exception as e:  # fail open: never block or delay the message
        entry.update(outcome="error", error=f"{type(e).__name__}: {str(e)[:200]}")
        log(entry)


def status():
    print(f"Jev skill picker (every message): {'ON' if os.path.exists(FLAG) else 'OFF'}")
    cat = load_catalog()
    print(f"Catalog: {len(cat['skills'])} skills (built {cat['built']})")
    if os.path.exists(LOG):
        rows = [json.loads(l) for l in open(LOG) if l.strip()]
        if rows:
            from collections import Counter
            c = Counter(r.get("outcome") for r in rows)
            print(f"Messages: {len(rows)}  picked {c['picked']}, no skill {c['none']}, unsure {c['unsure']}, "
                  f"private {c['private']}, skipped {c['skipped']}, errors {c['error']}")
            print(f"Jev cost so far: ${sum(r.get('cost_usd') or 0 for r in rows):.6f}")
    if os.path.exists(FLAG):
        print("While ON, each message you type goes through OpenRouter to TypeSafe (Jev's maker). "
              "Keep it OFF for private work.")


def main():
    args = sys.argv[1:]
    if args[:1] == ["--hook"]:
        return hook()
    if not args or args[0] in ("-h", "--help"):
        return print(__doc__)
    cmd = args[0]
    if cmd == "--rebuild":
        cat = build_catalog()
        return print(f"Catalog rebuilt: {len(cat['skills'])} skills, "
                     f"{len(cat['merged_duplicates'])} duplicates merged into their plain names.")
    if cmd == "--list":
        for s in load_catalog()["skills"]:
            print(f"{s['name']:<52} {s['line']}")
        return
    if cmd == "on":
        open(FLAG, "w").close()
        return print("Skill picker is ON for every message, from your next one.\n"
                     "Each message now goes through OpenRouter to TypeSafe (Jev's maker). Keep it OFF for private work.")
    if cmd == "off":
        if os.path.exists(FLAG):
            os.remove(FLAG)
        return print("Skill picker is OFF. Messages no longer leave your computer for skill picking.")
    if cmd == "status":
        return status()
    force = None
    if cmd == "--groups":
        force, args = int(args[1]), args[2:]
    r = pick(" ".join(args), force_groups=force)
    print(verdict(r))
    print(f"  top: " + ", ".join(f"{k} {v:.2f}" for k, v in r["top"]))
    print(f"  {r['rounds']} round(s), {r['groups']} group(s), {r['ms']} ms, ${r['cost_usd']:.6f}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--hook"]:
        try:
            hook()
        finally:
            sys.exit(0)
    main()
