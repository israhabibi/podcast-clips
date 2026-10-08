"""Build and atomically persist the review-ready media manifest."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def write_manifest(work_dir, manifest):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".episode-manifest-", suffix=".json", dir=work_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, work_dir / "episode_manifest.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def processing_manifest(video_id, podcast, title):
    return {
        "schema_version": 1,
        "video_id": video_id,
        "podcast": podcast,
        "episode_title": title,
        "status": "processing",
        "review_status": "pending",
        "clips": [],
    }


def build_review_manifest(work_dir, video_id, podcast, title, clips, captions):
    """Require a complete, one-to-one set of rendered media and captions."""
    work_dir = Path(work_dir)
    clips_dir = work_dir / "clips"
    if not isinstance(clips, list) or not clips:
        raise ValueError("Cannot mark episode ready: clips.json contains no clips.")
    if not isinstance(captions, dict):
        raise ValueError("Cannot mark episode ready: captions.json must be an object.")
    expected_keys = {str(index) for index in range(1, len(clips) + 1)}
    if set(captions) != expected_keys:
        raise ValueError("Cannot mark episode ready: captions do not match the rendered clip list.")

    manifest_clips = []
    for index, clip in enumerate(clips, 1):
        if not isinstance(clip, dict):
            raise ValueError(f"Clip {index} metadata must be an object.")
        filename = clip.get("filename") or f"clip{index:02d}.mp4"
        if not isinstance(filename, str) or not filename or Path(filename).name != filename:
            raise ValueError(f"Clip {index} has an invalid output filename.")
        caption = captions[str(index)]
        if not isinstance(caption, dict) or caption.get("clip") != filename:
            raise ValueError(f"Caption {index} does not reference {filename}.")
        if not isinstance(caption.get("caption"), str) or not caption["caption"].strip():
            raise ValueError(f"Caption {index} is empty.")
        if caption.get("title") != clip.get("title"):
            raise ValueError(f"Caption {index} title does not match clip metadata.")
        media = clips_dir / filename
        if not media.is_file() or media.stat().st_size <= 0:
            raise ValueError(f"Rendered clip {index} is missing or empty: {filename}.")
        digest = hashlib.sha256()
        with media.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        manifest_clips.append({
            "clip_num": index,
            "filename": filename,
            "sha256": digest.hexdigest(),
            "file_size": media.stat().st_size,
            "start": clip["start"],
            "end": clip["end"],
            "title": clip["title"],
            "caption": caption["caption"],
            "review_status": "pending",
            "platform_upload_ids": {},
        })
    return {
        "schema_version": 1,
        "video_id": video_id,
        "podcast": podcast,
        "episode_title": title,
        "status": "ready_for_review",
        "review_status": "pending",
        "clips": manifest_clips,
    }
