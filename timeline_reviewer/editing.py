"""Bounded, local Tesseract edits for an explicitly bound review bundle.

The draft is a private sidecar, never a served bundle asset. Native commits are
made from a copy into a new version directory; the selected project is not
modified. This module never renders or serves source media.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading
from uuid import uuid4

from .capcut import file_hash
from .manifest import load_manifest, validate_manifest
from .tesseract import _run, resolve_cli


_LOCK = threading.RLock()
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
_DRAFT_NAME = ".madison-edit-draft.json"


class EditError(ValueError):
    """A draft cannot be applied safely to its selected native project."""


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _number(value: object, field: str, minimum: float = 0,
            maximum: float = 86400) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EditError(f"{field} must be a finite number")
    if not minimum <= value <= maximum:
        raise EditError(f"{field} is outside the supported range")
    return float(value)


def _ms(value: float) -> int:
    return round(value * 1000)


def _close_ms(a: float, b: float, tolerance: float = 2.1) -> bool:
    return abs(a - b) <= tolerance


def _write_json_atomic(path: Path, value: dict) -> None:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                     sort_keys=True, separators=(",", ":")).encode("utf-8")
    temporary = path.with_name(path.name + ".pending-" + uuid4().hex)
    with temporary.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _subset_equal(expected: object, actual: object) -> bool:
    """Tesseract may add neutral defaults to a checkout after commit."""
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and
                all(key in actual and _subset_equal(value, actual[key])
                    for key, value in expected.items()))
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(expected) == len(actual) and
                all(_subset_equal(a, b) for a, b in zip(expected, actual)))
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and math.isfinite(actual) and abs(expected - actual) <= 1e-6)
    return expected == actual


class EditSession:
    """Apply a replacement list of simple edits to a native Tesseract project.

    ``expected_revision`` is the ``token`` returned by ``state``. This token
    changes whenever the draft changes, so two browser tabs cannot silently
    overwrite each other's pending edits. Source revisions are checked against
    file metadata during polling and freshly hashed before native commit.
    """

    def __init__(self, bundle: Path, project: Path, cli: str | None = None,
                 media_roots: list[Path] | tuple[Path, ...] | None = None):
        self.bundle = Path(bundle).expanduser().resolve()
        self.origin_project = Path(project).expanduser().resolve()
        if not self.bundle.is_dir() or not (self.bundle / "data.json").is_file():
            raise EditError("Select an existing Madison review bundle")
        if self.origin_project.suffix.lower() != ".tsrct" or not self.origin_project.is_file():
            raise EditError("Select an existing Tesseract .tsrct project")
        self.draft_path = self.bundle / _DRAFT_NAME
        if self.draft_path.is_symlink():
            raise EditError("The private draft path cannot be a symlink")
        self._manifest_path = self.bundle / "data.json"
        self._hash_cache: dict[Path, tuple[int, int, str]] = {}
        self._manifest_hash = self._file_hash(self._manifest_path)
        self._origin_hash = self._file_hash(self.origin_project)
        self.cli = resolve_cli(cli)
        roots = [self.bundle, self.origin_project.parent] if media_roots is None else list(media_roots)
        self.media_roots = tuple(Path(root).expanduser().resolve() for root in roots)
        self._native_path: Path | None = None
        self._native_sha: str | None = None
        self._native: dict | None = None
        self._current_record = self._record()
        self._refresh_native(self._current_record)

    def _file_hash(self, path: Path, *, force: bool = False) -> str:
        """Reuse a digest only while the same file keeps size and mtime.

        Normal viewer polling avoids rereading a large native archive. A
        commit bypasses this cache and verifies the file again before and
        during native work, including same-size, same-mtime replacements.
        """
        target = path.resolve()
        before = target.stat()
        cached = self._hash_cache.get(target)
        if (not force and cached is not None and
                cached[:2] == (before.st_size, before.st_mtime_ns)):
            return cached[2]
        digest = file_hash(target)
        after = target.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise EditError("A source file changed while its revision was being checked")
        self._hash_cache[target] = (after.st_size, after.st_mtime_ns, digest)
        return digest

    def _record(self) -> dict:
        if self.draft_path.is_symlink():
            raise EditError("The private draft path cannot be a symlink")
        if not self.draft_path.exists():
            manifest = self._validate_layer_binding(load_manifest(self._manifest_path))
            return {"schemaVersion": 1, "originSha256": self._origin_hash,
                    "bundleSha256": self._manifest_hash,
                    "activeProject": str(self.origin_project),
                    "activeSha256": self._origin_hash,
                    "baseManifest": manifest,
                    "operations": [], "renderPending": False}
        if self.draft_path.stat().st_size > 10 * 1024 * 1024:
            raise EditError("The private draft exceeds its size limit")
        try:
            value = json.loads(self.draft_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise EditError("The private edit draft is unreadable") from exc
        if (not isinstance(value, dict) or value.get("schemaVersion") != 1 or
                value.get("originSha256") != self._origin_hash or
                value.get("bundleSha256") != self._manifest_hash or
                not isinstance(value.get("operations"), list)):
            raise EditError("The review bundle or original project changed since this draft was created")
        if not isinstance(value.get("activeProject"), str) or not value["activeProject"]:
            raise EditError("The private draft references an invalid native project")
        active = Path(value["activeProject"]).expanduser().resolve()
        if (active.suffix.lower() != ".tsrct" or not active.is_file() or
                not active.is_relative_to(self.origin_project.parent)):
            raise EditError("The private draft references an invalid native project")
        value["baseManifest"] = self._validate_layer_binding(
            validate_manifest(value.get("baseManifest")))
        return value

    @staticmethod
    def _validate_layer_binding(manifest: dict) -> dict:
        seen = set()
        for track in manifest["tracks"]:
            for clip in track["clips"]:
                layer_id = clip.get("layerId")
                if layer_id is None:
                    continue
                if isinstance(layer_id, bool) or not isinstance(layer_id, int):
                    raise EditError("Native layer IDs in a review bundle must be integers")
                if layer_id in seen:
                    raise EditError("Multiple review clips reference the same native layer ID")
                seen.add(layer_id)
        return manifest

    def _checkout(self, project: Path) -> dict:
        with tempfile.TemporaryDirectory(prefix="madison-checkout-") as folder:
            target = Path(folder) / "editable.json"
            _run([self.cli, "project", "checkout", "--project", str(project),
                  "--output", str(target)])
            if not target.is_file() or target.stat().st_size > 64 * 1024 * 1024:
                raise EditError("Tesseract checkout is missing or too large")
            try:
                document = json.loads(target.read_text(encoding="utf-8-sig"))
            except (UnicodeError, ValueError) as exc:
                raise EditError("Tesseract checkout is not valid JSON") from exc
        if (not isinstance(document, dict) or
                not isinstance(document.get("composition"), dict) or
                not isinstance(document["composition"].get("layers"), list)):
            raise EditError("Tesseract checkout has no editable composition")
        return document

    def _refresh_native(self, record: dict, *, force_hash: bool = False) -> tuple[Path, str, dict]:
        project = Path(record["activeProject"]).resolve()
        current_hash = self._file_hash(project, force=force_hash)
        if current_hash != record.get("activeSha256"):
            raise EditError("The native project changed outside Madison; reopen a fresh review")
        if self._file_hash(self._manifest_path, force=force_hash) != self._manifest_hash:
            raise EditError("The review bundle changed outside Madison; reopen a fresh review")
        if self._native_path != project or self._native_sha != current_hash:
            self._native = self._checkout(project)
            self._native_path = project
            self._native_sha = current_hash
        return project, current_hash, self._native

    def _current(self, *, force_hash: bool = False) -> tuple[dict, Path, dict, str, str]:
        record = self._record()
        project, project_hash, native = self._refresh_native(record, force_hash=force_hash)
        source_revision = _digest([project_hash, self._manifest_hash, record["baseManifest"]])
        token = _digest([source_revision, record["operations"]])
        return record, project, native, source_revision, token

    @staticmethod
    def _layers(native: dict) -> dict[int, dict]:
        output = {}
        for layer in native["composition"]["layers"]:
            if not isinstance(layer, dict):
                raise EditError("Native project contains an invalid root layer")
            layer_id = layer.get("id")
            if isinstance(layer_id, bool) or not isinstance(layer_id, int) or layer_id in output:
                raise EditError("Native project has missing or duplicate layer IDs")
            output[layer_id] = layer
        return output

    @staticmethod
    def _dynamics(native: dict, layer_id: int) -> bool:
        dynamics = native["composition"].get("dynamics") or {}
        if not isinstance(dynamics, dict) or set(dynamics) - {"entries"}:
            return True
        entries = dynamics.get("entries", []) if isinstance(dynamics, dict) else []
        if not isinstance(entries, list):
            return True
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("target"), dict):
                return True
            target = entry["target"]
            target_id = target.get("layerId")
            property_type = target.get("propertyType")
            if (set(target) - {"kind", "layerId", "propertyType"} or
                    target.get("kind") != "layer" or isinstance(target_id, bool) or
                    not isinstance(target_id, int) or
                    not isinstance(property_type, str) or not property_type):
                return True
            if target_id == layer_id:
                return True
        return False

    def _target(self, manifest: dict, native: dict, clip_id: str, layer_id: int) -> tuple[dict, dict]:
        if not isinstance(clip_id, str) or not clip_id or len(clip_id) > 128:
            raise EditError("clipId is invalid")
        if isinstance(layer_id, bool) or not isinstance(layer_id, int) or layer_id < 0:
            raise EditError("layerId is invalid")
        matches = [(clip, track["kind"]) for track in manifest["tracks"] for clip in track["clips"]
                   if clip["id"] == clip_id and clip.get("layerId") == layer_id]
        if len(matches) != 1:
            raise EditError("The clip ID and native layer ID do not match")
        layer = self._layers(native).get(layer_id)
        if not layer or layer.get("type") not in ("Video", "Audio"):
            raise EditError("The selected native media layer is unavailable")
        try:
            description = json.loads(layer.get("description") or "{}")
        except ValueError as exc:
            raise EditError("The selected layer has invalid identity metadata") from exc
        if not isinstance(description, dict):
            raise EditError("The selected layer has invalid identity metadata")
        identity = description.get("native_clip_id", description.get("capcut_segment_id",
                        description.get("source_segment_id")))
        if identity is not None and identity != clip_id:
            raise EditError("The native layer identity disagrees with the review clip")
        clip, kind = matches[0]
        if layer["type"].lower() != kind:
            raise EditError("The review lane disagrees with the native media type")
        active, source = layer.get("activeRange"), layer.get("sourceRange")
        if not isinstance(active, dict) or not isinstance(source, dict):
            raise EditError("The selected layer has no supported timing ranges")
        comparisons = ((active.get("start"), clip["start"]),
                       (active.get("start", 0) + active.get("duration", 0), clip["end"]),
                       (source.get("start"), clip["sourceStart"]),
                       (source.get("start", 0) + source.get("duration", 0), clip["sourceEnd"]))
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
               not _close_ms(value, seconds * 1000) for value, seconds in comparisons):
            raise EditError("The review clip timing disagrees with its native layer")
        if layer.get("isHidden") is not clip["hidden"]:
            raise EditError("The review clip visibility disagrees with its native layer")
        actual_volume, expected_volume = layer.get("volume"), clip["volume"]
        if ((actual_volume is None) != (expected_volume is None) or
                (actual_volume is not None and
                 (isinstance(actual_volume, bool) or not isinstance(actual_volume, (int, float))
                  or not math.isfinite(actual_volume) or abs(actual_volume - expected_volume) > 1e-5))):
            raise EditError("The review clip volume disagrees with its native layer")
        return clip, layer

    def _apply(self, operations: list, base_manifest: dict, native: dict) -> tuple[dict, dict]:
        if not isinstance(operations, list) or len(operations) > 1000:
            raise EditError("Too many draft operations")
        manifest = deepcopy(base_manifest)
        edited = deepcopy(native)
        edited_layers = self._layers(edited)
        seen = set()
        for op in operations:
            if not isinstance(op, dict) or op.get("type") not in ("remove", "trim", "volume"):
                raise EditError("Only remove, trim and volume operations are supported")
            kind = op["type"]
            fields = {"remove": {"type", "clipId", "layerId"},
                      "trim": {"type", "clipId", "layerId", "start", "end", "sourceStart", "sourceEnd"},
                      "volume": {"type", "clipId", "layerId", "volume"}}[kind]
            if set(op) != fields:
                raise EditError(f"{kind} has missing or unexpected fields")
            clip_id, layer_id = op["clipId"], op["layerId"]
            key = (clip_id, kind)
            if key in seen:
                raise EditError("Duplicate operation for one clip and edit type")
            seen.add(key)
            original_clip, original_layer = self._target(base_manifest, native, clip_id, layer_id)
            clip = next(clip for track in manifest["tracks"] for clip in track["clips"] if clip["id"] == clip_id)
            layer = edited_layers[layer_id]
            if kind == "remove":
                layer["isHidden"] = True
                clip["hidden"] = True
                continue
            if self._dynamics(native, layer_id):
                raise EditError("This clip has native animation; trim or volume needs an agent review")
            if any(key in original_layer for key in
                   ("effects", "effectStack", "masks", "timeRemap", "animators", "fx")):
                raise EditError("This clip has native effects; trim or volume needs an agent review")
            if kind == "volume":
                requested = op["volume"]
                volume = None if requested is None else _number(requested, "volume", maximum=2)
                if original_layer.get("volume") is None and volume not in (None, 0):
                    raise EditError("Enabling previously disabled source audio needs an agent review")
                layer["volume"] = volume
                clip["volume"] = volume
                continue
            if original_layer["type"] == "Audio" and original_layer.get("windowMs"):
                raise EditError("Trimming windowed audio needs an agent review")
            if original_layer.get("playback") or not _close_ms(
                    original_layer["activeRange"]["duration"], original_layer["sourceRange"]["duration"]):
                raise EditError("Trimming a retimed clip needs an agent review")
            if abs(original_clip["speed"] - 1) > .001:
                raise EditError("Trimming a retimed clip needs an agent review")
            start = _number(op["start"], "start", maximum=base_manifest["duration"])
            end = _number(op["end"], "end", maximum=base_manifest["duration"] + .002)
            src_start = _number(op["sourceStart"], "sourceStart")
            src_end = _number(op["sourceEnd"], "sourceEnd")
            start_ms, end_ms, source_start_ms, source_end_ms = map(
                _ms, (start, end, src_start, src_end))
            active = original_layer["activeRange"]
            source = original_layer["sourceRange"]
            original_end = active["start"] + active["duration"]
            original_source_end = source["start"] + source["duration"]
            if (start_ms < active["start"] or end_ms > original_end or end_ms <= start_ms or
                    source_start_ms < source["start"] or source_end_ms > original_source_end or
                    source_end_ms <= source_start_ms or
                    not _close_ms(source_start_ms - source["start"], start_ms - active["start"]) or
                    not _close_ms(original_source_end - source_end_ms, original_end - end_ms) or
                    not _close_ms(source_end_ms - source_start_ms, end_ms - start_ms)):
                raise EditError("Trim must shorten a speed 1 clip without slipping its source")
            layer["activeRange"] = {"start": start_ms, "duration": end_ms - start_ms}
            layer["sourceRange"] = {"start": source_start_ms,
                                    "duration": source_end_ms - source_start_ms}
            clip.update(start=start_ms / 1000, end=end_ms / 1000,
                        duration=(end_ms - start_ms) / 1000,
                        sourceStart=source_start_ms / 1000,
                        sourceEnd=source_end_ms / 1000)
        # Validate the same public data shape the UI receives.
        return validate_manifest(manifest), edited

    def state(self) -> dict:
        with _LOCK:
            record, project, native, source_revision, token = self._current()
            effective, _ = self._apply(record["operations"], record["baseManifest"], native)
            return {"sourceRevision": source_revision, "token": token,
                    "operations": deepcopy(record["operations"]), "manifest": effective,
                    "renderPending": bool(record.get("renderPending")),
                    "savedProject": str(project) if project != self.origin_project else None}

    def save_draft(self, operations: list, expected_revision: str) -> dict:
        with _LOCK:
            record, _, native, _, token = self._current()
            if expected_revision != token:
                raise EditError("The draft changed in another tab; refresh before saving")
            self._apply(operations, record["baseManifest"], native)
            record["operations"] = deepcopy(operations)
            _write_json_atomic(self.draft_path, record)
            return self.state()

    def commit(self, expected_revision: str) -> dict:
        with _LOCK:
            record, project, native, _, token = self._current(force_hash=True)
            if expected_revision != token:
                raise EditError("The draft changed in another tab; refresh before applying")
            if not record["operations"]:
                raise EditError("There are no draft edits to apply")
            effective, desired = self._apply(record["operations"], record["baseManifest"], native)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            suffix = uuid4().hex[:10]
            basename = f"{self.origin_project.stem}-madison-{stamp}-{suffix}"
            staging = self.origin_project.parent / ("." + basename + ".pending")
            destination = self.origin_project.parent / basename
            staging.mkdir(exist_ok=False)
            staged_project = staging / self.origin_project.name
            try:
                shutil.copyfile(project, staged_project)
                if (self._file_hash(project, force=True) != record["activeSha256"] or
                        self._file_hash(self._manifest_path, force=True) != self._manifest_hash or
                        self._file_hash(staged_project, force=True) != record["activeSha256"]):
                    raise EditError("The native project changed during copying")
                with tempfile.TemporaryDirectory(prefix="madison-commit-") as folder:
                    authored = Path(folder) / "editable.json"
                    _write_json_atomic(authored, desired)
                    _run([self.cli, "project", "commit", "--project", str(staged_project),
                          "--file", str(authored)])
                    readback = self._checkout(staged_project)
                expected_layers = desired["composition"]["layers"]
                actual_layers = readback["composition"]["layers"]
                if len(expected_layers) != len(actual_layers) or not _subset_equal(desired, readback):
                    raise EditError("Native readback differs from the requested edit")
                if (self._file_hash(project, force=True) != record["activeSha256"] or
                        self._file_hash(self._manifest_path, force=True) != self._manifest_hash):
                    raise EditError("The original native project changed during commit")
                if destination.exists():
                    raise EditError("Versioned output already exists")
                staging.rename(destination)
            except Exception as exc:
                raise EditError(f"Native edit failed; diagnostic copy retained at {staging}: {exc}") from exc
            saved = destination / self.origin_project.name
            record.update(activeProject=str(saved), activeSha256=self._file_hash(saved, force=True),
                          baseManifest=effective, operations=[], renderPending=True)
            record["baseManifest"]["revision"] = record["activeSha256"][:12]
            _write_json_atomic(self.draft_path, record)
            self._native_path = saved
            self._native_sha = record["activeSha256"]
            self._native = readback
            result = self.state()
            result.update(savedProject=str(saved), revision=result["sourceRevision"],
                          renderPending=True)
            return result

    def source_files(self) -> dict[str, dict]:
        """Return only approved on-disk video sources for the server to gate.

        Paths are deliberately absent from ``state``. The caller may issue
        opaque same-origin media URLs for this mapping, never an arbitrary
        browser-provided path.
        """
        with _LOCK:
            record, _, native, _, _ = self._current()
            layers = self._layers(native)
            dimensions = native.get("dimensions")
            canvas = None
            if isinstance(dimensions, dict):
                width, height = dimensions.get("width"), dimensions.get("height")
                if (all(isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) and 0 < value <= 16384
                        for value in (width, height))):
                    canvas = {"width": width, "height": height}
            result = {}
            for track in record["baseManifest"]["tracks"]:
                if track["kind"] != "video":
                    continue
                for clip in track["clips"]:
                    layer = layers.get(clip.get("layerId"))
                    if not layer or layer.get("type") != "Video" or layer.get("playback"):
                        continue
                    try:
                        self._target(record["baseManifest"], native, clip["id"], clip["layerId"])
                        description = json.loads(layer.get("description") or "{}")
                        value = description.get("source_path")
                        if not isinstance(value, str):
                            continue
                        raw = Path(value).expanduser()
                        if not raw.is_absolute() or raw.suffix.lower() not in _VIDEO_SUFFIXES:
                            continue
                        path = raw.resolve()
                        if (not path.is_file() or not any(path.is_relative_to(root) for root in self.media_roots)
                                or (clip.get("sourceFilename") and
                                    path.name.casefold() != clip["sourceFilename"].casefold())):
                            continue
                        transform = layer.get("transform")
                        safe_transform = None
                        if isinstance(transform, dict):
                            allowed = ("position", "scale", "rotation", "opacity", "anchorPoint")
                            candidate = {key: deepcopy(transform[key]) for key in allowed if key in transform}
                            numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                          and math.isfinite(v) for item in candidate.values()
                                          for v in (item if isinstance(item, list) else [item]))
                            if numeric:
                                safe_transform = candidate
                        result[clip["id"]] = {"path": path, "layerId": clip["layerId"],
                                               "sourceStart": clip["sourceStart"],
                                               "sourceEnd": clip["sourceEnd"],
                                               "start": clip["start"], "end": clip["end"],
                                               "transform": safe_transform,
                                               "canvas": canvas}
                    except (EditError, OSError, ValueError):
                        continue
            return result
