"""API-key resolution (env var -> JEV_KEY_CMD -> saved command) and environment-variable configuration."""
import os
import unittest

from tests.helpers import fake_home, load, write

SCRIPTS = ("claude/jev-router/router.py", "codex/jev-router/router.py",
           "claude/skill-picker/picker.py", "codex/skill-picker/picker.py")


class TestKeyResolution(unittest.TestCase):
    def test_precedence_env_then_cmd_then_saved_command(self):
        for rel in SCRIPTS:
            with self.subTest(script=rel), fake_home() as home:
                write(home, ".config/jev/key_cmd", "printf from-file")
                m = load(rel)
                self.assertEqual(m.api_key(), "from-file")
                os.environ["JEV_KEY_CMD"] = "printf from-cmd"
                self.assertEqual(load(rel).api_key(), "from-cmd")
                os.environ["OPENROUTER_API_KEY"] = "from-env"
                self.assertEqual(load(rel).api_key(), "from-env")

    def test_no_key_configured_is_a_clear_error(self):
        for rel in SCRIPTS:
            with self.subTest(script=rel), fake_home():
                with self.assertRaises(RuntimeError) as cm:
                    load(rel).api_key()
                self.assertIn("no OpenRouter key configured", str(cm.exception))

    def test_failing_or_empty_or_missing_command(self):
        for rel in SCRIPTS:
            for cmd in ("false", "printf ''", "/definitely/not/a/program", "printf 'unbalanced"):
                with self.subTest(script=rel, cmd=cmd), fake_home(), self.assertRaises(RuntimeError):
                    os.environ["JEV_KEY_CMD"] = cmd
                    load(rel).api_key()

    def test_command_runs_without_a_shell(self):
        """Pipes, redirects and `;` must NOT be interpreted: the command is split with shlex and exec'd directly."""
        for rel in SCRIPTS:
            with self.subTest(script=rel), fake_home() as home:
                os.environ["JEV_KEY_CMD"] = f"echo a;b > {home}/pwned"
                key = load(rel).api_key()
                self.assertEqual(key, f"a;b > {home}/pwned")  # echo received `;` and `>` as plain words
                self.assertFalse(os.path.exists(os.path.join(home, "pwned")))  # nothing was redirected

    def test_tilde_and_env_vars_are_expanded_per_argument(self):
        for rel in SCRIPTS:
            with self.subTest(script=rel), fake_home():
                os.environ["JEV_TEST_VALUE"] = "expanded"
                os.environ["JEV_KEY_CMD"] = "printf $JEV_TEST_VALUE"
                self.assertEqual(load(rel).api_key(), "expanded")
                os.environ.pop("JEV_TEST_VALUE")

    def test_errors_never_contain_the_key(self):
        for rel in SCRIPTS:
            with self.subTest(script=rel), fake_home():
                os.environ["JEV_KEY_CMD"] = "sh -c 'echo sk-or-SECRET-VALUE >&2; exit 3'"
                with self.assertRaises(RuntimeError) as cm:
                    load(rel).api_key()
                self.assertNotIn("SECRET-VALUE", str(cm.exception))

    def test_jev_skill_caller_resolves_the_same_way(self):
        with fake_home() as home:
            write(home, ".config/jev/key_cmd", "printf skill-key")
            self.assertEqual(load("claude/skills/jev/scripts/jev.py").api_key(), "skill-key")
        with fake_home():
            with self.assertRaises(SystemExit):
                load("claude/skills/jev/scripts/jev.py").api_key()


class TestEnvConfiguration(unittest.TestCase):
    def test_defaults(self):
        for rel in SCRIPTS[:2]:
            with fake_home():
                m = load(rel)
                self.assertEqual(m.ENDPOINT, "https://openrouter.ai/api/alpha/decisions")
                self.assertEqual(m.MODEL, "typesafe/jev-1.13")
                self.assertEqual((m.MIN_CONFIDENCE, m.FOLLOWUP_CUTOFF, m.HTTP_TIMEOUT), (0.60, 0.50, 1.5))

    def test_overrides_and_bad_values(self):
        for rel in SCRIPTS[:2]:
            with fake_home({"JEV_MODEL": "typesafe/jev-next", "JEV_ROUTER_MIN_CONFIDENCE": "0.8",
                            "JEV_HTTP_TIMEOUT": "not-a-number"}):
                m = load(rel)
                self.assertEqual(m.MODEL, "typesafe/jev-next")
                self.assertEqual(m.MIN_CONFIDENCE, 0.8)
                self.assertEqual(m.HTTP_TIMEOUT, 1.5)  # a typo falls back instead of crashing the hook
        for rel in SCRIPTS[2:]:
            with fake_home({"JEV_SKILL_MIN_CONFIDENCE": "0.9"}):
                self.assertEqual(load(rel).THRESHOLD, 0.9)


if __name__ == "__main__":
    unittest.main()
