"""Helpers for accurate, transcript-grounded clip selection."""

import math
import re


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number.")
    return float(value)


def _validated_segments(segments):
    if not isinstance(segments, list) or not segments:
        raise ValueError("Transcript must contain at least one segment.")
    validated = []
    previous_start = -1.0
    for index, segment in enumerate(segments, 1):
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str) or not segment["text"].strip():
            raise ValueError(f"Transcript segment {index} has no text.")
        start = _number(segment.get("start"), f"Transcript segment {index} start")
        end = _number(segment.get("end"), f"Transcript segment {index} end")
        if start < 0 or end <= start or start < previous_start:
            raise ValueError(f"Transcript segment {index} has invalid or unordered timing.")
        validated.append({"start": start, "end": end, "text": segment["text"].strip()})
        previous_start = start
    return validated


def format_timed_transcript(segments, chunk_seconds=30):
    """Keep prompt chunks compact while retaining exact times for every segment."""
    validated = _validated_segments(segments)
    lines = []
    chunk = []
    chunk_start = None
    for segment in validated:
        if chunk and segment["end"] - chunk_start > chunk_seconds:
            lines.append(" ".join(chunk))
            chunk = []
            chunk_start = None
        if chunk_start is None:
            chunk_start = segment["start"]
        chunk.append(f"[{segment['start']:.2f}-{segment['end']:.2f}] {segment['text']}")
    if chunk:
        lines.append(" ".join(chunk))
    return "\n".join(lines)


def _normalized_text(text):
    return re.sub(r"[^\w]+", " ", text.casefold(), flags=re.UNICODE).strip()


def validate_clips(clips, segments, *, min_duration=40, max_duration=70, boundary_tolerance=2):
    """Snap generated ranges to transcript boundaries and reject unsupported hooks."""
    transcript = _validated_segments(segments)
    if not isinstance(clips, list) or not clips:
        raise ValueError("LLM returned no clips.")
    starts = [segment["start"] for segment in transcript]
    ends = [segment["end"] for segment in transcript]
    validated = []
    for index, clip in enumerate(clips, 1):
        if not isinstance(clip, dict):
            raise ValueError(f"Clip {index} must be a JSON object.")
        start = _number(clip.get("start"), f"Clip {index} start")
        end = _number(clip.get("end"), f"Clip {index} end")
        title = clip.get("title")
        hook = clip.get("hook")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Clip {index} needs a nonempty title.")
        title = title.strip()
        if len(title) > 50:
            shortened = title[:47].rsplit(" ", 1)[0].rstrip()
            title = f"{shortened or title[:47].rstrip()}..."
        if not isinstance(hook, str) or not hook.strip():
            raise ValueError(f"Clip {index} needs a transcript hook.")
        snapped_start = min(starts, key=lambda boundary: abs(boundary - start))
        snapped_end = min(ends, key=lambda boundary: abs(boundary - end))
        if abs(snapped_start - start) > boundary_tolerance or abs(snapped_end - end) > boundary_tolerance:
            raise ValueError(f"Clip {index} timestamps do not match transcript segment boundaries.")
        duration = snapped_end - snapped_start
        if not min_duration <= duration <= max_duration:
            raise ValueError(f"Clip {index} duration must be {min_duration}-{max_duration} seconds after boundary alignment.")
        clip_text = " ".join(
            segment["text"] for segment in transcript
            if segment["end"] > snapped_start and segment["start"] < snapped_end
        )
        if _normalized_text(hook) not in _normalized_text(clip_text):
            first_segment = next(
                segment for segment in transcript
                if segment["end"] > snapped_start and segment["start"] < snapped_end
            )
            hook = first_segment["text"]
        validated.append({**clip, "start": snapped_start, "end": snapped_end, "title": title.strip(), "hook": hook.strip()})
    return validated