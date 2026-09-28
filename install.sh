#!/usr/bin/env bash
# Install (or remove) the Jev toolkit for Claude Code and/or the Codex CLI.
#
#   ./install.sh                      install for whichever of Claude Code / Codex is present
#   ./install.sh --claude --enable    Claude Code only, and switch the router + skill picker ON
#   ./install.sh --key-cmd 'security find-generic-password -s jev-openrouter -w'
#   ./install.sh --uninstall          remove everything this script installed
#
# Safe by design: it backs up any config file before editing it, merges into existing hooks instead
# of replacing them, never writes an API key anywhere, and leaves the router OFF unless you pass --enable.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${HOME:?HOME must be set}"

DO_CLAUDE=0
DO_CODEX=0
ENABLE=0
DRY=0
UNINSTALL=0
PURGE=0
SYMLINKS=1
INSTALL_SKILL=1
KEY_CMD=""

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Targets (default: whichever of ~/.claude and ~/.codex exists):
  --claude            install for Claude Code
  --codex             install for the Codex CLI
  --all               both

Options:
  --enable            switch the router and the skill picker ON (default: OFF - your prompts
                      are only sent to Jev once you turn it on)
  --key-cmd CMD       save the command that prints your OpenRouter key to ~/.config/jev/key_cmd
                      (a command, never the key itself), e.g.
                      --key-cmd 'security find-generic-password -s jev-openrouter -w'
  --no-symlinks       do not create jev-router / skill-pick / cx-router / cx-skill-pick in ~/.local/bin
  --no-skill          do not install the optional `jev` skill (Claude Code)
  --dry-run           print what would happen, change nothing
  --uninstall         remove the installed files and hook entries (keeps your rules.json and
                      overrides.json unless --purge is also given)
  --purge             with --uninstall: also delete rules.json, overrides.json and logs
  -h, --help          show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --claude) DO_CLAUDE=1 ;;
    --codex) DO_CODEX=1 ;;
    --all) DO_CLAUDE=1; DO_CODEX=1 ;;
    --enable) ENABLE=1 ;;
    --key-cmd) shift; KEY_CMD="${1:-}"; [ -n "$KEY_CMD" ] || { echo "--key-cmd needs a value" >&2; exit 2; } ;;
    --no-symlinks) SYMLINKS=0 ;;
    --no-skill) INSTALL_SKILL=0 ;;
    --dry-run) DRY=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --purge) PURGE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

command -v python3 >/dev/null 2>&1 || { echo "python3 (3.8+) is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || { echo "python 3.8+ is required" >&2; exit 1; }

if [ "$DO_CLAUDE" -eq 0 ] && [ "$DO_CODEX" -eq 0 ]; then
  [ -d "$HOME_DIR/.claude" ] && DO_CLAUDE=1
  [ -d "$HOME_DIR/.codex" ] && DO_CODEX=1
  if [ "$DO_CLAUDE" -eq 0 ] && [ "$DO_CODEX" -eq 0 ]; then
    echo "Neither ~/.claude nor ~/.codex exists. Run the tool once, or pass --claude / --codex." >&2
    exit 1
  fi
fi

say() { printf '%s\n' "$*"; }
run() {
  if [ "$DRY" -eq 1 ]; then printf '[dry-run] %s\n' "$*"; else "$@"; fi
}

# put_file SRC DEST MODE [keep-existing]
put_file() {
  local src="$1" dest="$2" mode="$3" keep="${4:-}"
  if [ "$keep" = "keep-existing" ] && [ -e "$dest" ]; then
    say "  keep   $dest (already exists)"
    return 0
  fi
  run mkdir -p "$(dirname "$dest")"
  run cp "$src" "$dest"
  run chmod "$mode" "$dest"
  say "  put    $dest"
}

# hooks_edit merge|remove FILE COMMAND...   (JSON-safe, backs the file up first, idempotent)
hooks_edit() {
  local mode="$1" file="$2"
  shift 2
  if [ "$DRY" -eq 1 ]; then
    printf '[dry-run] %s hooks in %s\n' "$mode" "$file"
    return 0
  fi
  python3 - "$mode" "$file" "$@" <<'PY'
import json
import os
import sys
import time

mode, path, *cmds = sys.argv[1:]
data = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    if raw.strip():
        data = json.loads(raw)
    if not isinstance(data, dict):
        sys.exit(f"{path}: expected a JSON object at the top level")
before = json.dumps(data, sort_keys=True)

MARKERS = ("jev-router/router.py", "skill-picker/picker.py")
hooks = data.setdefault("hooks", {})
groups = hooks.setdefault("UserPromptSubmit", [])
if mode == "merge":
    present = {h.get("command") for g in groups for h in g.get("hooks", [])}
    for c in cmds:
        if c in present:
            continue
        label = "Jev sizing..." if "router.py" in c else "Jev picking a skill..."
        groups.append({"hooks": [{"type": "command", "command": c, "timeout": 5, "statusMessage": label}]})
else:
    kept = []
    for g in groups:
        inner = [h for h in g.get("hooks", []) if not any(m in h.get("command", "") for m in MARKERS)]
        if inner:
            g["hooks"] = inner
            kept.append(g)
    if kept:
        hooks["UserPromptSubmit"] = kept
    else:
        hooks.pop("UserPromptSubmit", None)
    if not hooks:
        data.pop("hooks", None)

if json.dumps(data, sort_keys=True) == before:
    print(f"  ok     {path} (hooks already {'present' if mode == 'merge' else 'absent'})")
    sys.exit(0)
if os.path.exists(path):
    backup = f"{path}.bak-jev-{time.strftime('%Y%m%d%H%M%S')}"
    with open(path, encoding="utf-8") as src, open(backup, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    print(f"  backup {backup}")
if mode == "remove" and not data:
    os.remove(path)  # nothing but our hooks was in there; the backup above keeps the original
    print(f"  removed Jev hooks and the now-empty {path}")
    sys.exit(0)
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(f"  {'added ' if mode == 'merge' else 'removed'} Jev hooks in {path}")
PY
}

link_cli() { # TARGET NAME
  local target="$1" dest="$HOME_DIR/.local/bin/$2"
  [ "$SYMLINKS" -eq 1 ] || return 0
  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    say "  skip   $dest exists and is not a symlink"
    return 0
  fi
  run mkdir -p "$HOME_DIR/.local/bin"
  run ln -sf "$target" "$dest"
  say "  link   $dest"
}

unlink_cli() { # NAME EXPECTED_TARGET
  local dest="$HOME_DIR/.local/bin/$1"
  if [ -L "$dest" ] && [ "$(readlink "$dest")" = "$2" ]; then
    run rm -f "$dest"
    say "  unlink $dest"
  fi
}

install_tool() { # claude|codex
  local tool="$1" base router cmd picker_cmd hooks_file agents_ext
  if [ "$tool" = "claude" ]; then
    base="$HOME_DIR/.claude"; router="jev-router"; cmd="jev-router"; picker_cmd="skill-pick"
    hooks_file="$base/settings.json"; agents_ext="md"
  else
    base="$HOME_DIR/.codex"; router="cx-router"; cmd="cx-router"; picker_cmd="cx-skill-pick"
    hooks_file="$base/hooks.json"; agents_ext="toml"
  fi
  say "== $tool =="
  put_file "$REPO_DIR/$tool/jev-router/router.py" "$base/jev-router/router.py" 755
  put_file "$REPO_DIR/$tool/jev-router/$router" "$base/jev-router/$router" 755
  put_file "$REPO_DIR/$tool/jev-router/rules.example.json" "$base/jev-router/rules.json" 644 keep-existing
  put_file "$REPO_DIR/$tool/skill-picker/picker.py" "$base/skill-picker/picker.py" 755
  put_file "$REPO_DIR/$tool/skill-picker/overrides.example.json" "$base/skill-picker/overrides.json" 644 keep-existing
  if [ "$tool" = "claude" ]; then
    put_file "$REPO_DIR/claude/skill-picker/builtins.json" "$base/skill-picker/builtins.json" 644
  fi
  local tier
  for tier in tiny everyday large hardest; do
    put_file "$REPO_DIR/$tool/agents/jev-$tier.$agents_ext" "$base/agents/jev-$tier.$agents_ext" 644
  done
  if [ "$tool" = "claude" ] && [ "$INSTALL_SKILL" -eq 1 ]; then
    put_file "$REPO_DIR/claude/skills/jev/SKILL.md" "$base/skills/jev/SKILL.md" 644
    put_file "$REPO_DIR/claude/skills/jev/scripts/jev.py" "$base/skills/jev/scripts/jev.py" 755
  fi

  hooks_edit merge "$hooks_file" "$base/jev-router/router.py" "$base/skill-picker/picker.py --hook"

  link_cli "$base/jev-router/$router" "$cmd"
  link_cli "$base/skill-picker/picker.py" "$picker_cmd"

  if [ "$DRY" -eq 0 ]; then
    python3 "$base/skill-picker/picker.py" --rebuild | sed 's/^/  /'
  fi
  if [ "$ENABLE" -eq 1 ]; then
    run touch "$base/jev-router/enabled" "$base/skill-picker/enabled"
    say "  ON     router and skill picker enabled"
  else
    say "  OFF    router and skill picker are off (enable: $cmd on && $picker_cmd on)"
  fi
}

uninstall_tool() { # claude|codex
  local tool="$1" base router cmd picker_cmd hooks_file agents_ext
  if [ "$tool" = "claude" ]; then
    base="$HOME_DIR/.claude"; router="jev-router"; cmd="jev-router"; picker_cmd="skill-pick"
    hooks_file="$base/settings.json"; agents_ext="md"
  else
    base="$HOME_DIR/.codex"; router="cx-router"; cmd="cx-router"; picker_cmd="cx-skill-pick"
    hooks_file="$base/hooks.json"; agents_ext="toml"
  fi
  say "== uninstall $tool =="
  [ -e "$hooks_file" ] && hooks_edit remove "$hooks_file"
  unlink_cli "$cmd" "$base/jev-router/$router"
  unlink_cli "$picker_cmd" "$base/skill-picker/picker.py"
  local tier
  for tier in tiny everyday large hardest; do
    run rm -f "$base/agents/jev-$tier.$agents_ext"
  done
  run rm -rf "$base/jev-router/__pycache__" "$base/skill-picker/__pycache__"
  run rm -f "$base/jev-router/router.py" "$base/jev-router/$router" "$base/jev-router/enabled"
  run rm -f "$base/skill-picker/picker.py" "$base/skill-picker/builtins.json" "$base/skill-picker/enabled" \
            "$base/skill-picker/catalog.json"
  if [ "$tool" = "claude" ]; then
    run rm -rf "$base/skills/jev"
  fi
  if [ "$PURGE" -eq 1 ]; then
    run rm -f "$base/jev-router/rules.json" "$base/jev-router/log.jsonl" \
              "$base/skill-picker/overrides.json" "$base/skill-picker/log.jsonl"
    run rmdir "$base/jev-router" "$base/skill-picker" 2>/dev/null || true
    say "  purged rules, overrides and logs"
  else
    say "  kept   rules.json / overrides.json (use --purge to delete them too)"
  fi
  say "  removed"
}

if [ "$UNINSTALL" -eq 1 ]; then
  [ "$DO_CLAUDE" -eq 1 ] && uninstall_tool claude
  [ "$DO_CODEX" -eq 1 ] && uninstall_tool codex
  say "Done. Your key command file (~/.config/jev/key_cmd) was left alone."
  exit 0
fi

if [ -n "$KEY_CMD" ]; then
  say "== key command =="
  if [ "$DRY" -eq 1 ]; then
    say "[dry-run] would write ~/.config/jev/key_cmd"
  else
    ( umask 077; mkdir -p "$HOME_DIR/.config/jev"; printf '%s\n' "$KEY_CMD" > "$HOME_DIR/.config/jev/key_cmd" )
    say "  saved  ~/.config/jev/key_cmd (a command, not the key; mode 600)"
  fi
fi

[ "$DO_CLAUDE" -eq 1 ] && install_tool claude
[ "$DO_CODEX" -eq 1 ] && install_tool codex

case ":$PATH:" in
  *":$HOME_DIR/.local/bin:"*) ;;
  *) [ "$SYMLINKS" -eq 1 ] && say "note: $HOME_DIR/.local/bin is not on your PATH; add it to use the short commands." ;;
esac

cat <<EOF

Next steps
  1. Make sure your OpenRouter key can be found (see README, "Keep your key safe"):
       export OPENROUTER_API_KEY=...        # or use --key-cmd / JEV_KEY_CMD
  2. Check everything, including one live Jev call:
$([ "$DO_CLAUDE" -eq 1 ] && echo "       jev-router doctor")
$([ "$DO_CODEX" -eq 1 ] && echo "       cx-router doctor")
  3. Turn it on when you are ready (messages are sent to Jev while it is ON):
$([ "$DO_CLAUDE" -eq 1 ] && echo "       jev-router on && skill-pick on")
$([ "$DO_CODEX" -eq 1 ] && echo "       cx-router on && cx-skill-pick on")
  Already-running sessions may need /hooks (or a restart) to pick up new hooks.
EOF
