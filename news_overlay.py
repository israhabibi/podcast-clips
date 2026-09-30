"""Create a compact, readable ASS source card for vertical clips."""

import math
import re
import textwrap
from urllib.parse import urlsplit


def _timestamp(seconds):
    centiseconds = round(seconds * 100)
    hours, centiseconds = divmod(centiseconds, 360000)
    minutes, centiseconds = divmod(centiseconds, 6000)
    whole_seconds, centiseconds = divmod(centiseconds, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def _source_label(url):
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "net", "org", "gov"}:
        return parts[-3].upper()
    return parts[-2].upper() if len(parts) >= 2 else host.upper()


def build_news_overlay_event(sources, clip_duration, *, display_seconds=6, max_sources=3):
    """Return one upper-left source-card event, or None when no valid sources exist."""
    if isinstance(clip_duration, bool) or not isinstance(clip_duration, (int, float)):
        raise ValueError("Clip duration must be a finite number.")
    if not math.isfinite(clip_duration) or clip_duration <= 0:
        raise ValueError("Clip duration must be a finite positive number.")

    lines = ["SUMBER TERKAIT"]
    for source in sources[:max_sources] if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        title = source.get("title")
        url = source.get("url")
        if not isinstance(title, str) or not isinstance(url, str):
            continue
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        safe_title = re.sub(r"[{}\\\r\n]+", " ", title).strip()
        if not safe_title:
            continue
        label = _source_label(url)
        item_lines = textwrap.wrap(
            f"- {label}: {safe_title}", width=44, max_lines=1, placeholder="..."
        )
        lines.extend(item_lines)
    if len(lines) == 1:
        return None

    lines.append("Link lengkap di caption")
    end = float(clip_duration)
    start = max(0.0, end - min(display_seconds, end))
    text = r"{\an7\pos(64,120)}" + r"\N".join(lines)
    return f"Dialogue: 1,{_timestamp(start)},{_timestamp(end)},SourceCard,,0,0,0,,{text}"