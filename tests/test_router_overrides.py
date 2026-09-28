"""Tags, rules and the decision logic. Everything here is offline: tags and force rules never call Jev."""
import json
import os
import unittest

from tests.helpers import ROUTERS, fake_home, load, write


class RouterCase(unittest.TestCase):
    """Runs every test once per platform (Claude Code, Codex)."""

    def each_platform(self):
        for name, cfg in ROUTERS.items():
            with self.subTest(platform=name), fake_home() as home:
                write(home, cfg["baseline_file"], cfg["sonnet_like"])
                yield name, cfg, home, load(cfg["path"])


class TestTags(RouterCase):
    def test_size_tags_delegate_or_stay(self):
        for _, cfg, _, m in self.each_platform():
            d, ctx = m.route("@tiny what is 2+2")
            self.assertEqual(d["outcome"], "delegated")
            self.assertEqual(d["agent"], "jev-tiny")
            self.assertIn("leave that tag out", ctx)
            d, ctx = m.route("@everyday hi")  # same tier as the session default
            self.assertEqual(d["outcome"], "self:covered")
            self.assertIsNone(ctx)
            d, _ = m.route("@large summarise this")
            self.assertEqual((d["outcome"], d["agent"]), ("delegated", "jev-large"))
            d, _ = m.route("[hardest] think hard")  # bracket form
            self.assertEqual((d["outcome"], d["agent"]), ("delegated", "jev-hardest"))

    def test_here_never_delegates(self):
        for _, _, _, m in self.each_platform():
            d, ctx = m.route("@here just answer")
            self.assertEqual(d["outcome"], "self:here")
            self.assertIsNone(ctx)

    def test_case_and_whitespace(self):
        for _, _, _, m in self.each_platform():
            self.assertEqual(m.route("@TINY loud")[0]["outcome"], "delegated")
            self.assertEqual(m.route("   @tiny padded")[0]["outcome"], "delegated")

    def test_model_name_aliases(self):
        for name, _, _, m in self.each_platform():
            if name == "claude":
                self.assertEqual(m.route("@haiku x")[0]["agent"], "jev-tiny")
                self.assertEqual(m.route("@opus x")[0]["agent"], "jev-large")
                self.assertEqual(m.route("@fable x")[0]["agent"], "jev-hardest")
                self.assertEqual(m.route("@sonnet x")[0]["outcome"], "self:covered")
            else:
                self.assertEqual(m.route("@luna x")[0]["agent"], "jev-tiny")
                self.assertEqual(m.route("@astra x")[0]["agent"], "jev-large")
                self.assertEqual(m.route("@ultra x")[0]["agent"], "jev-hardest")
                self.assertEqual(m.route("@sol x")[0]["outcome"], "self:covered")

    def test_things_that_look_like_tags_are_left_alone(self):
        for _, _, _, m in self.each_platform():
            for text in ("@src/main.py fix this", "please @tiny do this", "bob@tiny.com wrote",
                         "[WIP] fix build", "@largefile.txt please", "@unknown hello"):
                self.assertIsNone(m.parse_tag(text), text)

    def test_explicit_tag_is_honoured_even_as_a_downgrade(self):
        for _, _, _, m in self.each_platform():
            m.current_baseline = lambda: (2, "A bigger model")
            d, _ = m.route("@everyday cheap please")
            self.assertEqual((d["outcome"], d["agent"]), ("delegated", "jev-everyday"))

    def test_tiny_stays_local_when_the_session_is_already_the_weakest(self):
        for _, _, _, m in self.each_platform():
            m.current_baseline = lambda: (0, "Weakest")
            self.assertEqual(m.route("@tiny x")[0]["outcome"], "self:covered")

    def test_describe_is_human_readable(self):
        for name, cfg, _, m in self.each_platform():
            d, _ = m.route("@tiny x")
            text = m.describe(d)
            self.assertIn("you asked for a tiny job (@tiny)", text)
            self.assertIn(cfg["tiny_name"], text)
            self.assertIn(cfg["noun"], text)


class TestBaseline(unittest.TestCase):
    def test_claude_reads_settings_json(self):
        cases = {"haiku": 0, "sonnet": 1, "claude-opus-5-5": 2, "fable": 3, "opus[1m]": 2}
        for model, rank in cases.items():
            with self.subTest(model=model), fake_home() as home:
                write(home, ".claude/settings.json", json.dumps({"model": model}))
                self.assertEqual(load("claude/jev-router/router.py").current_baseline()[0], rank)

    def test_claude_unknown_or_missing_defaults_to_everyday(self):
        with fake_home():
            self.assertEqual(load("claude/jev-router/router.py").current_baseline()[0], 1)
        with fake_home() as home:
            write(home, ".claude/settings.json", "{ not json")
            self.assertEqual(load("claude/jev-router/router.py").current_baseline()[0], 1)

    def test_codex_reads_top_level_model_in_config_toml(self):
        cases = {"gpt-6-luna": 0, "gpt-6-sol": 1, "gpt-6-astra": 2}
        for model, rank in cases.items():
            with self.subTest(model=model), fake_home() as home:
                write(home, ".codex/config.toml",
                      f'model = "{model}"\nmodel_reasoning_effort = "low"\n[projects."/x"]\nmodel = "gpt-6-luna"\n')
                self.assertEqual(load("codex/jev-router/router.py").current_baseline()[0], rank)


class TestRules(RouterCase):
    def _rules(self, home, rules):
        path = write(home, "rules.json", json.dumps({"rules": rules}))
        os.environ["JEV_ROUTER_RULES"] = path

    def test_force_rules_and_precedence(self):
        for name, cfg, home, _ in self.each_platform():
            self._rules(home, [
                {"match": "internal-secret", "force": "here", "note": "private stays here"},
                {"match": "nuke prod", "force": "hardest", "note": "scary"},
            ])
            m = load(cfg["path"])
            d, _ = m.route("this has internal-secret in it")
            self.assertEqual(d["outcome"], "self:here")
            d, ctx = m.route("please nuke prod now")
            self.assertEqual((d["outcome"], d["agent"]), ("delegated", "jev-hardest"))
            self.assertIn("user-defined routing rule (scary)", ctx)
            d, _ = m.route("@tiny internal-secret")  # a tag beats a rule
            self.assertEqual(d["agent"], "jev-tiny")

    def test_bad_rules_are_skipped_not_fatal(self):
        for _, cfg, home, _ in self.each_platform():
            self._rules(home, [
                {"match": "(unclosed", "min": "large"},
                {"match": "x", "min": "banana"},
                "not a dict",
                {"match": "ok", "max": "everyday", "note": "cap"},
            ])
            self.assertEqual(len(load(cfg["path"]).load_rules()), 1)

    def test_malformed_or_missing_rules_file(self):
        for _, cfg, home, _ in self.each_platform():
            os.environ["JEV_ROUTER_RULES"] = write(home, "rules.json", "{ nope")
            self.assertEqual(load(cfg["path"]).load_rules(), [])
            os.environ["JEV_ROUTER_RULES"] = os.path.join(home, "does-not-exist.json")
            self.assertEqual(load(cfg["path"]).load_rules(), [])

    def test_min_floor_lifts_a_tiny_verdict_and_applies_when_jev_is_unsure(self):
        """Drive route() with a stubbed Jev answer so the floor logic is tested without any network."""
        for _, cfg, home, _ in self.each_platform():
            self._rules(home, [{"match": "dns", "min": "everyday", "note": "infra"}])
            m = load(cfg["path"])
            m.api_key = lambda: "unused"

            def stub(size, confidence, followup=0.05):
                return lambda message, key: {"answers": {
                    "size": {"choice": size, "confidence": confidence},
                    "followup": {"noul": followup}}, "usage": {"cost": 0}}

            m.ask_jev = stub("tiny", 0.9)
            d, _ = m.route("create a dns record")
            self.assertEqual((d["via"], d["size"], d["outcome"]), ("rule-min", "everyday", "self:covered"))
            m.ask_jev = stub("tiny", 0.3)  # unsure: a floor is a promise, so it still applies
            d, _ = m.route("create a dns record")
            self.assertEqual((d["via"], d["size"]), ("rule-min", "everyday"))
            m.ask_jev = stub("tiny", 0.9)
            d, _ = m.route("create a record")  # no match: normal behaviour, tiny always delegates
            self.assertEqual(d["outcome"], "delegated")
            m.ask_jev = stub("tiny", 0.9, followup=0.9)  # follow-ups are never pulled out of the session
            d, _ = m.route("yes, do the dns one")
            self.assertEqual(d["outcome"], "self:followup")

    def test_max_cap_only_applies_when_jev_is_sure(self):
        for _, cfg, home, _ in self.each_platform():
            self._rules(home, [{"match": "cheap", "max": "everyday", "note": "cap"}])
            m = load(cfg["path"])
            m.api_key = lambda: "unused"
            m.ask_jev = lambda message, key: {"answers": {"size": {"choice": "hardest", "confidence": 0.9},
                                                          "followup": {"noul": 0.0}}, "usage": {}}
            d, _ = m.route("keep it cheap")
            self.assertEqual((d["via"], d["size"]), ("rule-max", "everyday"))
            m.ask_jev = lambda message, key: {"answers": {"size": {"choice": "hardest", "confidence": 0.2},
                                                          "followup": {"noul": 0.0}}, "usage": {}}
            self.assertEqual(m.route("keep it cheap")[0]["outcome"], "self:unsure")


if __name__ == "__main__":
    unittest.main()
