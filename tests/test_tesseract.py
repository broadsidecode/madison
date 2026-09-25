import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from timeline_reviewer.capcut import file_hash
from timeline_reviewer import tesseract
from test_capcut import fixture, write_json


class FakeEngine:
    """Mocks process output only; never requires or starts an editor."""
    def __init__(self):
        self.commands = []
        self.document = {"composition": {"id": "main", "layers": []}}

    def __call__(self, command):
        self.commands.append(command)
        if "--version" in command:
            return "tsrct 0.1.0"
        if "create" in command:
            Path(command[command.index("--project") + 1]).write_bytes(b"synthetic archive")
        if "import-video" in command:
            return json.dumps({"durationMs": 12000, "width": 1920, "height": 1080})
        if "checkout" in command:
            write_json(Path(command[command.index("--output") + 1]), self.document)
        if "commit" in command:
            self.document = json.loads(Path(command[command.index("--file") + 1]).read_text())
        if "apply" in command:
            actions = json.loads(Path(command[command.index("--actions") + 1]).read_text())
            for action in actions:
                layer = next(layer for layer in self.document["composition"]["layers"] if layer["id"] == action["layerId"])
                layer["playback"] = action["timeRemap"]
            self.document["duration"] = max(layer["activeRange"]["start"] + layer["activeRange"]["duration"]
                                            for layer in self.document["composition"]["layers"]) / 1000
        if "export" in command:
            Path(command[command.index("--output") + 1]).write_bytes(b"synthetic video")
        return "{}"


class TesseractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project, self.draft, self.media = fixture(self.root)
        self.selected = self.project / "Timelines/timeline-one/draft_content.json"
        self.output = self.root / "new-import"
        self.engine = FakeEngine()

    def run_import(self, **kwargs):
        with patch.object(tesseract, "resolve_cli", return_value="fake-tsrct"), \
                patch.object(tesseract, "_run", side_effect=self.engine):
            return tesseract.import_project(self.project, "First", self.output, **kwargs)

    def test_import_native_layers_and_source_hashes(self):
        before = file_hash(self.media)
        report = self.run_import()
        layer = self.engine.document["composition"]["layers"][0]
        self.assertEqual(layer["sourceRange"], {"start": 2000, "duration": 2000})
        self.assertEqual(layer["activeRange"], {"start": 1000, "duration": 2000})
        self.assertEqual(layer["volume"], .7)
        self.assertEqual(layer["transform"]["position"], [960, 540])
        self.assertTrue(report["source_hashes_verified"])
        self.assertEqual(file_hash(self.media), before)
        self.assertFalse(report["rendered"])
        self.assertFalse(any("export" in command for command in self.engine.commands))

    def test_unsupported_fails_before_directory_or_cli(self):
        self.draft["tracks"][0]["segments"][0]["enable_color_match_adjust"] = True
        write_json(self.selected, self.draft)
        with patch.object(tesseract, "resolve_cli") as cli, self.assertRaisesRegex(tesseract.ImportError, "Unsupported active"):
            tesseract.import_project(self.project, "First", self.output)
        self.assertFalse(self.output.exists())
        cli.assert_not_called()

    def test_lossy_acceptance_is_recorded(self):
        self.draft["tracks"][0]["segments"][0]["enable_color_match_adjust"] = True
        write_json(self.selected, self.draft)
        report = self.run_import(allow_lossy=True)
        self.assertTrue(report["allow_lossy"])
        self.assertTrue(report["unsupported"])

    def test_hidden_clip_and_track_stack(self):
        self.draft["tracks"][0]["segments"][0]["visible"] = False
        top = json.loads(json.dumps(self.draft["tracks"][0]))
        top["segments"][0]["id"] = "overlay"
        top["segments"][0]["visible"] = True
        self.draft["tracks"].append(top)
        write_json(self.selected, self.draft)
        self.run_import()
        layers = self.engine.document["composition"]["layers"]
        self.assertEqual([layer["id"] for layer in layers], [2, 1])
        self.assertTrue(layers[1]["isHidden"])
        self.assertEqual(sum("import-video" in cmd for cmd in self.engine.commands), 1)

    def test_speed_remap_and_rounding(self):
        segment = self.draft["tracks"][0]["segments"][0]
        segment["source_timerange"] = {"start": 2_000_501, "duration": 1_900_000}
        segment["speed"] = .95
        write_json(self.selected, self.draft)
        self.run_import()
        actions = json.loads((self.output / "import-data/actions.json").read_text())
        action = actions[0]
        self.assertEqual(action["type"], "setFxLayerTimeRemap")
        self.assertEqual(action["timeRemap"]["keyframes"][0]["value"], 2001)
        self.assertEqual(action["timeRemap"]["keyframes"][1]["value"], 3901)
        self.assertEqual(self.engine.document["duration"], 6)
        self.assertEqual(self.engine.document["composition"]["layers"][0]["playback"], action["timeRemap"])
        self.assertTrue((self.output / "import-data/duration-restored.json").is_file())

    def test_existing_destination_untouched(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(tesseract.ImportError, "already exists"):
            self.run_import()
        self.assertEqual(sentinel.read_text(), "keep")

    def test_source_change_detected_and_failed_output_retained(self):
        original = self.engine
        def change_on_commit(command):
            value = original(command)
            if "commit" in command:
                self.media.write_bytes(b"changed by external actor")
            return value
        with patch.object(tesseract, "resolve_cli", return_value="fake-tsrct"), \
                patch.object(tesseract, "_run", side_effect=change_on_commit), \
                self.assertRaisesRegex(tesseract.ImportError, "Source changed"):
            tesseract.import_project(self.project, "First", self.output)
        report = json.loads((self.output / "import-report.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["source_hashes_verified"])

    def test_missing_media_fails_before_output(self):
        self.draft["materials"]["videos"][0]["path"] = "missing.mp4"
        write_json(self.selected, self.draft)
        with self.assertRaisesRegex(tesseract.ImportError, "Missing source"):
            self.run_import()
        self.assertFalse(self.output.exists())

    def test_platform_and_version_gates(self):
        with patch.object(tesseract.platform, "system", return_value="Linux"), \
                self.assertRaisesRegex(tesseract.ImportError, "Windows and macOS"):
            tesseract.resolve_cli()
        with patch.object(tesseract.platform, "system", return_value="Darwin"), \
                patch.object(tesseract.shutil, "which", return_value="fake-tsrct"), \
                patch.object(tesseract, "_run", return_value="tsrct 0.3.0 (abc)"), \
                self.assertRaisesRegex(tesseract.ImportError, "requires Tesseract 0.2.0 or 0.1.0"):
            tesseract.resolve_cli()
        for version in ("0.2.0", "0.1.0"):
            with patch.object(tesseract.platform, "system", return_value="Darwin"), \
                    patch.object(tesseract.shutil, "which", return_value="fake-tsrct"), \
                    patch.object(tesseract, "_run", return_value=f"tsrct {version} (abc)"):
                self.assertEqual(tesseract.resolve_cli(), "fake-tsrct")

    def test_import_records_detected_engine_version(self):
        original = self.engine.__call__

        def newer(command):
            return "tsrct 0.2.0 (abc)" if "--version" in command else original(command)
        with patch.object(tesseract, "resolve_cli", return_value="fake-tsrct"), \
                patch.object(tesseract, "_run", side_effect=newer):
            report = tesseract.import_project(self.project, "First", self.output)
        self.assertEqual(report["tesseract_version"], "0.2.0")

    def test_windows_prefers_newest_supported_install(self):
        base = self.root / "AppData" / "Tesseract"
        older = base / "public-cli/0.1.0-x86_64/bin/tsrct.exe"
        newer = base / "public-cli/0.2.0-x86_64/bin/tsrct.exe"
        shim = base / "bin/tsrct.cmd"
        for path in (older, newer, shim):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic file; never executed")
        with patch.object(tesseract.platform, "system", return_value="Windows"), \
                patch.dict(tesseract.os.environ, {"LOCALAPPDATA": str(base.parent)}), \
                patch.object(tesseract.shutil, "which", return_value=str(shim)), \
                patch.object(tesseract, "_run", return_value="tsrct 0.2.0") as run:
            self.assertEqual(tesseract.resolve_cli(), str(newer.resolve()))
        self.assertEqual(run.call_args.args[0], [str(newer.resolve()), "--version"])

    def test_render_is_explicit(self):
        self.assertTrue(self.run_import(render=True)["rendered"])
        self.assertTrue((self.output / "preview.mp4").is_file())

    def test_missing_audio_tool_fails_before_output(self):
        self.draft["tracks"][0]["type"] = "audio"
        self.draft["materials"]["audios"] = self.draft["materials"].pop("videos")
        write_json(self.selected, self.draft)
        with patch.object(tesseract.shutil, "which", return_value=None), \
                self.assertRaisesRegex(tesseract.ImportError, "ffprobe"):
            self.run_import()
        self.assertFalse(self.output.exists())

    def test_audio_container_extracts_only_into_output(self):
        self.draft["tracks"][0]["type"] = "audio"
        self.draft["materials"]["audios"] = self.draft["materials"].pop("videos")
        write_json(self.selected, self.draft)
        with patch.object(tesseract.shutil, "which", side_effect=lambda name: name), \
                patch.object(tesseract, "_probe_audio", return_value=12000):
            report = self.run_import()
        extract = next(cmd for cmd in self.engine.commands if cmd[0] == "ffmpeg")
        # Temp directories can use short Windows names or macOS /var aliases.
        # Compare their canonical locations, as the importer does.
        self.assertTrue(Path(extract[-1]).resolve().is_relative_to(self.output.resolve()))
        self.assertIn("-n", extract)
        self.assertTrue(report["source_hashes_verified"])

    def test_small_source_overrun_preserves_range_and_intrinsic_duration(self):
        self.draft["tracks"][0]["segments"][0]["source_timerange"]["start"] = 10_049_000
        write_json(self.selected, self.draft)
        report = self.run_import()
        layer = self.engine.document["composition"]["layers"][0]
        self.assertEqual(layer["sourceIntrinsicDuration"], 12000)
        self.assertEqual(layer["sourceRange"]["start"] + layer["sourceRange"]["duration"], 12049)
        self.assertTrue(any("49 ms" in text for text in report["warnings"]))

    def test_muted_track_fails_before_cli_or_output(self):
        self.draft["tracks"][0]["volume"] = 0
        write_json(self.selected, self.draft)
        with patch.object(tesseract, "resolve_cli") as cli, \
                self.assertRaisesRegex(tesseract.ImportError, "track_volume"):
            tesseract.import_project(self.project, "First", self.output)
        cli.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_windows_official_shim_resolves_to_native_without_executing_batch(self):
        base = self.root / "AppData" / "Tesseract"
        native = base / "public-cli/0.1.0-x86_64/bin/tsrct.exe"
        shim = base / "bin/tsrct.cmd"
        native.parent.mkdir(parents=True)
        shim.parent.mkdir(parents=True)
        native.write_bytes(b"synthetic executable; never executed")
        shim.write_text("synthetic shim; never executed")
        with patch.object(tesseract.platform, "system", return_value="Windows"), \
                patch.dict(tesseract.os.environ, {"LOCALAPPDATA": str(base.parent)}), \
                patch.object(tesseract.shutil, "which", return_value=str(shim)), \
                patch.object(tesseract, "_run", return_value="tsrct 0.1.0") as run:
            self.assertEqual(tesseract.resolve_cli(), str(native.resolve()))
            self.assertEqual(tesseract.resolve_cli(str(shim)), str(native.resolve()))
        self.assertEqual(run.call_args_list[0].args[0], [str(native.resolve()), "--version"])
        self.assertEqual(run.call_args_list[1].args[0], [str(native.resolve()), "--version"])

    def test_windows_arbitrary_batch_launcher_rejected_without_execution(self):
        for extension in (".cmd", ".bat"):
            batch = self.root / ("external-launcher" + extension)
            batch.write_text("synthetic batch; never executed")
            with patch.object(tesseract.platform, "system", return_value="Windows"), \
                    patch.dict(tesseract.os.environ, {"LOCALAPPDATA": str(self.root / "AppData")}), \
                    patch.object(tesseract, "_run") as run, \
                    self.subTest(extension=extension), \
                    self.assertRaisesRegex(tesseract.ImportError, "actual native tsrct.exe using --tesseract"):
                tesseract.resolve_cli(str(batch))
            run.assert_not_called()

    def test_subprocess_boundary_rejects_windows_batch_tools(self):
        with patch.object(tesseract.platform, "system", return_value="Windows"), \
                patch.object(tesseract.subprocess, "run") as run, \
                self.assertRaisesRegex(tesseract.ImportError, "Batch launchers"):
            tesseract._run(["ffprobe.cmd", "media & special.mp4"])
        run.assert_not_called()

    def test_readback_accepts_only_known_neutral_transform_defaults(self):
        self.run_import()
        authored = json.loads(json.dumps(self.engine.document))
        final = json.loads(json.dumps(authored))
        transform = final["composition"]["layers"][0]["transform"]
        transform.update({"orientation": [0.0, 0.0, 0.0], "rotationX": 0.0,
                          "rotationY": 0.0, "skew": 0.0, "skewAxis": 0.0})
        tesseract._verify_readback(final, authored, [])
        for key, wrong in (("skew", 1), ("position", [0, 0]), ("unknownTransform", 0)):
            changed = json.loads(json.dumps(final))
            changed["composition"]["layers"][0]["transform"][key] = wrong
            with self.subTest(key=key), self.assertRaisesRegex(tesseract.ImportError, "transform"):
                tesseract._verify_readback(changed, authored, [])

    def test_readback_requires_exact_playback_remap_and_duration(self):
        segment = self.draft["tracks"][0]["segments"][0]
        segment["speed"] = .95
        segment["source_timerange"]["duration"] = 1_900_000
        write_json(self.selected, self.draft)
        self.run_import()
        authored = json.loads((self.output / "import-data/editable.json").read_text())
        actions = json.loads((self.output / "import-data/actions.json").read_text())
        final = json.loads(json.dumps(self.engine.document))
        tesseract._verify_readback(final, authored, actions)
        final["composition"]["layers"][0]["playback"]["keyframes"][1]["value"] += 1
        with self.assertRaisesRegex(tesseract.ImportError, "playback remap"):
            tesseract._verify_readback(final, authored, actions)
        final = json.loads(json.dumps(self.engine.document))
        final["duration"] -= 1
        with self.assertRaisesRegex(tesseract.ImportError, "timeline duration"):
            tesseract._verify_readback(final, authored, actions)


if __name__ == "__main__":
    unittest.main()
