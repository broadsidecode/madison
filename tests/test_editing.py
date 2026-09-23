"""Synthetic native editor tests; no Tesseract installation or film assets."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from timeline_reviewer.capcut import file_hash
from timeline_reviewer import editing


def put(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


class FakeCli:
    def __init__(self, source: Path, document: dict):
        self.documents = {str(source): deepcopy(document)}
        self.commands = []
        self.fail_commit = False

    def __call__(self, command):
        self.commands.append(command)
        project = str(Path(command[command.index("--project") + 1]))
        if "checkout" in command:
            value = self.documents.get(project, next(iter(self.documents.values())))
            put(Path(command[command.index("--output") + 1]), value)
        elif "commit" in command:
            if self.fail_commit:
                raise editing.EditError("synthetic rejection")
            self.documents[project] = json.loads(Path(command[command.index("--file") + 1]).read_text())
            Path(project).write_bytes(b"synthetic committed archive")
        return "{}"


class EditingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.project = self.root / "source.tsrct"
        self.project.write_bytes(b"original synthetic native archive")
        self.media = self.root / "top.mp4"
        self.media.write_bytes(b"safe video sample")
        self.secret = self.root / "other" / "secret.mp4"
        self.secret.parent.mkdir()
        self.secret.write_bytes(b"private media")
        self.manifest = {
            "schemaVersion": 1, "title": "Synthetic cut", "duration": 5, "fps": 24,
            "revision": "base", "videoUrl": "media/preview.mp4", "notes": [], "waveform": None,
            "tracks": [
                {"id": "top", "name": "Top", "kind": "video", "parked": False,
                 "clips": [self.clip("topclip", 1, "top.mp4")]},
                {"id": "lower", "name": "Lower", "kind": "video", "parked": False,
                 "clips": [self.clip("lowerclip", 2, "lower.mp4")]},
                {"id": "music", "name": "Music", "kind": "audio", "parked": False,
                 "clips": [self.clip("soundclip", 3, "sound.wav")]},
            ]}
        put(self.bundle / "data.json", self.manifest)
        self.native = {"duration": 5, "dimensions": {"width": 1080, "height": 1920},
                       "composition": {"id": "main", "layers": [
            self.layer("topclip", 1, "top.mp4", "Video", str(self.media)),
            self.layer("lowerclip", 2, "lower.mp4", "Video", str(self.secret)),
            self.layer("soundclip", 3, "sound.wav", "Audio", None)],
            "dynamics": {"entries": []}}, "extra": {"keep": [1, 2, 3]}}
        self.engine = FakeCli(self.project, self.native)
        self.cli_patch = patch.object(editing, "resolve_cli", return_value="synthetic-tsrct")
        self.run_patch = patch.object(editing, "_run", side_effect=self.engine)
        self.cli_patch.start()
        self.run_patch.start()
        self.addCleanup(self.cli_patch.stop)
        self.addCleanup(self.run_patch.stop)
        self.session = editing.EditSession(self.bundle, self.project,
                                           media_roots=[self.media.parent])

    def clip(self, clip_id, layer_id, filename):
        return {"id": clip_id, "layerId": layer_id, "label": clip_id,
                "sourceFilename": filename, "start": 0, "end": 4, "duration": 4,
                "sourceStart": 2, "sourceEnd": 6, "speed": 1, "hidden": False,
                "volume": 1, "colorPending": False}

    def layer(self, clip_id, layer_id, filename, kind, source_path):
        description = {"source_segment_id": clip_id}
        if source_path:
            description["source_path"] = source_path
        return {"id": layer_id, "type": kind, "name": filename,
                "description": json.dumps(description), "isHidden": False,
                "activeRange": {"start": 0, "duration": 4000},
                "sourceRange": {"start": 2000, "duration": 4000},
                "sourceIntrinsicDuration": 10000,
                "source": {"assetId": f"asset-{layer_id}"}, "volume": 1,
                "transform": {"position": [100, 200], "scale": [100, 100],
                              "rotation": 0, "opacity": 100} if kind == "Video" else None,
                "preserveUnknown": {"opacityCurve": [1, .5]}}

    def op(self, kind="remove", clip="topclip", layer=1, **kwargs):
        return {"type": kind, "clipId": clip, "layerId": layer, **kwargs}

    def find(self, state, cid):
        return next(c for track in state["manifest"]["tracks"] for c in track["clips"] if c["id"] == cid)

    def test_remove_reveals_lower_clip_and_undo_is_replacement(self):
        first = self.session.state()
        original = file_hash(self.project)
        staged = self.session.save_draft([self.op()], first["token"])
        self.assertTrue(self.find(staged, "topclip")["hidden"])
        self.assertFalse(self.find(staged, "lowerclip")["hidden"])
        self.assertEqual(file_hash(self.project), original)
        self.assertNotEqual(staged["token"], first["token"])
        self.assertEqual(staged["sourceRevision"], first["sourceRevision"])
        undone = self.session.save_draft([], staged["token"])
        self.assertFalse(self.find(undone, "topclip")["hidden"])
        with self.assertRaisesRegex(editing.EditError, "another tab"):
            self.session.save_draft([self.op()], staged["token"])
        self.assertFalse(any("commit" in cmd for cmd in self.engine.commands))

    def test_draft_survives_a_new_session(self):
        staged = self.session.save_draft([self.op()], self.session.state()["token"])
        reopened = editing.EditSession(self.bundle, self.project,
                                       media_roots=[self.media.parent])
        resumed = reopened.state()
        self.assertEqual(resumed["token"], staged["token"])
        self.assertEqual(resumed["operations"], [self.op()])
        self.assertTrue(self.find(resumed, "topclip")["hidden"])

    def test_duplicate_native_layer_binding_is_rejected_on_open(self):
        self.manifest["tracks"][1]["clips"][0]["layerId"] = 1
        put(self.bundle / "data.json", self.manifest)
        with self.assertRaisesRegex(editing.EditError, "same native layer ID"):
            editing.EditSession(self.bundle, self.project)

    def test_duplicate_native_layer_binding_in_saved_base_is_rejected(self):
        self.session.save_draft([self.op()], self.session.state()["token"])
        record = json.loads(self.session.draft_path.read_text(encoding="utf-8"))
        record["baseManifest"]["tracks"][1]["clips"][0]["layerId"] = 1
        put(self.session.draft_path, record)
        with self.assertRaisesRegex(editing.EditError, "same native layer ID"):
            editing.EditSession(self.bundle, self.project)

    def test_unchanged_poll_reuses_native_and_manifest_hashes(self):
        with patch.object(editing, "file_hash", wraps=editing.file_hash) as hash_file:
            self.session.state()
            self.session.state()
            self.session.source_files()
        hash_file.assert_not_called()

    def test_trim_and_volume_preserve_unedited_native_fields(self):
        state = self.session.state()
        ops = [self.op("trim", start=1, end=3, sourceStart=3, sourceEnd=5),
               self.op("volume", volume=.35)]
        draft = self.session.save_draft(ops, state["token"])
        self.assertEqual(self.find(draft, "topclip")["start"], 1)
        self.assertEqual(self.find(draft, "topclip")["sourceStart"], 3)
        self.assertEqual(self.find(draft, "topclip")["volume"], .35)
        effective, desired = self.session._apply(ops, self.session._record()["baseManifest"], self.native)
        self.assertEqual(desired["composition"]["layers"][0]["activeRange"], {"start": 1000, "duration": 2000})
        self.assertEqual(desired["composition"]["layers"][0]["sourceRange"], {"start": 3000, "duration": 2000})
        self.assertEqual(desired["composition"]["layers"][0]["preserveUnknown"], self.native["composition"]["layers"][0]["preserveUnknown"])
        self.assertEqual(desired["composition"]["layers"][1:], self.native["composition"]["layers"][1:])
        self.assertEqual(desired["extra"], self.native["extra"])

    def test_invalid_or_hazardous_operations_are_rejected(self):
        base = self.session.state()["token"]
        cases = [
            [self.op(layer=2)], [self.op(clip="missing")],
            [self.op("delete")], [self.op(extra=True)],
            [self.op("trim", start=1, end=3, sourceStart=2, sourceEnd=4)],
            [self.op("trim", start=0, end=6, sourceStart=2, sourceEnd=8)],
            [self.op("volume", volume=float("nan"))],
            [self.op("volume", volume=3)],
            [self.op(), self.op()],
        ]
        for operations in cases:
            with self.subTest(operations=operations), self.assertRaises(editing.EditError):
                self.session.save_draft(operations, base)
        self.assertFalse(self.session.draft_path.exists())
        self.native["composition"]["layers"][0]["description"] = json.dumps({"source_segment_id": "different"})
        self.session._native = self.native
        with self.assertRaisesRegex(editing.EditError, "identity"):
            self.session.save_draft([self.op()], base)

    def test_dynamics_and_retime_need_manual_review(self):
        self.native["composition"]["dynamics"]["entries"] = [
            {"target": {"kind": "layer", "layerId": 1, "propertyType": "volume"}}]
        self.session._native = self.native
        base = self.session.state()["token"]
        with self.assertRaisesRegex(editing.EditError, "animation"):
            self.session.save_draft([self.op("volume", volume=.2)], base)
        with self.assertRaisesRegex(editing.EditError, "animation"):
            self.session.save_draft([self.op("trim", start=1, end=3, sourceStart=3, sourceEnd=5)], base)
        self.session.save_draft([self.op()], base)

    def test_malformed_dynamics_reject_trim_and_volume_but_allow_remove(self):
        bad_entries = [None, {}, {"target": "layer"},
                       {"target": {"kind": "mystery", "layerId": 2, "propertyType": "volume"}},
                       {"target": {"kind": "layer", "layerId": 2}},
                       {"target": {"kind": "layer", "layerId": 2,
                                   "propertyType": "volume", "unknown": True}}]
        for entry in bad_entries:
            with self.subTest(entry=entry):
                self.native["composition"]["dynamics"]["entries"] = [entry]
                self.session._native = self.native
                token = self.session.state()["token"]
                with self.assertRaisesRegex(editing.EditError, "animation"):
                    self.session.save_draft([self.op("volume", volume=.5)], token)
                with self.assertRaisesRegex(editing.EditError, "animation"):
                    self.session.save_draft([self.op("trim", start=1, end=3,
                                                    sourceStart=3, sourceEnd=5)], token)
                safe = self.session.save_draft([self.op()], token)
                self.assertTrue(self.find(safe, "topclip")["hidden"])

    def test_disabled_source_audio_cannot_be_unmuted_by_slider(self):
        self.native["composition"]["layers"][0]["volume"] = None
        self.manifest["tracks"][0]["clips"][0]["volume"] = None
        put(self.bundle / "data.json", self.manifest)
        # The bundle changed before the session was opened; construct a fresh one.
        fresh = editing.EditSession(self.bundle, self.project, media_roots=[self.media.parent])
        fresh._native = self.native
        token = fresh.state()["token"]
        with self.assertRaisesRegex(editing.EditError, "disabled source audio"):
            fresh.save_draft([self.op("volume", volume=.5)], token)
        muted = fresh.save_draft([self.op("volume", volume=0)], token)
        self.assertEqual(self.find(muted, "topclip")["volume"], 0)

    def test_source_preview_requires_allowlisted_existing_matching_video(self):
        mapping = self.session.source_files()
        self.assertIn("topclip", mapping)
        self.assertEqual(mapping["topclip"]["path"], self.media)
        self.assertEqual(mapping["topclip"]["canvas"], {"width": 1080, "height": 1920})
        self.assertNotIn("lowerclip", mapping)
        self.assertNotIn("soundclip", mapping)
        self.assertNotIn("path", json.dumps(self.session.state()).lower())
        self.media.rename(self.media.with_suffix(".old"))
        self.assertNotIn("topclip", self.session.source_files())

    def test_commit_makes_fresh_native_version_and_clears_draft(self):
        before = file_hash(self.project)
        staged = self.session.save_draft([self.op()], self.session.state()["token"])
        committed = self.session.commit(staged["token"])
        saved = Path(committed["savedProject"])
        self.assertTrue(saved.is_file())
        self.assertTrue(saved.is_relative_to(self.project.parent))
        self.assertNotEqual(saved, self.project)
        self.assertEqual(file_hash(self.project), before)
        self.assertEqual(committed["operations"], [])
        self.assertTrue(committed["renderPending"])
        self.assertTrue(self.find(committed, "topclip")["hidden"])
        self.assertEqual(self.engine.documents[str(saved.parent.with_name("." + saved.parent.name + ".pending") / saved.name)]["composition"]["layers"][1:], self.native["composition"]["layers"][1:])
        with self.assertRaisesRegex(editing.EditError, "another tab"):
            self.session.commit(staged["token"])

    def test_failed_commit_retains_original_and_no_final_version(self):
        before = file_hash(self.project)
        staged = self.session.save_draft([self.op()], self.session.state()["token"])
        self.engine.fail_commit = True
        with self.assertRaisesRegex(editing.EditError, "diagnostic copy retained"):
            self.session.commit(staged["token"])
        self.assertEqual(file_hash(self.project), before)
        self.assertEqual(self.session.state()["operations"], [self.op()])

    def test_changed_native_or_manifest_blocks_edits(self):
        token = self.session.state()["token"]
        self.project.write_bytes(b"unexpected external replacement")
        with self.assertRaisesRegex(editing.EditError, "changed outside"):
            self.session.save_draft([self.op()], token)

    def test_same_size_external_change_with_new_mtime_is_detected(self):
        original = self.project.read_bytes()
        old = self.project.stat()
        self.project.write_bytes(original[::-1])
        os.utime(self.project, ns=(old.st_atime_ns, old.st_mtime_ns + 10_000_000))
        self.assertEqual(self.project.stat().st_size, old.st_size)
        with self.assertRaisesRegex(editing.EditError, "changed outside"):
            self.session.state()

    def test_commit_forces_hash_even_when_size_and_mtime_are_restored(self):
        staged = self.session.save_draft([self.op()], self.session.state()["token"])
        original = self.project.read_bytes()
        old = self.project.stat()
        self.project.write_bytes(original[::-1])
        os.utime(self.project, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertEqual((self.project.stat().st_size, self.project.stat().st_mtime_ns),
                         (old.st_size, old.st_mtime_ns))
        with self.assertRaisesRegex(editing.EditError, "changed outside"):
            self.session.commit(staged["token"])

    def test_changed_manifest_blocks_a_saved_draft(self):
        staged = self.session.save_draft([self.op()], self.session.state()["token"])
        self.manifest["title"] = "Changed elsewhere"
        put(self.bundle / "data.json", self.manifest)
        with self.assertRaisesRegex(editing.EditError, "changed outside"):
            self.session.commit(staged["token"])


if __name__ == "__main__":
    unittest.main()
