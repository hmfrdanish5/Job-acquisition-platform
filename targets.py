"""Local acquisition-target registry. JSON file, not a cloud service."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from config import TARGETS
from url_safety import is_safe_public_url

logger = logging.getLogger(__name__)


@dataclass
class AcquisitionTarget:
    id: str
    name: str
    url: str
    company: str = ""
    enabled: bool = True
    keywords: str = ""
    notes: str = ""

    def validation_error(self) -> str | None:
        if not self.id.strip():
            return "Target is missing id."
        if not self.url.strip():
            return f"Target {self.id} is missing url."
        ok, reason = is_safe_public_url(self.url)
        if not ok:
            return f"Target {self.id}: {reason}"
        return None


def targets_config_path() -> Path:
    configured = Path(TARGETS["path"])
    if configured.is_file():
        return configured
    return Path(TARGETS["example_path"])


def load_targets(*, enabled_only: bool = False) -> list[AcquisitionTarget]:
    path = targets_config_path()
    if not path.is_file():
        logger.warning("No targets file at %s or %s", TARGETS["path"], TARGETS["example_path"])
        return []

    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    raw_list = payload.get("targets", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_list, list):
        raise ValueError("targets file must contain a list or {\"targets\": [...]}")

    targets: list[AcquisitionTarget] = []
    seen: set[str] = set()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        target = AcquisitionTarget(
            id=str(item.get("id") or "").strip(),
            name=str(item.get("name") or item.get("id") or "").strip(),
            url=str(item.get("url") or "").strip(),
            company=str(item.get("company") or item.get("company_name") or "").strip(),
            enabled=bool(item.get("enabled", True)),
            keywords=str(item.get("keywords") or "").strip(),
            notes=str(item.get("notes") or "").strip(),
        )
        if not target.id:
            continue
        if target.id in seen:
            logger.warning("Duplicate target id %s — skipping", target.id)
            continue
        seen.add(target.id)
        if enabled_only and not target.enabled:
            continue
        targets.append(target)
    return targets


def get_target(target_id: str) -> AcquisitionTarget | None:
    needle = (target_id or "").strip()
    for target in load_targets(enabled_only=False):
        if target.id == needle:
            return target
    return None
