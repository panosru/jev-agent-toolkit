"""Skill picker: catalog building, overrides, wording, and fail-open behaviour."""
import json
import os
import unittest

from tests.helpers import fake_home, load, write

SKILL = """---
name: {name}
description: {desc}
{extra}---
body
"""


class TestCatalog(unittest.TestCase):
    def test_claude_catalog_reads_skills_and_honours_overrides(self):
        with fake_home() as home:
            write(home, ".claude/skills/alpha/SKILL.md", SKILL.format(name="alpha", desc="Do alpha things. More text here.", extra=""))
            write(home, ".claude/skills/hidden/SKILL.md",
                  SKILL.format(name="hidden", desc="User-only skill.", extra="disable-model-invocation: true\n"))
            write(home, ".claude/skills/beta/SKILL.md", SKILL.format(name="beta", desc="Do beta things.", extra=""))
            m = load("claude/skill-picker/picker.py")
            names = {s["name"] for s in m.build_catalog()["skills"]}
            self.assertIn("alpha", names)
            self.assertIn("beta", names)
            self.assertNotIn("hidden", names)  # the assistant cannot load user-only skills
            write(home, ".claude/skill-picker/overrides.json",
                  json.dumps({"lines": {"alpha": "Hand-written line"}, "exclude": ["beta"]}))
            skills = {s["name"]: s["line"] for s in m.build_catalog()["skills"]}
            self.assertEqual(skills["alpha"], "Hand-written line")
            self.assertNotIn("beta", skills)

    def test_fresh_install_with_no_settings_or_plugins_does_not_crash(self):
        with fake_home():
            m = load("claude/skill-picker/picker.py")
            self.assertIsInstance(m.build_catalog()["skills"], list)  # only the shipped built-ins, no exception

    def test_codex_catalog_scans_skills_and_plugin_cache_and_honours_overrides(self):
        with fake_home() as home:
            write(home, ".codex/skills/one/SKILL.md", SKILL.format(name="one", desc="First skill.", extra=""))
            write(home, ".codex/skills/.system/two/SKILL.md", SKILL.format(name="two", desc="Bundled skill.", extra=""))
            write(home, ".codex/plugins/cache/vendor/plug/1.0/skills/three/SKILL.md",
                  SKILL.format(name="three", desc="Plugin skill.", extra=""))
            write(home, ".codex/skill-picker/overrides.json", json.dumps({"exclude": ["two"], "lines": {"one": "Better"}}))
            m = load("codex/skill-picker/picker.py")
            skills = {s["name"]: s["line"] for s in m.build_catalog()["skills"]}
            self.assertEqual(sorted(skills), ["one", "three"])
            self.assertEqual(skills["one"], "Better")

    def test_one_line_and_frontmatter(self):
        with fake_home() as home:
            m = load("claude/skill-picker/picker.py")
            self.assertEqual(m.one_line("First sentence is long enough to stand alone here. Second sentence."),
                             "First sentence is long enough to stand alone here.")
            self.assertTrue(m.one_line("x" * 400).endswith("…"))
            path = write(home, "s/SKILL.md", '---\nname: "quoted"\ndescription: >\n  folded\n  text\n---\n')
            fm = m._frontmatter(path)
            self.assertEqual((fm["name"], fm["description"]), ("quoted", "folded text"))


class TestPrivateMessages(unittest.TestCase):
    """@here and force:here rules keep a message out of Jev entirely (checked before any key or network use)."""

    def _run(self, tool, home, prompt):
        import subprocess
        import sys
        from tests.helpers import REPO
        picker = f"{tool}/skill-picker/picker.py"
        p = subprocess.run([sys.executable, os.path.join(REPO, picker), "--hook"], input=json.dumps({"prompt": prompt}),
                           capture_output=True, text=True, env={"HOME": home, "PATH": os.environ.get("PATH", "")}, timeout=30)
        log = open(os.path.join(home, f".{tool if tool == 'codex' else 'claude'}", "skill-picker", "log.jsonl")).read()
        return p, log

    def test_here_tag_and_force_rule_are_never_sent(self):
        for tool in ("claude", "codex"):
            base = ".codex" if tool == "codex" else ".claude"
            with self.subTest(tool=tool), fake_home() as home:
                write(home, f"{base}/skill-picker/enabled", "")
                write(home, f"{base}/jev-router/rules.json",
                      json.dumps({"rules": [{"match": "client-acme", "force": "here", "note": "private"}]}))
                for prompt in ("@here summarise CANARY-42", "[here] summarise CANARY-42", "review client-acme CANARY-42"):
                    p, log = self._run(tool, home, prompt)
                    self.assertEqual((p.returncode, p.stdout), (0, ""))
                    self.assertIn('"outcome": "private"', log.splitlines()[-1])  # not "error": it never reached the key/network step
                    self.assertNotIn("CANARY-42", log)

    def test_other_messages_are_not_treated_as_private(self):
        for tool in ("claude", "codex"):
            base = ".codex" if tool == "codex" else ".claude"
            with self.subTest(tool=tool), fake_home() as home:
                write(home, f"{base}/skill-picker/enabled", "")
                p, log = self._run(tool, home, "please summarise this @heretic thing")
                self.assertNotIn('"outcome": "private"', log)  # falls through to the normal path (fails open: no key here)


class TestVerdictWording(unittest.TestCase):
    def test_three_outcomes(self):
        for rel in ("claude/skill-picker/picker.py", "codex/skill-picker/picker.py"):
            with self.subTest(script=rel), fake_home():
                m = load(rel)
                self.assertIn("picked `xlsx` (97% sure)", m.verdict({"confidence": 0.97, "skill": "xlsx"}))
                self.assertIn("no skill fits this (98% sure)", m.verdict({"confidence": 0.98, "skill": m.NONE}))
                self.assertIn("not sure which skill fits (best guess: `a`, 26%)", m.verdict({"confidence": 0.26, "skill": "a"}))
                # A low-confidence "none" is reported as none, matching how the hook logs it.
                self.assertIn("no skill fits", m.verdict({"confidence": 0.3, "skill": m.NONE}))


if __name__ == "__main__":
    unittest.main()
