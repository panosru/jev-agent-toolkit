"""install.sh against a throwaway HOME: merge, idempotency, preservation of user config, uninstall."""
import json
import os
import shutil
import subprocess
import unittest

from tests.helpers import REPO, fake_home, write

INSTALL = os.path.join(REPO, "install.sh")


@unittest.skipUnless(shutil.which("bash"), "bash not available")
class TestInstaller(unittest.TestCase):
    def sh(self, home, *args):
        env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
        return subprocess.run(["bash", INSTALL, *args], capture_output=True, text=True, env=env, timeout=120)

    def test_install_merge_idempotent_uninstall(self):
        with fake_home() as home:
            original = {"model": "sonnet", "hooks": {
                "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/usr/local/bin/my-own-hook"}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "/usr/local/bin/on-stop"}]}]}}
            settings = write(home, ".claude/settings.json", json.dumps(original))
            write(home, ".codex/config.toml", 'model = "gpt-6-sol"\n')

            p = self.sh(home, "--all", "--key-cmd", "printf demo")
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            cmds = [h["command"] for g in json.load(open(settings))["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
            self.assertEqual(len(cmds), 3)
            self.assertIn("/usr/local/bin/my-own-hook", cmds)  # the user's own hook is kept
            self.assertTrue(any(c.endswith("jev-router/router.py") for c in cmds))
            self.assertTrue(any(c.endswith("skill-picker/picker.py --hook") for c in cmds))
            self.assertEqual(json.load(open(settings))["model"], "sonnet")
            self.assertEqual(oct(os.stat(os.path.join(home, ".config/jev/key_cmd")).st_mode & 0o777), "0o600")
            for base, ext in ((".claude", "md"), (".codex", "toml")):
                for tier in ("tiny", "everyday", "large", "hardest"):
                    self.assertTrue(os.path.exists(os.path.join(home, base, "agents", f"jev-{tier}.{ext}")))
            # OFF by default: no enable flags
            self.assertFalse(os.path.exists(os.path.join(home, ".claude/jev-router/enabled")))

            self.sh(home, "--all")  # again: no duplicates
            cmds2 = [h["command"] for g in json.load(open(settings))["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
            self.assertEqual(cmds, cmds2)

            p = self.sh(home, "--all", "--uninstall")
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            self.assertEqual(json.load(open(settings)), original)  # exactly what the user had
            self.assertFalse(os.path.exists(os.path.join(home, ".codex/hooks.json")))
            self.assertFalse(os.path.exists(os.path.join(home, ".claude/agents/jev-tiny.md")))
            self.assertTrue(os.path.exists(os.path.join(home, ".claude/jev-router/rules.json")))  # user data kept

    def test_enable_flag_and_dry_run(self):
        with fake_home() as home:
            write(home, ".claude/settings.json", "{}")
            p = self.sh(home, "--claude", "--dry-run")
            self.assertEqual(p.returncode, 0)
            self.assertFalse(os.path.exists(os.path.join(home, ".claude/jev-router")))  # dry run writes nothing
            self.assertEqual(self.sh(home, "--claude", "--enable").returncode, 0)
            self.assertTrue(os.path.exists(os.path.join(home, ".claude/jev-router/enabled")))
            self.assertTrue(os.path.exists(os.path.join(home, ".claude/skill-picker/enabled")))

    def test_refuses_to_guess_when_nothing_is_installed(self):
        with fake_home() as home:
            p = self.sh(home)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("--claude", p.stderr)

    def test_broken_settings_json_is_not_overwritten(self):
        with fake_home() as home:
            settings = write(home, ".claude/settings.json", "{ this is not json")
            p = self.sh(home, "--claude")
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(open(settings).read(), "{ this is not json")


if __name__ == "__main__":
    unittest.main()
