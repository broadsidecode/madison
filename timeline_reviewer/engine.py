"""Tesseract engine compatibility status for the viewer's version panel.

Compares three facts: the installed engine, the versions this Madison supports,
and the version Mirage's official instructions currently pin. Offline or failed
checks are reported as not verified, never as current.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Callable
from urllib.request import Request, urlopen

from .tesseract import SUPPORTED_CLI_VERSIONS, ImportError, _run, locate_cli, parse_version

OFFICIAL_PIN_URL = ("https://raw.githubusercontent.com/mirage-hq/Tesseract/main/"
                    "skills/tesseract-video/references/cli-version.txt")
STATES = ("checking", "current", "engine_update", "madison_update", "engine_unsupported",
          "not_found", "not_verified")
ATTENTION = ("madison_update", "engine_unsupported")
_PLAIN_VERSION = re.compile(r"\d+\.\d+\.\d+")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def official_version(opener=urlopen) -> str | None:
    """Read Mirage's published CLI pin; None when offline or unexpected."""
    try:
        request = Request(OFFICIAL_PIN_URL, headers={"User-Agent": "Madison-engine-status"})
        with opener(request, timeout=3) as response:
            text = response.read(64).decode("utf-8").strip()
    except Exception:
        return None
    return text if _PLAIN_VERSION.fullmatch(text) else None


def installed_version(cli: str | None = None) -> str | None:
    """Return the selected or discovered engine's version without gating it."""
    try:
        return parse_version(_run([locate_cli(cli), "--version"]))
    except (ImportError, OSError):
        return None


def classify(installed: str | None, official: str | None) -> str:
    supported = SUPPORTED_CLI_VERSIONS
    if official and official not in supported and _key(official) > _key(supported[0]):
        return "madison_update"
    if installed and installed not in supported:
        return "engine_unsupported"
    if installed is None:
        return "not_found"
    if official is None:
        return "not_verified"
    if _key(installed) < _key(official):
        return "engine_update"
    return "current"


def engine_status(cli: str | None = None, *, opener=urlopen,
                  installed_reader: Callable[[str | None], str | None] = installed_version) -> dict:
    """Build the public status object. Never raises."""
    try:
        installed = installed_reader(cli)
    except Exception:
        installed = None
    official = official_version(opener)
    state = classify(installed, official)
    return {"state": state, "attention": state in ATTENTION, "installed": installed,
            "official": official, "supported": list(SUPPORTED_CLI_VERSIONS),
            "checkedAt": _timestamp()}


def public_status(value) -> dict:
    """Validate a status object before it is served to the browser."""
    checking = {"state": "checking", "attention": False, "installed": None, "official": None,
                "supported": list(SUPPORTED_CLI_VERSIONS), "checkedAt": None}
    if not isinstance(value, dict) or value.get("state") not in STATES:
        return checking
    answer = dict(checking)
    answer["state"] = value["state"]
    answer["attention"] = value["state"] in ATTENTION
    for key in ("installed", "official"):
        item = value.get(key)
        answer[key] = item if isinstance(item, str) and _PLAIN_VERSION.fullmatch(item) else None
    if isinstance(value.get("checkedAt"), str):
        answer["checkedAt"] = value["checkedAt"]
    return answer
