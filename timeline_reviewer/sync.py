"""Opt in, local CapCut sync with a reviewable preflight and atomic viewer switch.

The named CapCut project is only read. Every import gets a fresh destination;
failed imports and older review media are deliberately retained.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
from uuid import uuid4

from .capcut import file_hash, inspect_project
from .manifest import asset_files, load_manifest
from .prepare import prepare_bundle, probe
from .tesseract import import_project


def _snapshot(report: dict) -> dict:
    fields = ("id", "name", "type", "track_index", "path", "source_timerange_us",
              "target_timerange_us", "hidden", "volume", "speed", "transform")
    return {"sourceHash": report["snapshot_hash"], "duration": report["duration"],
            "segments": [{key: segment[key] for key in fields} for segment in report["segments"]]}


def _short_name(segment: dict) -> str:
    return Path(str(segment.get("name") or segment["id"])).name[:80]


def _safe_identity(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else None


def _audio_offset_segments(report: dict) -> list[dict]:
    # Tesseract 0.1.0 imports the layer range, but its native renderer has a
    # known source-offset limitation for audio clips. The issue is material to
    # a music edit even when CapCut has no unsupported effect flags.
    return [segment for segment in report["segments"] if segment["type"] == "audio"
            and not segment["hidden"] and segment["source_start"] > .001]


def _diff(previous: dict | None, current: dict) -> dict:
    before = {item["id"]: item for item in (previous or {}).get("segments", [])}
    after = {item["id"]: item for item in current["segments"]}
    changes = {"added": 0, "removed": 0, "moved": 0, "nudged": 0,
               "laneChanged": 0, "trimmed": 0, "changed": 0,
               "previousDuration": (previous or {}).get("duration"),
               "currentDuration": current["duration"], "examples": []}
    def example(message):
        if len(changes["examples"]) < 8:
            changes["examples"].append(message)
    for identity, item in after.items():
        old = before.get(identity)
        name = _short_name(item)
        if old is None:
            changes["added"] += 1
            example(f"Added {name} at {item['target_timerange_us']['start'] / 1e6:.3f}s")
            continue
        start_shift = abs(old["target_timerange_us"]["start"] - item["target_timerange_us"]["start"])
        if start_shift > 50_000:
            changes["moved"] += 1
            example(f"Moved {name} to {item['target_timerange_us']['start'] / 1e6:.3f}s")
        elif start_shift > 1_000:
            changes["nudged"] += 1
            example(f"Adjusted timing for {name} by {start_shift / 1e3:.0f}ms")
        if old["track_index"] != item["track_index"]:
            changes["laneChanged"] += 1
            example(f"Changed lane for {name}")
        if (any(abs(old["source_timerange_us"][key] - item["source_timerange_us"][key]) > 1_000
                for key in ("start", "duration"))
                or abs(old["target_timerange_us"]["duration"]
                       - item["target_timerange_us"]["duration"]) > 1_000):
            changes["trimmed"] += 1
            example(f"Trimmed {name}")
        if any(old[key] != item[key] for key in
               ("name", "type", "path", "hidden", "volume", "speed", "transform")):
            changes["changed"] += 1
            example(f"Changed settings for {name}")
    for identity, item in before.items():
        if identity not in after:
            changes["removed"] += 1
            example(f"Removed {_short_name(item)}")
    return changes


def _timeline(report: dict, rendered_duration: float, rendered_fps: float,
              mapping: list[dict]) -> dict:
    tolerance = 1 / report["fps"] + .01
    if abs(report["duration"] - rendered_duration) > tolerance:
        raise ValueError("The rendered movie duration differs from the selected timeline. The old review remains active.")
    layers = {item["segment_id"]: item["layer_id"] for item in mapping}
    active_findings = {item["segment_id"] for item in report["unsupported"] if item["active"]}
    tracks = {}
    for segment in report["segments"]:
        index = segment["track_index"]
        kind = segment["type"]
        if kind not in ("video", "audio"):
            continue
        if index not in tracks:
            tracks[index] = {"id": f"capcut-track-{index}",
                             "name": f"{'Picture' if kind == 'video' else 'Audio'} {index + 1}",
                             "kind": kind, "clips": []}
        start = segment["target_start"]
        end = min(start + segment["target_duration"], rendered_duration)
        if end <= start:
            raise ValueError("A clip falls outside the rendered movie. The old review remains active.")
        source_start = segment["source_start"]
        source_end = source_start + segment["source_duration"] * (end - start) / segment["target_duration"]
        native_id = segment["id"]
        stable_id = native_id if (len(native_id) <= 128 and
                                  re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", native_id)) else (
            "capcut-" + hashlib.sha256(native_id.encode("utf-8")).hexdigest()[:24])
        tracks[index]["clips"].append({"id": stable_id,
            "label": str(segment["name"])[:512], "sourceFilename": Path(segment["path"]).name,
            "start": start, "end": end, "duration": end - start,
            "sourceStart": source_start, "sourceEnd": source_end,
            "speed": segment["speed"], "hidden": segment["hidden"],
            "volume": None if segment["separated_audio"] and kind == "video" else segment["volume"],
            "colorPending": segment["id"] in active_findings,
            "layerId": layers[segment["id"]]})
    if not tracks:
        raise ValueError("No supported picture or audio lanes were found.")
    return {"schemaVersion": 1, "title": report["title"], "duration": rendered_duration,
            "fps": rendered_fps, "tracks": [tracks[index] for index in sorted(tracks)]}


class CapCutSync:
    """One local source binding and at most one background sync job."""

    def __init__(self, bundle: Path, project: Path, timeline: str, root: Path,
                 cli: str | None = None):
        self.bundle = Path(bundle).expanduser().resolve()
        self.project = Path(project).expanduser().resolve()
        self.timeline = timeline
        self.root = Path(root).expanduser().resolve()
        if not self.project.is_dir() or not self.bundle.is_dir():
            raise ValueError("Select an existing CapCut project and review bundle.")
        if self.root.is_relative_to(self.project) or self.root.is_relative_to(self.bundle):
            raise ValueError("The sync output must be outside the source project and review bundle.")
        self.root.mkdir(parents=True, exist_ok=True)
        self.versions = self.root / "versions"
        self.versions.mkdir(exist_ok=True)
        self.cli = cli
        self.lock = threading.RLock()
        self.job = {"enabled": True, "state": "idle", "stage": None, "error": None,
                    "revision": None, "renderPending": False, "editable": False, "document": None}
        self.snapshot_path = self.root / "last-success.json"
        active_revision = load_manifest(self.bundle / "data.json")["revision"]
        self.previous = None
        recovery = (self.versions / active_revision / "success-snapshot.json"
                    if re.fullmatch(r"[0-9a-f]{32}", active_revision) else None)
        for candidate in (self.snapshot_path, recovery):
            if candidate is None:
                continue
            if candidate.is_file():
                record = json.loads(candidate.read_text(encoding="utf-8"))
                if not isinstance(record, dict):
                    raise ValueError("The last successful sync record is invalid.")
                if record.get("revision") == active_revision:
                    self.previous = record
                    break
        inspect_project(self.project, self.timeline)  # Validate the explicit binding now.

    def state(self, current_revision: str) -> dict:
        report = inspect_project(self.project, self.timeline)
        current = _snapshot(report)
        diff = _diff(self.previous, current)
        active = [item for item in report["unsupported"] if item["active"]]
        segment_names = {segment["id"]: _short_name(segment) for segment in report["segments"]}
        grouped = {}
        for item in active:
            code = item["code"] if re.fullmatch(r"[A-Za-z0-9_]{1,100}", item["code"]) else "unmapped_setting"
            detail = item["detail"]
            if "/" in detail or "\\" in detail:
                detail = "This CapCut setting is not converted."
            group = grouped.setdefault(code, {"code": code,
                "detail": detail, "count": 0, "examples": []})
            group["count"] += 1
            if item["segment_id"] and len(group["examples"]) < 3:
                group["examples"].append(segment_names.get(item["segment_id"], "Selected clip"))
        offsets = _audio_offset_segments(report)
        if offsets:
            grouped["audio_source_offset"] = {
                "code": "audio_source_offset",
                "detail": "Tesseract 0.1.0 may render trimmed audio from the wrong source position. Check the export against CapCut.",
                "count": len(offsets), "examples": [_short_name(item) for item in offsets[:3]]}
        missing = report["missing_media"]
        unchanged = self.previous is not None and self.previous.get("sourceHash") == current["sourceHash"]
        with self.lock:
            busy = self.job["state"] == "running"
        return {"enabled": True, "projectName": self.project.name, "timelineName": self.timeline,
                "sourceHash": current["sourceHash"], "currentRevision": current_revision,
                "diff": diff,
                "unsupported": list(grouped.values()),
                "missingMedia": [{"segmentId": _safe_identity(item["segment_id"]),
                                  "name": Path(item["path"]).name if item["path"] else "Unknown media",
                                  "detail": item["detail"]} for item in missing],
                "requiresLossy": bool(active or offsets),
                "canSync": bool(report["segments"]) and not missing and not unchanged and not busy,
                "noChanges": unchanged}

    def status(self) -> dict:
        with self.lock:
            return dict(self.job)

    def start(self, expected_hash: str, allow_lossy: bool, activate_callback=None) -> dict:
        if not isinstance(expected_hash, str) or len(expected_hash) != 64 or any(c not in "0123456789abcdef" for c in expected_hash):
            raise ValueError("Supply the exact source hash shown in the comparison.")
        if not isinstance(allow_lossy, bool):
            raise ValueError("allowLossy must be true or false.")
        with self.lock:
            if self.job["state"] == "running":
                raise ValueError("A CapCut sync is already running.")
            report = inspect_project(self.project, self.timeline)
            if report["snapshot_hash"] != expected_hash:
                raise ValueError("CapCut changed since the comparison. Review the changes again.")
            if self.previous and self.previous.get("sourceHash") == expected_hash:
                raise ValueError("This saved CapCut version is already synced.")
            if report["missing_media"] or not report["segments"]:
                raise ValueError("Relink missing media and save the timeline before syncing.")
            if (any(item["active"] for item in report["unsupported"])
                    or _audio_offset_segments(report)) and not allow_lossy:
                raise ValueError("Some settings or trimmed audio may not transfer faithfully. Review and explicitly accept those limits.")
            token = uuid4().hex
            self.job = {"enabled": True, "state": "running", "stage": "inspecting",
                        "error": None, "revision": None, "renderPending": True,
                        "editable": False, "document": None}
            worker = threading.Thread(target=self._run, args=(expected_hash, allow_lossy, token, activate_callback),
                                      daemon=True, name="madison-capcut-sync")
            worker.start()
            return dict(self.job)

    def _stage(self, stage: str) -> None:
        with self.lock:
            self.job["stage"] = stage

    def _run(self, expected_hash: str, allow_lossy: bool, token: str, activate_callback) -> None:
        output = self.versions / token
        try:
            report = inspect_project(self.project, self.timeline)
            if report["snapshot_hash"] != expected_hash:
                raise ValueError("CapCut changed before import began. Review the changes again.")
            self._stage("importing")
            imported = import_project(self.project, self.timeline, output, self.cli,
                                      allow_lossy=allow_lossy, render=True)
            if imported["snapshot_hash"] != expected_hash or not imported.get("rendered"):
                raise ValueError("The import did not render the expected CapCut version.")
            self._stage("preparing")
            movie = output / "preview.mp4"
            _, _, duration, rendered_fps = probe(movie)
            metadata = _timeline(imported, duration, rendered_fps, imported["layer_mapping"])
            metadata_path = output / "review-timeline.json"
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            ready = output / "review-bundle"
            prepare_bundle(movie, ready, metadata_path)
            prepared = load_manifest(ready / "data.json")
            asset_files(ready, prepared)
            # A final inspection catches a save made while FFmpeg prepared the
            # derivative, even after the native importer checked its sources.
            if inspect_project(self.project, self.timeline)["snapshot_hash"] != expected_hash:
                raise ValueError("CapCut changed during sync. The old review remains active; compare and retry.")
            self._stage("activating")
            target_video = f"media/capcut-{token}.mp4"
            target_poster = f"media/capcut-{token}.jpg"
            for source, relative in ((ready / "media/preview.mp4", target_video),
                                     (ready / "media/poster.jpg", target_poster)):
                destination = self.bundle / relative
                pending = destination.with_name(destination.name + ".pending")
                with source.open("rb") as input_stream, pending.open("xb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
                    output_stream.flush()
                    os.fsync(output_stream.fileno())
                os.replace(pending, destination)
            prepared["videoUrl"] = target_video
            prepared["posterUrl"] = target_poster
            prepared["revision"] = token
            pending_manifest = self.bundle / f"data.pending-{token}.json"
            pending_manifest.write_text(json.dumps(prepared, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            validated = load_manifest(pending_manifest)
            asset_files(self.bundle, validated)
            source_hashes = imported.get("source_hashes")
            if not isinstance(source_hashes, dict) or not source_hashes:
                raise ValueError("The native import omitted its source verification record.")
            for filename, expected in source_hashes.items():
                source = Path(filename)
                if not source.is_file() or file_hash(source) != expected:
                    raise ValueError("A CapCut source changed while the review was being prepared. Compare and retry.")
            snapshot = _snapshot(imported)
            snapshot["revision"] = token
            # Retain a recovery record beside the immutable native version
            # before switching the viewer manifest. If the separate pointer
            # update fails, restart can recover by manifest revision.
            (output / "success-snapshot.json").write_text(
                json.dumps(snapshot, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            pending_snapshot = self.root / f"last-success.pending-{token}.json"
            pending_snapshot.write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            os.replace(pending_manifest, self.bundle / "data.json")
            self.previous = snapshot
            try:
                os.replace(pending_snapshot, self.snapshot_path)
            except OSError:
                # The version record above is the durable fallback; do not
                # misreport a switched, verified review as a failed import.
                pass
            editable = False
            if activate_callback is not None:
                try:
                    editable = bool(activate_callback(output / "timeline.tsrct", output))
                except (ValueError, OSError):
                    editable = False
            with self.lock:
                self.job.update({"state": "complete", "stage": "complete", "error": None,
                                 "revision": token, "renderPending": False,
                                 "editable": editable,
                                 "document": "Native project saved" if editable else
                                             ("Native project saved; Madison editor rebind required"
                                              if activate_callback is not None else
                                              "Native project saved; browser editing is not enabled")})
        except Exception as exc:
            # The version folder and its detailed report remain local. The
            # browser gets a useful stage but never a source media path.
            if output.is_dir():
                try:
                    (output / "sync-error.json").write_text(
                        json.dumps({"stage": self.job["stage"], "error": str(exc)},
                                   ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass
            with self.lock:
                stage = self.job["stage"]
                self.job.update({"state": "failed", "stage": "failed",
                                 "error": f"Sync failed during {stage}. The prior version remains available; inspect the retained local diagnostic output.",
                                 "renderPending": False})
