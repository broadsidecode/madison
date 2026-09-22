"""Synthetic CapCut fixtures: no private projects or media are included."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from timeline_reviewer.capcut import CapCutError, file_hash, inspect_project


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(root):
    project = root / "editor-project"
    project.mkdir()
    media = root / "synthetic.mp4"
    media.write_bytes(b"synthetic bytes for mocked tool tests only")
    draft = {"id": "timeline-one", "name": "First", "duration": 6_000_000, "fps": 30,
             "canvas_config": {"width": 1920, "height": 1080},
             "materials": {"videos": [{"id": "material-one", "path": str(media), "duration": 12_000_000}]},
             "tracks": [{"type": "video", "segments": [{
                 "id": "segment-one", "material_id": "material-one",
                 "source_timerange": {"start": 2_000_000, "duration": 2_000_000},
                 "target_timerange": {"start": 1_000_000, "duration": 2_000_000},
                 "visible": True, "volume": .7, "speed": 1}]}]}
    write_json(project / "draft_content.json", draft)
    write_json(project / "timeline_layout.json", {"dockItems": [{
        "timelineIds": ["timeline-one", "timeline-two", "timeline-three"],
        "timelineNames": ["First", "Second", "Third"]}]})
    for index, identity in enumerate(("timeline-one", "timeline-two", "timeline-three")):
        value = copy.deepcopy(draft)
        value["id"] = identity
        value["name"] = ("First", "Second", "Third")[index]
        value["tracks"][0]["segments"][0]["target_timerange"]["start"] += index * 1_000_000
        write_json(project / "Timelines" / identity / "draft_content.json", value)
    return project, draft, media


class CapCutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project, self.draft, self.media = fixture(self.root)
        self.selected = self.project / "Timelines/timeline-one/draft_content.json"

    def test_selects_exact_second_of_three_without_reading_other_drafts(self):
        # Unselected drafts are deliberately invalid: successful selection proves isolation.
        self.selected.write_text("not json")
        (self.project / "Timelines/timeline-three/draft_content.json").write_text("not json")
        report = inspect_project(self.project, "Second")
        self.assertEqual(report["selected_timeline_id"], "timeline-two")
        self.assertEqual(report["segments"][0]["target_start"], 2)
        self.assertEqual(len(report["source_files"]), 3)

    def test_missing_name_does_not_fall_back_to_root(self):
        with self.assertRaisesRegex(CapCutError, "not found"):
            inspect_project(self.project, "Missing")

    def test_duplicate_name_is_ambiguous(self):
        write_json(self.project / "timeline_layout.json", {"dockItems": [{
            "timelineIds": ["timeline-one", "timeline-two"], "timelineNames": ["First", "First"]}]})
        with self.assertRaisesRegex(CapCutError, "ambiguous"):
            inspect_project(self.project, "First")

    def test_identity_mismatch_rejected(self):
        self.draft["id"] = "wrong"
        write_json(self.selected, self.draft)
        with self.assertRaisesRegex(CapCutError, "does not match"):
            inspect_project(self.project, "First")

    def test_microseconds_hidden_volume_and_source_preservation(self):
        segment = self.draft["tracks"][0]["segments"][0]
        segment["visible"] = False
        segment["source_timerange"]["start"] = 2_000_501
        write_json(self.selected, self.draft)
        before = {str(p): file_hash(p) for p in self.project.rglob("*.json")}
        report = inspect_project(self.project, "First")
        actual = report["segments"][0]
        self.assertEqual(actual["source_start"], 2.000501)
        self.assertEqual(actual["source_timerange_us"]["start"], 2_000_501)
        self.assertTrue(actual["hidden"])
        self.assertEqual(actual["volume"], .7)
        self.assertEqual(before, {str(p): file_hash(p) for p in self.project.rglob("*.json")})

    def test_missing_media_actionable(self):
        self.draft["materials"]["videos"][0]["path"] = "absent.mp4"
        write_json(self.selected, self.draft)
        report = inspect_project(self.project, "First")
        self.assertIn("Relink", report["missing_media"][0]["detail"])

    def test_effect_keyframe_and_fade_diagnosed(self):
        segment = self.draft["tracks"][0]["segments"][0]
        segment.update({"enable_color_match_adjust": True, "common_keyframes": [{"id": "key"}],
                        "extra_material_refs": ["fade"]})
        self.draft["materials"]["audio_fades"] = [{"id": "fade", "fade_in_duration": 300_000}]
        write_json(self.selected, self.draft)
        codes = {item["code"] for item in inspect_project(self.project, "First")["unsupported"]}
        self.assertTrue({"enable_color_match_adjust", "common_keyframes", "extra_material"} <= codes)

    def test_invalid_ranges_and_nonfinite_rejected(self):
        self.draft["tracks"][0]["segments"][0]["volume"] = float("nan")
        write_json(self.selected, self.draft)
        with self.assertRaises(CapCutError):
            inspect_project(self.project, "First")

    def test_network_and_nul_media_rejected(self):
        for value in ("https://example.invalid/file.mp4", "\\\\server\\share\\file.mp4", "bad\x00path"):
            self.draft["materials"]["videos"][0]["path"] = value
            write_json(self.selected, self.draft)
            with self.subTest(value=value), self.assertRaises(CapCutError):
                inspect_project(self.project, "First")

    def test_traversal_timeline_rejected(self):
        write_json(self.project / "timeline_layout.json", {"dockItems": [{
            "timelineIds": ["../../other"], "timelineNames": ["First"]}]})
        with self.assertRaises(CapCutError):
            inspect_project(self.project, "First")

    def test_track_volume_zero_and_other_non_neutral_gains_are_findings(self):
        for gain in (0, .5, 2):
            self.draft["tracks"][0]["volume"] = gain
            write_json(self.selected, self.draft)
            with self.subTest(gain=gain):
                self.assertTrue(any(item["code"] == "track_volume" and item["active"]
                                    for item in inspect_project(self.project, "First")["unsupported"]))
        for gain in (None, 1):
            self.draft["tracks"][0]["volume"] = gain
            write_json(self.selected, self.draft)
            with self.subTest(gain=gain):
                self.assertFalse(any(item["code"] == "track_volume"
                                     for item in inspect_project(self.project, "First")["unsupported"]))

    def test_flip_requires_actual_booleans(self):
        segment = self.draft["tracks"][0]["segments"][0]
        for axis in ("horizontal", "vertical"):
            for invalid in ("false", "true", 0, 1, None):
                segment["clip"] = {"flip": {axis: invalid}}
                write_json(self.selected, self.draft)
                with self.subTest(axis=axis, invalid=invalid), self.assertRaisesRegex(CapCutError, "must be a boolean"):
                    inspect_project(self.project, "First")
        segment["clip"] = {"flip": {"horizontal": True, "vertical": False}}
        write_json(self.selected, self.draft)
        self.assertEqual(inspect_project(self.project, "First")["segments"][0]["transform"]["flip"],
                         {"horizontal": True, "vertical": False})

    def test_audio_flags_require_actual_booleans(self):
        owners = ((self.draft["materials"]["videos"][0], "has_sound_separated"),
                  (self.draft["tracks"][0]["segments"][0], "is_tone_modify"))
        for owner, key in owners:
            for invalid in ("false", "true", 0, 1, None):
                owner[key] = invalid
                write_json(self.selected, self.draft)
                with self.subTest(key=key, invalid=invalid), self.assertRaisesRegex(CapCutError, "must be a boolean"):
                    inspect_project(self.project, "First")
            owner[key] = False


if __name__ == "__main__":
    unittest.main()
