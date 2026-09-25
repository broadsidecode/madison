"""Optional, experimental CapCut to Tesseract 0.2.0 or 0.1.0 conversion.

Only subprocess calls to separately installed tools are made. No installation,
license acceptance, network request, or modification of an input is performed.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

from .capcut import file_hash, inspect_project

# Newest first. Both share document formatVersion 1 and were verified against
# real engines; any other version is refused rather than guessed.
SUPPORTED_CLI_VERSIONS = ("0.2.0", "0.1.0")
SUPPORTED_TEXT = " or ".join(SUPPORTED_CLI_VERSIONS)
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
RENDER_CANVASES = {(1080, 1920), (1920, 1080), (1080, 1080),
                   (1080, 1350), (810, 1080), (1350, 1080)}


class ImportError(ValueError):
    """An optional import could not safely proceed."""


def _run(command: list[str]) -> str:
    if platform.system() == "Windows" and Path(command[0]).suffix.lower() in (".cmd", ".bat"):
        raise ImportError("Batch launchers are not supported; select a native executable to keep media paths outside a command shell")
    env = None
    if platform.system() == "Windows" and Path(command[0]).name.lower() == "tsrct.exe":
        # Obsidian ships a DXC DLL that shadows Tesseract's renderer through
        # PATH and makes native export exit 0xC000001D. Exclude only that one
        # directory from this child process; never change the user's PATH.
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            conflict = os.path.normcase(os.path.normpath(
                str(Path(local_app_data) / "Programs" / "Obsidian")))
            env = os.environ.copy()
            env["PATH"] = ";".join(entry for entry in env.get("PATH", "").split(";")
                                   if os.path.normcase(os.path.normpath(entry.strip('"'))) != conflict)
    try:
        result = subprocess.run(command, check=False, capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                stdin=subprocess.DEVNULL, timeout=3600, shell=False, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ImportError(f"Cannot execute {Path(command[0]).name}: {exc}") from exc
    if result.returncode:
        raise ImportError(f"{Path(command[0]).name} failed ({result.returncode}): "
                          + (result.stderr or result.stdout)[-3000:])
    return result.stdout.strip()


def _json_run(command: list[str]) -> dict:
    value = _run(command)
    try:
        result = json.loads(value) if value else {}
    except ValueError as exc:
        raise ImportError(f"{Path(command[0]).name} returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ImportError(f"{Path(command[0]).name} returned an unexpected JSON value")
    return result


def cli_version(executable: str) -> str:
    """Return the selected CLI's version, refusing any version not verified here."""
    version = _run([executable, "--version"])
    versions = re.findall(r"(?<![\d.])\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?(?![\d.])", version)
    if len(versions) != 1 or versions[0] not in SUPPORTED_CLI_VERSIONS:
        raise ImportError(f"This adapter requires Tesseract {SUPPORTED_TEXT}; the selected CLI reports {version!r}.")
    return versions[0]


def resolve_cli(cli: str | None = None) -> str:
    system = platform.system()
    if system not in ("Windows", "Darwin"):
        raise ImportError("Tesseract import supports Windows and macOS only; CapCut inspection works on this host.")
    local_app_data = os.environ.get("LOCALAPPDATA")
    windows_base = Path(local_app_data) / "Tesseract" if local_app_data else None
    # The official installer keeps each version in its own folder behind one
    # shim. Prefer the newest supported native executable that is present.
    windows_native = next((candidate for candidate in (
        windows_base / "public-cli" / f"{version}-x86_64" / "bin" / "tsrct.exe"
        for version in SUPPORTED_CLI_VERSIONS) if candidate.is_file()), None) if windows_base else None
    if cli:
        path = Path(cli).expanduser()
        resolved = str(path.resolve()) if path.is_file() else shutil.which(cli)
    else:
        resolved = shutil.which("tsrct")
        if system == "Windows" and windows_native and windows_native.is_file() and (
                not resolved or Path(resolved).suffix.lower() in (".cmd", ".bat")):
            resolved = str(windows_native.resolve())
        if not resolved:
            if system == "Windows":
                candidates = [windows_native] if windows_native else []
            else:
                candidates = [Path.home() / "Library/Application Support/Tesseract/bin/tsrct"]
            resolved = next((str(path) for path in candidates if path.is_file()), None)
    if not resolved:
        raise ImportError(f"Tesseract {SUPPORTED_TEXT} is not installed or not found. Install it separately and pass --tesseract; no installation was attempted.")
    if system == "Windows" and Path(resolved).suffix.lower() in (".cmd", ".bat"):
        official_shim = windows_base / "bin" / "tsrct.cmd" if windows_base else None
        if (official_shim and Path(resolved).resolve() == official_shim.resolve()
                and windows_native and windows_native.is_file()):
            resolved = str(windows_native.resolve())
        else:
            raise ImportError("Windows batch launchers are not supported. Pass the actual native tsrct.exe using --tesseract; the launcher was not executed.")
    cli_version(resolved)
    return resolved


def _ms_range(value: dict) -> dict:
    # Round endpoints independently so adjoining clips keep the same boundary.
    start = (value["start"] + 500) // 1000
    end = (value["start"] + value["duration"] + 500) // 1000
    if end <= start:
        raise ImportError("A clip is shorter than Tesseract's millisecond precision")
    return {"start": start, "duration": end - start}


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _probe_audio(path: Path, probe: str) -> int:
    info = _json_run([probe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,duration",
                      "-of", "json", str(path)])
    stream = next((item for item in info.get("streams", []) if item.get("codec_type") == "audio"), None)
    if stream is None:
        raise ImportError(f"No audio stream was found in {path.name}")
    try:
        duration = float(stream.get("duration", info.get("format", {}).get("duration")))
    except (ValueError, TypeError) as exc:
        raise ImportError(f"Cannot determine audio duration for {path.name}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ImportError(f"Invalid audio duration for {path.name}")
    return round(duration * 1000)


def _verify_sources(hashes: dict[str, str]) -> None:
    for filename, expected in hashes.items():
        path = Path(filename)
        if not path.is_file() or file_hash(path) != expected:
            raise ImportError(f"Source changed while importing: {path}. Close the editor and retry into a fresh destination.")


def _same_duration(actual, expected) -> bool:
    return (not isinstance(actual, bool) and isinstance(actual, (int, float))
            and math.isfinite(actual) and abs(actual - expected) <= 1e-6)


def _verify_readback(final: dict, authored: dict, actions: list[dict]) -> None:
    if not _same_duration(final.get("duration"), authored["duration"]):
        raise ImportError("Native readback changed the timeline duration")
    if final.get("dimensions") != authored["dimensions"]:
        raise ImportError("Native readback changed the canvas dimensions")
    actual_layers = final.get("composition", {}).get("layers", [])
    expected_layers = authored["composition"]["layers"]
    remaps = {action["layerId"]: action["timeRemap"] for action in actions}
    if len(actual_layers) != len(expected_layers):
        raise ImportError("Native readback has a different layer count")
    neutral_transform = {"orientation": [0, 0, 0], "rotationX": 0, "rotationY": 0,
                         "skew": 0, "skewAxis": 0}
    for actual, expected in zip(actual_layers, expected_layers):
        for field in ("id", "type", "activeRange", "sourceRange", "sourceIntrinsicDuration",
                      "isHidden", "volume", "preserveAudioPitch"):
            if actual.get(field) != expected.get(field):
                raise ImportError(f"Native readback changed {field} for layer {expected['id']}")
        for field, value in expected["source"].items():
            if actual.get("source", {}).get(field) != value:
                raise ImportError(f"Native readback changed source {field} for layer {expected['id']}")
        if "transform" in expected:
            transform = actual.get("transform")
            if not isinstance(transform, dict):
                raise ImportError(f"Native readback lost transform for layer {expected['id']}")
            for field, value in expected["transform"].items():
                if transform.get(field) != value:
                    raise ImportError(f"Native readback changed transform {field} for layer {expected['id']}")
            for field, value in transform.items():
                if field not in expected["transform"] and (field not in neutral_transform or value != neutral_transform[field]):
                    raise ImportError(f"Native readback added a non-neutral transform {field} for layer {expected['id']}")
        # In 0.1.0 and 0.2.0 the setFxLayerTimeRemap action persists as layer.playback.
        if expected["id"] in remaps:
            if actual.get("playback") != remaps[expected["id"]]:
                raise ImportError(f"Native readback changed playback remap for layer {expected['id']}")
        elif actual.get("playback"):
            raise ImportError(f"Native readback added an unexpected playback remap for layer {expected['id']}")


def import_project(project_dir: Path, timeline_name: str, output_dir: Path,
                   cli: str | None = None, allow_lossy: bool = False,
                   render: bool = False) -> dict:
    """Create editable native layers in a new directory, after strict preflight.

    ``allow_lossy`` permits diagnosed unsupported features to be omitted. It
    never permits missing media, unsafe paths, invalid time ranges or overwrite.
    Failed output is retained with a failure report, never deleted or reused.
    """
    project_dir = Path(project_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise ImportError("Destination already exists; choose a fresh output directory")
    if output.is_relative_to(project_dir):
        raise ImportError("Output must be outside the source CapCut project")
    report = inspect_project(project_dir, timeline_name)
    if report["missing_media"]:
        names = ", ".join(item["path"] or item["segment_id"] for item in report["missing_media"][:5])
        raise ImportError(f"Missing source media: {names}. Relink in CapCut before importing.")
    blocking = [item for item in report["unsupported"] if item["active"]]
    if blocking and not allow_lossy:
        codes = ", ".join(sorted({item["code"] for item in blocking}))
        raise ImportError(f"Unsupported active features: {codes}. Inspect the report first; --allow-lossy explicitly accepts their omission.")
    if not report["segments"]:
        raise ImportError("The selected timeline has no supported media segments")
    for segment in report["segments"]:
        _ms_range(segment["source_timerange_us"])
        _ms_range(segment["target_timerange_us"])
    canvas = report["canvas"]
    if render and (canvas["width"], canvas["height"]) not in RENDER_CANVASES:
        raise ImportError("This canvas size is not supported by Tesseract rendering; import without --render")
    executable = resolve_cli(cli)
    engine_version = cli_version(executable)
    audio = [segment for segment in report["segments"] if segment["type"] == "audio"]
    probe = shutil.which("ffprobe") if audio else None
    needs_extract = any(Path(segment["path"]).suffix.lower() not in AUDIO_EXTENSIONS for segment in audio)
    ffmpeg = shutil.which("ffmpeg") if needs_extract else None
    if audio and not probe:
        raise ImportError("Audio import requires separately installed ffprobe on PATH")
    if needs_extract and not ffmpeg:
        raise ImportError("Audio inside a video container requires separately installed FFmpeg on PATH")
    audio_durations = {path: _probe_audio(Path(path), probe)
                       for path in sorted({segment["path"] for segment in audio})}
    hashes = {item["path"]: item["sha256"] for item in report["source_files"]}
    for segment in report["segments"]:
        if segment["path"] not in hashes:
            hashes[segment["path"]] = file_hash(Path(segment["path"]))
    _verify_sources(hashes)
    # The directory is claimed atomically. Existing directories never receive writes.
    output.mkdir(parents=False, exist_ok=False)
    work = output / "import-data"
    work.mkdir()
    document_path = output / "timeline.tsrct"
    report_path = output / "import-report.json"
    report.update({"status": "in_progress", "allow_lossy": bool(allow_lossy),
                   "tesseract_version": engine_version, "document": str(document_path),
                   "report_path": str(report_path), "rendered": False,
                   "limitations": [
                       "Experimental one-way conversion; no CapCut export or round trip.",
                       "Native audio source offsets have a known rendering limitation in Tesseract 0.1.0 that is not yet ruled out in 0.2.0; audition any export.",
                       "Millisecond rounding applies to native layer boundaries; original microseconds remain in this report.",
                       "Output frame rate is engine controlled; source timeline fps is recorded, not guaranteed.",
                       "This conversion is not certified visually or audibly faithful to CapCut."]})
    _write(report_path, report)

    def engine(*args):
        return _json_run([executable, *map(str, args)])

    try:
        engine("project", "create", "--project", document_path)
        assets = {}
        for segment in report["segments"]:
            key = (segment["type"], segment["path"])
            if key in assets:
                continue
            asset_id = f"source-{len(assets) + 1:04d}"
            source_path = Path(segment["path"])
            imported_path = source_path
            if key[0] == "audio" and source_path.suffix.lower() not in AUDIO_EXTENSIONS:
                imported_path = work / f"{asset_id}.wav"
                _run([ffmpeg, "-v", "error", "-nostdin", "-n", "-i", str(source_path),
                      "-map", "0:a:0", "-c:a", "pcm_s24le", str(imported_path)])
            if key[0] == "video":
                result = engine("project", "import-video", "--project", document_path,
                                "--file", imported_path, "--asset-id", asset_id)
                duration = result.get("durationMs")
                width, height = result.get("width"), result.get("height")
                if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
                       not math.isfinite(value) or value <= 0 for value in (duration, width, height)):
                    raise ImportError("Tesseract did not return valid source dimensions and duration")
            else:
                result = engine("project", "import-asset", "--project", document_path,
                                "--file", imported_path, "--asset-id", asset_id, "--kind", "audio")
                duration, width, height = audio_durations[str(source_path)], None, None
            assets[key] = {"asset_id": result.get("assetId", asset_id), "duration_ms": duration,
                           "width": width, "height": height}
        editable_path = work / "editable.json"
        engine("project", "checkout", "--project", document_path, "--output", editable_path)
        doc = json.loads(editable_path.read_text(encoding="utf-8-sig"))
        doc["dimensions"] = canvas
        doc["duration"] = report["duration"]
        composition = doc["composition"]
        composition["name"] = report["title"]
        composition_id = composition.get("id", "main")
        layers, actions, mapping = [], [], []
        for index, segment in enumerate(report["segments"], 1):
            asset = assets[(segment["type"], segment["path"])]
            source = _ms_range(segment["source_timerange_us"])
            active = _ms_range(segment["target_timerange_us"])
            source_overrun = source["start"] + source["duration"] - asset["duration_ms"]
            if source_overrun > 50:
                raise ImportError(f"Source range exceeds actual media duration for segment {segment['id']}")
            if source_overrun > 0:
                report["warnings"].append(
                    f"Segment {segment['id']} ends {source_overrun:g} ms after the measured media duration; "
                    "the original range and intrinsic duration were preserved. Check its final frame.")
            layer = {"type": "Video" if segment["type"] == "video" else "Audio", "id": index,
                     "name": segment["name"], "activeRange": active, "sourceRange": source,
                     "sourceIntrinsicDuration": asset["duration_ms"], "source": {"assetId": asset["asset_id"]},
                     "isHidden": segment["hidden"], "volume": segment["volume"],
                     "preserveAudioPitch": segment["preserve_audio_pitch"], "captionsEnabled": False,
                     "description": json.dumps({"source_segment_id": segment["id"],
                                                "track_index": segment["track_index"],
                                                "segment_index": segment["segment_index"]})}
            if segment["type"] == "video":
                transform = segment["transform"]
                width, height = asset["width"], asset["height"]
                cw, ch = canvas["width"], canvas["height"]
                fit = min(cw / width, ch / height)
                layer.update({"blendMode": "normal", "transform": {
                    "anchorPoint": [width / 2, height / 2],
                    "position": [cw / 2 + transform["transform"]["x"] * cw / 2,
                                 ch / 2 - transform["transform"]["y"] * ch / 2],
                    "scale": [100 * fit * transform["scale"]["x"] * (-1 if transform["flip"]["horizontal"] else 1),
                              100 * fit * transform["scale"]["y"] * (-1 if transform["flip"]["vertical"] else 1)],
                    "rotation": -transform["rotation"], "opacity": transform["alpha"] * 100}})
                layer["source"]["fit"] = "contain"
                if segment["separated_audio"]:
                    layer["volume"] = None
            layers.append((segment["track_index"], segment["segment_index"], layer))
            mapping.append({"segment_id": segment["id"], "layer_id": index})
            if abs(segment["speed"] - 1) > 1e-8:
                actions.append({"type": "setFxLayerTimeRemap", "compositionId": composition_id,
                                "layerId": index, "timeRemap": {"before": "inactive", "after": "inactive",
                                "keyframes": [{"id": f"speed-{index}-start", "time": active["start"],
                                               "value": source["start"], "easing": {"type": "linear"}},
                                              {"id": f"speed-{index}-end", "time": active["start"] + active["duration"],
                                               "value": source["start"] + source["duration"], "easing": {"type": "linear"}}]}})
        # CapCut later tracks overlay earlier tracks. Tesseract first layer is on top.
        composition["layers"] = [layer for _, _, layer in sorted(layers, key=lambda item: (-item[0], item[1]))]
        _write(editable_path, doc)
        engine("project", "commit", "--project", document_path, "--file", editable_path)
        if actions:
            actions_path = work / "actions.json"
            _write(actions_path, actions)
            engine("project", "apply", "--project", document_path, "--actions", actions_path)
        final_path = work / "final.json"
        engine("project", "checkout", "--project", document_path, "--output", final_path)
        final = json.loads(final_path.read_text(encoding="utf-8-sig"))
        if not _same_duration(final.get("duration"), report["duration"]):
            # Applying playback actions can trim trailing empty time. Restore the
            # duration from a fresh checkout so the new playback graphs survive.
            final["duration"] = report["duration"]
            restored_path = work / "duration-restored.json"
            _write(restored_path, final)
            engine("project", "commit", "--project", document_path, "--file", restored_path)
            engine("project", "checkout", "--project", document_path, "--output", final_path)
            final = json.loads(final_path.read_text(encoding="utf-8-sig"))
        _verify_readback(final, doc, actions)
        if not document_path.is_file():
            raise ImportError("Tesseract did not create the native project")
        _verify_sources(hashes)
        if render:
            engine("export", "--project", document_path, "--output", output / "preview.mp4")
            if not (output / "preview.mp4").is_file():
                raise ImportError("Tesseract export did not create the requested preview")
            report["rendered"] = True
        _verify_sources(hashes)
        report.update({"status": "imported", "source_hashes_verified": True,
                       "source_hashes": hashes, "layer_mapping": mapping})
        _write(report_path, report)
        return report
    except Exception as exc:
        report.update({"status": "failed", "error": str(exc)})
        try:
            _verify_sources(hashes)
            report["source_hashes_verified"] = True
        except (OSError, ImportError):
            report["source_hashes_verified"] = False
        _write(report_path, report)
        raise ImportError(f"Import failed; retained diagnostic output at {output}: {exc}") from exc
