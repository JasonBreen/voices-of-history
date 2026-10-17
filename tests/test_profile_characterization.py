from pathlib import Path
import sys
import json
import importlib.util
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "middleware"))
from leader_voice import leader_voice_rules
from profile_lore import ProfileLore, make_lore
from tfr_lore import TFRLore
from unified_mod import BASES, ENTRY, characterization_source


class ProfileCharacterizationTests(unittest.TestCase):
    def test_launcher_rejects_configuration_that_disagrees_with_detected_overhaul(self):
        spec = importlib.util.spec_from_file_location("profile_test_bridge", ROOT / "Launcher-Voices.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            userdir = root / "userdir"
            userdir.mkdir()
            (root / "middleware").mkdir()
            (userdir / "dlc_load.json").write_text(json.dumps({"enabled_mods": ["mod/MD_LOCAL_STANDARD.mod", ENTRY]}))
            (root / "middleware/config-unified-md.json").write_text(json.dumps({"game_profile": "the_fire_rises"}))
            with patch.object(bridge, "ROOT", root), patch.object(bridge, "USERDIR", userdir):
                with self.assertRaisesRegex(RuntimeError, "disagrees"):
                    bridge.main(["--check"])

    def fixture(self, root):
        for directory in ("history/countries", "common/characters", "localisation/english"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        (root / "localisation/english/people.yml").write_text('''l_english:
 LEADER_BIO: "Authored temperament. Later career is not current history."
 rational_desc: "Calculating, resistant to emotional appeals."
 unknown_desc: "[GetDynamicBiography]"
''', encoding="utf-8")
        (root / "common/characters/SOV.txt").write_text('''characters = {
\tSOV_test = {
 name = "Test Leader"
 country_leader = { desc = LEADER_BIO traits = { rational } }
 }
}
''')
        (root / "history/countries/USA - Test.txt").write_text('''# create_country_leader = { name = "Comment Person" desc = LEADER_BIO }
create_country_leader = {
 name = "Player Leader"
 traits = { rational }
}
''')

    def test_source_follows_all_selected_profiles_without_launching_or_changing_playsets(self):
        with tempfile.TemporaryDirectory() as temp:
            userdir = Path(temp)
            game = userdir / "game"
            (game / "history/countries").mkdir(parents=True)
            (userdir / "mod").mkdir()
            self.assertEqual(characterization_source([ENTRY], userdir, game), game.resolve())
            for base, profile in BASES.items():
                source = userdir / profile
                (source / "history/countries").mkdir(parents=True, exist_ok=True)
                descriptor = userdir / base
                metadata = 'name="Millennium Dawn: A Modern Day Mod"\nremote_file_id="2777392649"\n' if profile == "millennium_dawn" else ""
                descriptor.write_text(metadata + f'path="{source.as_posix()}"\n')
                if metadata:
                    (source / "descriptor.mod").write_text(metadata)
                before = descriptor.read_bytes()
                self.assertEqual(characterization_source([base, ENTRY], userdir, game), source.resolve())
                self.assertEqual(descriptor.read_bytes(), before)
            with self.assertRaises(RuntimeError):
                characterization_source([*BASES, ENTRY], userdir, game)

    def test_ambiguous_or_missing_source_does_not_fall_back_to_other_mod(self):
        with tempfile.TemporaryDirectory() as temp:
            userdir = Path(temp)
            (userdir / "mod").mkdir()
            base = "mod/MD_LOCAL_STANDARD.mod"
            path = userdir / base
            for text in ('name="No path"', 'path="missing"', 'path="one"\npath="two"'):
                path.write_text(text)
                with self.assertRaises(RuntimeError):
                    characterization_source([base, ENTRY], userdir, userdir)

    def test_current_identity_matches_authored_bio_and_history_traits_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            lore = ProfileLore(root, "cwic_dev")
            state = {"tag": "USA", "leader": "Player Leader"}
            world = {"SOV": {"tag": "SOV", "leader": "Test Leader"}}
            refs = lore.references(state, world, "leader:SOV")
            self.assertEqual({r["key"] for r in refs}, {"LEADER_BIO", "rational_desc"})
            self.assertEqual(sum(r["matched_name"] == "Test Leader" for r in refs), 2)
            self.assertNotIn("LEADER_BIO", {r["key"] for r in lore.references(state, world, "decree")})
            world["SOV"]["leader"] = "Replacement Leader"
            self.assertNotIn("LEADER_BIO", {r["key"] for r in lore.references(state, world, "leader:SOV")})
            self.assertFalse(any(c["name"] == "Comment Person" for c in lore.characters))

    def test_ambiguous_trait_variants_empty_bios_and_dynamic_text_stay_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root / "history/countries/SOV - Test.txt").write_text('''create_country_leader = {
 name = "Test Leader"
 desc = unknown_desc
 traits = { }
}
''')
            lore = ProfileLore(root, "millennium_dawn")
            refs = lore.references({"tag": "SOV", "leader": "Test Leader"}, {}, "decree")
            self.assertEqual(refs, [])
            self.assertIn("no unambiguous authored characterization", lore.briefing({"tag": "SOV", "leader": "Test Leader"}, {}, "decree"))

    def test_factory_keeps_tfr_lore_separate_and_uses_launcher_selected_source(self):
        for profile in ("vanilla", "millennium_dawn", "cwic_dev"):
            lore = make_lore({"game_profile": profile, "characterization": {"source": "selected"}, "tfr": {"source": "stale"}})
            self.assertIsInstance(lore, ProfileLore)
            self.assertEqual(str(lore.source), "selected")
        lore = make_lore({"game_profile": "the_fire_rises", "characterization": {"source": "selected"}, "tfr": {"source": "stale"}})
        self.assertIsInstance(lore, TFRLore)
        self.assertNotIsInstance(lore, ProfileLore)
        self.assertEqual(str(lore.source), "selected")

    def test_voice_rules_distinguish_mod_setting_without_speaker_stereotypes(self):
        expected = {"millennium_dawn": "MILLENNIUM DAWN CHARACTER VOICE",
                    "cwic_dev": "COLD WAR IRON CURTAIN CHARACTER VOICE",
                    "vanilla": "VANILLA CHARACTER VOICE", "the_fire_rises": "TFR CHARACTER VOICE"}
        for profile, label in expected.items():
            rules = leader_voice_rules(profile)
            self.assertIn(label, rules)
            self.assertIn("Never give a replacement leader the predecessor's voice", rules)


if __name__ == "__main__":
    unittest.main()
