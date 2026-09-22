"""Read-only inspection of a deliberately limited CapCut project format.

CapCut does not publish a stable project schema. This module never discovers
other projects or falls back to a different timeline when selection fails.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re


class CapCutError(ValueError):
    """The selected project cannot be inspected safely."""


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path) -> tuple[dict, str]:
    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            raise CapCutError(f"Project JSON exceeds 64 MiB: {path}")
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8-sig"),
                           parse_constant=lambda value: (_ for _ in ()).throw(
                               ValueError(f"Non-finite JSON number: {value}")))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CapCutError(f"Cannot read project JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CapCutError(f"Expected a JSON object: {path}")
    return value, hashlib.sha256(raw).hexdigest()


def _number(value, label: str, low: float = 0, high: float = 1e12) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CapCutError(f"{label} must be a number")
    if not low <= value <= high or not math.isfinite(value):
        raise CapCutError(f"{label} is outside supported bounds [{low}, {high}]")
    return value


def _boolean(value, label: str) -> bool:
    if not isinstance(value, bool):
        raise CapCutError(f"{label} must be a boolean")
    return value


def _range(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise CapCutError(f"{label} is missing")
    start = _number(value.get("start"), label + ".start", high=3_600_000_000_000)
    duration = _number(value.get("duration"), label + ".duration", low=1,
                       high=3_600_000_000_000)
    if int(start) != start or int(duration) != duration:
        raise CapCutError(f"{label} must use integer microseconds")
    return {"start": int(start), "duration": int(duration)}


def _safe_id(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise CapCutError("Timeline id must be a safe single directory name")
    return value


def _inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise CapCutError("Timeline metadata points outside the selected project")
    return resolved


def _inactive(value) -> bool:
    """Only empty containers and literal zero/false values are known inert."""
    if isinstance(value, dict):
        return all(_inactive(item) for item in value.values())
    if isinstance(value, list):
        return all(_inactive(item) for item in value)
    return value in (None, False, 0, "")


def inspect_project(project_dir: Path, timeline_name: str) -> dict:
    """Inspect exactly one named nested timeline, without writing anything.

    Missing media and unsupported features are findings. Malformed ranges,
    ambiguous identity and unsafe paths raise CapCutError. Times in ``segments``
    are seconds; exact source microseconds are also retained.
    """
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir() or not isinstance(timeline_name, str) or not timeline_name.strip():
        raise CapCutError("Supply an existing project directory and an exact timeline name")
    source_files = []

    def read(relative):
        path = _inside(root, root / relative)
        data, digest = _read(path)
        source_files.append({"path": str(path), "sha256": digest})
        return data

    root_draft = read("draft_content.json")
    layout = read("timeline_layout.json")
    entries = []
    docks = layout.get("dockItems", [])
    if not isinstance(docks, list):
        raise CapCutError("Timeline layout dockItems must be an array")
    for dock in docks:
        if not isinstance(dock, dict):
            raise CapCutError("Timeline layout dock must be an object")
        names, identities = dock.get("timelineNames", []), dock.get("timelineIds", [])
        if not isinstance(names, list) or not isinstance(identities, list) or len(names) != len(identities):
            raise CapCutError("Timeline layout names and ids have different lengths")
        if not all(isinstance(value, str) for value in names + identities):
            raise CapCutError("Timeline layout names and ids must be strings")
        entries.extend(zip(names, identities))
    # Some single-timeline projects put the name only on the root draft.
    if not entries and isinstance(root_draft.get("name"), str) and root_draft.get("id"):
        entries.append((root_draft["name"], root_draft["id"]))
    matches = {identity for name, identity in entries if name == timeline_name}
    if len(matches) != 1:
        reason = "not found in project metadata" if not matches else "ambiguous in project metadata"
        raise CapCutError(f"Timeline {timeline_name!r} is {reason}; choose an exact unique name")
    identity = _safe_id(matches.pop())
    dock_ids = [value for item in layout.get("dockItems", [])
                if isinstance(item, dict) for value in item.get("timelineIds", [])]
    if dock_ids and identity not in dock_ids:
        raise CapCutError("Selected timeline id is absent from the timeline layout")
    selected = read(Path("Timelines") / identity / "draft_content.json")
    if selected.get("id") != identity:
        raise CapCutError("Selected nested timeline id does not match its directory and metadata")
    unsupported, missing_media, warnings = [], [], []
    if root_draft.get("id") == identity and root_draft != selected:
        warnings.append("The root draft differs from the selected nested timeline; the nested copy was used.")

    def finding(code, detail, segment=None, active=True):
        unsupported.append({"code": code, "detail": detail,
                            "segment_id": segment, "active": bool(active)})

    duration_us = _number(selected.get("duration"), "duration", low=1,
                          high=3_600_000_000_000)
    fps = _number(selected.get("fps", 30), "fps", low=1, high=240)
    canvas = selected.get("canvas_config", {})
    if not isinstance(canvas, dict):
        raise CapCutError("canvas_config must be an object")
    width = _number(canvas.get("width"), "canvas width", low=1, high=16384)
    height = _number(canvas.get("height"), "canvas height", low=1, high=16384)
    if int(width) != width or int(height) != height:
        raise CapCutError("Canvas dimensions must be integers")
    materials = {}
    buckets = selected.get("materials", {})
    if not isinstance(buckets, dict):
        raise CapCutError("materials must be an object")
    for bucket, rows in buckets.items():
        if isinstance(rows, list):
            for material in rows:
                if isinstance(material, dict) and material.get("id"):
                    if material["id"] in materials:
                        raise CapCutError("Duplicate material id in the selected timeline")
                    materials[material["id"]] = (bucket, material)

    tracks = selected.get("tracks", [])
    if not isinstance(tracks, list) or len(tracks) > 1000:
        raise CapCutError("tracks must be a list with at most 1000 entries")
    segments, ids = [], set()
    for track_index, track in enumerate(tracks):
        if not isinstance(track, dict) or not isinstance(track.get("segments", []), list):
            raise CapCutError("Malformed track")
        kind = track.get("type")
        if kind not in ("video", "audio") and track.get("segments"):
            finding("track_type", f"Unsupported track type: {kind}")
            continue
        if track.get("attribute", 0):
            finding("track_attribute", "Track attribute flags are not mapped")
        if track.get("volume") is not None:
            track_volume = _number(track["volume"], "Track volume", high=100)
            if track_volume != 1:
                finding("track_volume", "Non-neutral track volume is not mapped")
        for feature in ("effects", "keyframes", "is_mute", "mute"):
            if track.get(feature):
                finding("track_" + feature, f"Track {feature} is not mapped")
        for segment_index, segment in enumerate(track.get("segments", [])):
            if len(segments) >= 100000:
                raise CapCutError("Selected timeline exceeds 100000 segments")
            if not isinstance(segment, dict):
                raise CapCutError("Malformed segment")
            sid = segment.get("id")
            if not isinstance(sid, str) or not sid or sid in ids:
                raise CapCutError("Every segment must have a unique nonempty id")
            ids.add(sid)
            source = _range(segment.get("source_timerange"), sid + " source")
            target = _range(segment.get("target_timerange"), sid + " target")
            if target["start"] + target["duration"] > duration_us + 1000:
                raise CapCutError(f"Segment {sid} exceeds the timeline duration")
            if not isinstance(segment.get("visible", True), bool) or not isinstance(track.get("visible", True), bool):
                raise CapCutError(f"Visibility must be boolean for segment {sid}")
            hidden = not segment.get("visible", True) or not track.get("visible", True)
            speed = _number(segment.get("speed", 1), sid + " speed", low=.01, high=100)
            volume = _number(segment.get("volume", 1), sid + " volume", high=100)
            if abs(source["duration"] - target["duration"] * speed) > max(2000, target["duration"] * .00001):
                finding("speed_range_mismatch", "Source and target durations do not match scalar speed", sid, not hidden)
            material_id = segment.get("material_id")
            if not isinstance(material_id, str):
                raise CapCutError(f"Missing source material id for segment {sid}")
            bucket, material = materials.get(material_id, (None, {}))
            if bucket not in ("videos", "audios"):
                finding("material_type", f"Unsupported or missing source material: {bucket}", sid, not hidden)
            path_string = material.get("path", "")
            path = None
            if not isinstance(path_string, str) or "\x00" in path_string:
                raise CapCutError(f"Invalid media path for segment {sid}")
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path_string) or path_string.startswith(("\\\\", "//")):
                raise CapCutError(f"Network media is unsupported for segment {sid}; relink a local file")
            if path_string:
                path = Path(path_string).expanduser()
                if not path.is_absolute():
                    path = root / path
                path = path.resolve()
            if path is None or not path.is_file():
                missing_media.append({"segment_id": sid, "path": str(path) if path else path_string,
                                      "detail": "Relink this material to an existing local file in CapCut and save."})
            clip = segment.get("clip") or {}
            if not isinstance(clip, dict):
                raise CapCutError(f"Invalid clip transform for segment {sid}")
            transform = {}
            for group, default in (("scale", 1), ("transform", 0)):
                values = clip.get(group) or {}
                if not isinstance(values, dict):
                    raise CapCutError(f"Invalid {group} transform for segment {sid}")
                transform[group] = {axis: _number(values.get(axis, default), sid + " " + group,
                                                  low=-10000, high=10000) for axis in ("x", "y")}
            transform["rotation"] = _number(clip.get("rotation", 0), sid + " rotation", -36000, 36000)
            transform["alpha"] = _number(clip.get("alpha", 1), sid + " alpha", 0, 1)
            flip = clip.get("flip") or {}
            if not isinstance(flip, dict):
                raise CapCutError(f"Invalid flip transform for segment {sid}")
            transform["flip"] = {axis: _boolean(flip.get(axis, False), sid + " flip " + axis)
                                 for axis in ("horizontal", "vertical")}
            separated_audio = _boolean(material.get("has_sound_separated", False), sid + " has_sound_separated")
            preserve_audio_pitch = _boolean(segment.get("is_tone_modify", False), sid + " is_tone_modify")
            for field in ("common_keyframes", "keyframe_refs", "volume_keyframes", "effects", "mask", "transition",
                          "reverse", "enable_color_match_adjust", "enable_adjust", "enable_lut",
                          "enable_video_mask", "enable_video_denoise", "enable_audio_denoise",
                          "enable_smart_color_adjust", "enable_color_curves", "enable_color_wheels",
                          "audio_fade", "audio_fades", "fade_in_duration", "fade_out_duration"):
                if segment.get(field):
                    finding(field, f"{field} is not converted", sid, not hidden)
            known_enable_flags = {item["code"] for item in unsupported if item["segment_id"] == sid}
            for field, value in segment.items():
                if field.startswith("enable_") and value and field not in known_enable_flags:
                    finding(field, f"Active {field} is not converted", sid, not hidden)
            for field, value in clip.items():
                if field not in ("scale", "transform", "rotation", "alpha", "flip") and value:
                    finding("clip_" + field, f"Clip {field} is not converted", sid, not hidden)
            if segment.get("blend_mode", "normal") not in ("normal", 0, None):
                finding("blend_mode", "Non-normal blending is not converted", sid, not hidden)
            if bucket == "videos" and material.get("type", "video") not in ("video", "raw_video"):
                finding("video_material_type", "This video material subtype is not supported", sid, not hidden)
            if separated_audio:
                finding("separated_audio", "Separated embedded audio semantics are unverified; lossy import mutes the video layer", sid, not hidden)
            if preserve_audio_pitch:
                finding("audio_pitch", "Pitch flag semantics are unverified; lossy import copies the flag without a fidelity guarantee", sid, not hidden)
            if material.get("crop") and material["crop"] != {
                    "upper_left_x": 0, "upper_left_y": 0, "upper_right_x": 1, "upper_right_y": 0,
                    "lower_left_x": 0, "lower_left_y": 1, "lower_right_x": 1, "lower_right_y": 1}:
                finding("crop", "Material crop is not converted", sid, not hidden)
            refs = segment.get("extra_material_refs", [])
            if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
                raise CapCutError(f"Invalid extra material references for segment {sid}")
            for ref in refs:
                ref_bucket, ref_material = materials.get(ref, (None, {}))
                neutral = False
                if ref_bucket == "speeds":
                    ref_speed = _number(ref_material.get("speed", speed), sid + " referenced speed", .01, 100)
                    neutral = not ref_material.get("curve_speed") and abs(ref_speed - speed) < 1e-8
                elif ref_bucket == "sound_channel_mappings":
                    neutral = ref_material.get("audio_channel_mapping", 0) == 0 and not ref_material.get("is_config_open", False)
                elif ref_bucket == "vocal_separations":
                    neutral = ref_material.get("choice", 0) == 0
                elif ref_bucket == "placeholder_infos":
                    neutral = not any(ref_material.get(key) for key in ("res_path", "res_text", "meta_type"))
                elif ref_bucket == "canvases":
                    neutral = (ref_material.get("type", "canvas_color") == "canvas_color"
                               and ref_material.get("color", "") in ("", "#000000", "#00000000")
                               and _inactive({key: value for key, value in ref_material.items()
                                              if key not in ("id", "type", "color")}))
                elif ref_bucket == "material_colors":
                    neutral = _inactive({key: value for key, value in ref_material.items()
                                         if key not in ("id", "type")})
                elif ref_bucket == "material_animations":
                    neutral = not ref_material.get("animations")
                elif ref_bucket == "audio_fades":
                    neutral = not ref_material.get("fade_in_duration", 0) and not ref_material.get("fade_out_duration", 0)
                if not neutral:
                    finding("extra_material", f"Referenced {ref_bucket or 'missing material'} is not converted", sid, not hidden)
            segments.append({"id": sid, "type": kind, "track_index": track_index,
                             "segment_index": segment_index, "material_id": material_id,
                             "path": str(path) if path else "", "name": material.get("material_name") or (path.name if path else sid),
                             "source_start": source["start"] / 1e6, "source_duration": source["duration"] / 1e6,
                             "target_start": target["start"] / 1e6, "target_duration": target["duration"] / 1e6,
                             "source_timerange_us": source, "target_timerange_us": target,
                             "hidden": hidden, "volume": volume, "speed": speed, "transform": transform,
                             "separated_audio": separated_audio,
                             "preserve_audio_pitch": preserve_audio_pitch,
                             "material_duration_us": material.get("duration")})
    if selected.get("keyframes") and (not isinstance(selected["keyframes"], dict) or any(selected["keyframes"].values())):
        finding("project_keyframes", "Project keyframes are not converted")
    config = selected.get("config") or {}
    if not isinstance(config, dict):
        raise CapCutError("Project config must be an object")
    if config.get("video_mute", False):
        for segment in segments:
            if segment["type"] == "video":
                segment["volume"] = 0
    combined = hashlib.sha256(json.dumps(source_files, sort_keys=True).encode()).hexdigest()
    return {"title": timeline_name, "duration": duration_us / 1e6, "fps": fps,
            "canvas": {"width": int(width), "height": int(height)}, "segments": segments,
            "unsupported": unsupported, "missing_media": missing_media, "warnings": warnings,
            "selected_timeline_id": identity, "snapshot_hash": combined,
            "source_files": source_files, "experimental": True}
