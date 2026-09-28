"""Test helpers: load the scripts against a throwaway HOME so no real config is ever read or written."""
import contextlib
import importlib.util
import itertools
import os
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_counter = itertools.count()

# Environment variables that would change behaviour; cleared for every test.
JEV_ENV = ("OPENROUTER_API_KEY", "JEV_KEY_CMD", "JEV_CONFIG_DIR", "JEV_ENDPOINT", "JEV_MODEL", "JEV_HTTP_TIMEOUT",
           "JEV_ROUTER_MIN_CONFIDENCE", "JEV_SKILL_MIN_CONFIDENCE", "JEV_FOLLOWUP_CUTOFF", "JEV_KEY_CMD_TIMEOUT",
           "JEV_ROUTER_RULES", "JEV_ROUTER_LOG", "JEV_ROUTER_FORCE", "SKILL_PICKER_LOG", "SKILL_PICKER_FORCE")


@contextlib.contextmanager
def fake_home(extra_env=None):
    """A temporary HOME with all Jev-related environment variables cleared."""
    saved = {k: os.environ.get(k) for k in ("HOME",) + JEV_ENV}
    with tempfile.TemporaryDirectory() as tmp:
        for k in JEV_ENV:
            os.environ.pop(k, None)
        os.environ["HOME"] = tmp
        for k, v in (extra_env or {}).items():
            os.environ[k] = v
        try:
            yield tmp
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def write(home, rel, text):
    path = os.path.join(home, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def load(rel):
    """Import a script from the repo under a unique module name (module-level constants read $HOME at import)."""
    path = os.path.join(REPO, rel)
    spec = importlib.util.spec_from_file_location(f"mod_{next(_counter)}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROUTERS = {
    "claude": dict(path="claude/jev-router/router.py", baseline_file=".claude/settings.json",
                   sonnet_like='{"model": "sonnet"}', tiny_name="Haiku 4.5", noun="helper"),
    "codex": dict(path="codex/jev-router/router.py", baseline_file=".codex/config.toml",
                  sonnet_like='model = "gpt-6-sol"\n', tiny_name="GPT-6-Luna", noun="subagent"),
}
