"""The hook scripts as the host tool runs them: JSON on stdin, JSON on stdout, always exit 0, never leak content."""
import json
import os
import subprocess
import sys
import unittest

from tests.helpers import REPO, fake_home, write

TOOLS = {
    "claude": ("claude/jev-router/router.py", ".claude/jev-router", "claude/skill-picker/picker.py", ".claude/skill-picker"),
    "codex": ("codex/jev-router/router.py", ".codex/jev-router", "codex/skill-picker/picker.py", ".codex/skill-picker"),
}


def run(script, home, payload, *args):
    env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
    return subprocess.run([sys.executable, os.path.join(REPO, script), *args], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=30)


class TestRouterHook(unittest.TestCase):
    def test_off_means_silent(self):
        for tool, (router, *_rest) in TOOLS.items():
            with self.subTest(tool=tool), fake_home() as home:
                p = run(router, home, {"prompt": "[large] hello"})
                self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_tag_produces_status_line_and_delegation_context(self):
        for tool, (router, rdir, *_rest) in TOOLS.items():
            with self.subTest(tool=tool), fake_home() as home:
                write(home, f"{rdir}/enabled", "")
                p = run(router, home, {"prompt": "[large] summarise this", "session_id": "abcdef123456"})
                self.assertEqual(p.returncode, 0)
                out = json.loads(p.stdout)
                self.assertIn("you asked for a large job", out["systemMessage"])
                self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
                self.assertIn("jev-large", out["hookSpecificOutput"]["additionalContext"])

    def test_slash_commands_and_garbage_input_fail_open(self):
        for tool, (router, rdir, *_rest) in TOOLS.items():
            with self.subTest(tool=tool), fake_home() as home:
                write(home, f"{rdir}/enabled", "")
                self.assertEqual(run(router, home, {"prompt": "/help"}).stdout, "")
                p = subprocess.run([sys.executable, os.path.join(REPO, router)], input="not json", capture_output=True,
                                   text=True, env={"HOME": home, "PATH": os.environ.get("PATH", "")}, timeout=30)
                self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_missing_key_fails_open_and_is_logged_without_content(self):
        for tool, (router, rdir, *_rest) in TOOLS.items():
            with self.subTest(tool=tool), fake_home() as home:
                write(home, f"{rdir}/enabled", "")
                p = run(router, home, {"prompt": "please review CANARY-PRIVATE-TEXT-42"})  # no tag: would need Jev
                self.assertEqual((p.returncode, p.stdout), (0, ""))
                log = open(os.path.join(home, rdir, "log.jsonl")).read()
                self.assertIn("no OpenRouter key configured", log)
                self.assertNotIn("CANARY-PRIVATE-TEXT-42", log)

    def test_log_never_contains_message_text(self):
        for tool, (router, rdir, *_rest) in TOOLS.items():
            with self.subTest(tool=tool), fake_home() as home:
                write(home, f"{rdir}/enabled", "")
                run(router, home, {"prompt": "[tiny] CANARY-PRIVATE-TEXT-42"})
                log = open(os.path.join(home, rdir, "log.jsonl")).read()
                self.assertIn('"via": "tag"', log)
                self.assertNotIn("CANARY-PRIVATE-TEXT-42", log)


class TestPickerHook(unittest.TestCase):
    def test_off_means_silent(self):
        for tool, (_r, _d, picker, _pd) in TOOLS.items():
            with self.subTest(tool=tool), fake_home() as home:
                p = run(picker, home, {"prompt": "make a deck"}, "--hook")
                self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_missing_key_fails_open_and_log_has_no_message_text(self):
        for tool, (_r, _d, picker, pdir) in TOOLS.items():
            with self.subTest(tool=tool), fake_home() as home:
                write(home, f"{pdir}/enabled", "")
                p = run(picker, home, {"prompt": "make a deck about CANARY-PRIVATE-TEXT-42"}, "--hook")
                self.assertEqual((p.returncode, p.stdout), (0, ""))
                log = open(os.path.join(home, pdir, "log.jsonl")).read()
                self.assertIn('"outcome": "error"', log)
                self.assertNotIn("CANARY-PRIVATE-TEXT-42", log)


class TestCli(unittest.TestCase):
    def test_check_command_is_offline_and_status_works_when_empty(self):
        for tool, cmd in (("claude", "claude/jev-router/jev-router"), ("codex", "codex/jev-router/cx-router")):
            with self.subTest(tool=tool), fake_home() as home:
                # the CLI imports router.py from the installed location, so mirror the install layout
                base = ".claude" if tool == "claude" else ".codex"
                write(home, f"{base}/jev-router/router.py", open(os.path.join(REPO, f"{tool}/jev-router/router.py")).read())
                env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
                script = os.path.join(REPO, cmd)
                p = subprocess.run([sys.executable, script, "check", "@tiny hello"], capture_output=True, text=True, env=env, timeout=30)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("you asked for a tiny job", p.stdout)
                p = subprocess.run([sys.executable, script, "status"], capture_output=True, text=True, env=env, timeout=30)
                self.assertIn("OFF", p.stdout)
                self.assertIn("No messages routed yet", p.stdout)


if __name__ == "__main__":
    unittest.main()
