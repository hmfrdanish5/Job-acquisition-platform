"""Save HTML/screenshots when a careers page cannot be collected."""

from __future__ import annotations

import logging
import os
from datetime import datetime

from playwright.sync_api import Page

from config import DEBUG

logger = logging.getLogger(__name__)
_snapshot_count = 0


def save_debug_artifact(page: Page, label: str) -> None:
    global _snapshot_count
    if not DEBUG.get("enabled", True):
        return
    if _snapshot_count >= DEBUG.get("max_snapshots_per_run", 8):
        return

    debug_dir = DEBUG["output_dir"]
    os.makedirs(debug_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)
    base = os.path.join(debug_dir, f"{stamp}_{safe}")

    try:
        if DEBUG.get("save_html_snapshot", True):
            with open(f"{base}.html", "w", encoding="utf-8") as handle:
                handle.write(page.content())
        if DEBUG.get("save_screenshot", True):
            page.screenshot(path=f"{base}.png", full_page=True)
        _snapshot_count += 1
        logger.info("[DEBUG] Saved artifacts for %s", label)
    except Exception as exc:
        logger.warning("[DEBUG] Could not save artifacts: %s", exc)
