# Troubleshooting

**First step, always:** `jev-router doctor` (Claude Code) or `cx-router doctor` (Codex). It checks the Python
version, that the hook is registered, that the four helper agents exist, your rules, the detected default model,
that a key resolves (never displayed) and that one real Jev call works. Its output names the failing step.

## Debugging a hook by hand

Hooks are ordinary scripts. Feed one the JSON the host would send:

```bash
echo '{"prompt":"[large] hello","session_id":"debug"}' \
  | JEV_ROUTER_FORCE=1 JEV_ROUTER_LOG=/dev/null python3 ~/.claude/jev-router/router.py

echo '{"prompt":"make a slide deck"}' \
  | SKILL_PICKER_FORCE=1 SKILL_PICKER_LOG=/dev/null python3 ~/.claude/skill-picker/picker.py --hook
```

`JEV_ROUTER_FORCE=1` / `SKILL_PICKER_FORCE=1` run the hook even while it is switched off, and the `*_LOG`
variables redirect the log so a test does not pollute your real one. Use the `~/.codex/…` paths for Codex.
An error never shows on screen (hooks fail open); it is recorded as `"outcome": "error"` in `log.jsonl`, which
contains the exception type and a short message but never your text or your key.

## Symptoms

| Symptom | Cause and fix |
|---|---|
| Nothing appears after installing | The tools are **OFF** after install. `jev-router on && skill-pick on` (`cx-router`, `cx-skill-pick` for Codex). |
| Still nothing, and it is on | The session predates the install. Run `/hooks` or restart it. Codex must also *trust* the hooks: review them once with `/hooks`. |
| `doctor`: hook not registered | `./install.sh` again (it merges, never duplicates). Check the file it names for valid JSON. |
| `doctor`: key not found | Set `OPENROUTER_API_KEY`, or `JEV_KEY_CMD`, or install with `--key-cmd`. A GUI-launched session needs the `key_cmd` file: it cannot see your shell profile. |
| `doctor`: key command failed | Run the command by hand and check it prints only the key. It is executed **without a shell**, so pipes and `&&` do not work: wrap them in a script. It must answer within `JEV_KEY_CMD_TIMEOUT` (default 2 s). |
| `HTTP Error 401` | Wrong or revoked key. |
| `HTTP Error 402` | No credit on the OpenRouter account. |
| `HTTP Error 404` / schema errors | The alpha route or model name changed. Check the OpenRouter "Decisions" API reference and set `JEV_ENDPOINT` / `JEV_MODEL`. |
| `HTTP Error 429` | Rate limit. The hook fails open; the next message tries again. |
| Status lines appear but nothing is delegated | Usually correct: your default model already covers that size, or Jev was unsure, or it was a follow-up. Try `@large` to prove delegation works. |
| Everything is "not sure" | Jev is unsure on short or vague messages. Rewrite the tier descriptions with examples from your work, or use tags. |
| `@large` seems to be ignored | It must be the very first thing in the message. If `@` opens a file picker, type `[large]` instead. Tags do nothing while the router is OFF. |
| A rule does not match | Remember to double backslashes in JSON, and test it: `jev-router check "your message"`. A rule with an invalid regex is skipped silently; `jev-router rules` lists the ones that loaded. |
| Wrong skill picked | Jev chooses from one line per skill. Sharpen that line in `overrides.json` and run `skill-pick --rebuild`. Add "not for …" to separate look-alikes. |
| Skill picked is not a real skill | The catalog is stale. `skill-pick --rebuild`. |
| Hooks feel slow | Check `jev-router status` for average latency. Lower `JEV_HTTP_TIMEOUT`, or turn the picker off (`skill-pick off`) and keep the router. |
| `codex exec` hangs when scripted | Give it `</dev/null`; it waits for extra input on stdin otherwise. For one-off automation `--dangerously-bypass-hook-trust` skips the hook review. |
| Codex: "You've hit your usage limit" | That is your Codex account's own quota, not this toolkit. Delegated work also uses quota. |
| Over SSH, commands are "not found" | Non-interactive shells often lack `~/.local/bin` on `PATH`. Call the script by full path. |
| Python errors on very old systems | Python 3.8+ is required (the tests cover 3.9 and 3.13). |

## Starting over

```bash
./install.sh --all --uninstall --purge   # remove everything this project installed (backs up edited config)
./install.sh --all                        # install again
```
