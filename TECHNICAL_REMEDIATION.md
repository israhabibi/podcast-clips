# Technical remediation specification

- **Audience:** an engineer or coding agent implementing the audit findings.
- **Source audit:** [AUDIT_IMPROVEMENTS.md](AUDIT_IMPROVEMENTS.md)
- **Work queue:** [REMEDIATION_KANBAN.md](REMEDIATION_KANBAN.md)

This document records the intended behavior and implementation boundaries. Completed code work is tracked in `REMEDIATION_KANBAN.md`; remaining deployment and live-provider verification is called out there. Review `PIPELINE.md` before changing the episode workflow, especially its sample-review requirement.

## 1. Current system and trust boundaries

| Component | Inputs | Output or side effect | Trust boundary |
| --- | --- | --- | --- |
| Flask gallery, `app/routes/clips.py` | URL path and deployed JSON | Public HTML and video responses | Public requests must not control filesystem access or OAuth credentials. |
| OAuth routes, `app/routes/youtube.py` and `app/routes/tiktok.py` | Browser session and provider callback | Replaces a shared platform token file | Only the operator may link an account; the callback must belong to the same session and intended account. |
| `monitor.py` | YouTube playlist feeds | New-episode records | Feed discovery is not proof that processing succeeded. |
| `curate.py` and `caption.py` | Transcript, search results, LLM responses | JSON used by renderers, gallery, and uploaders | External and generated content must be validated before use. |
| `cut.py` and `cut_smart.py` | Source video, clip JSON, transcript | Rendered MP4 files | Metadata must be valid before invoking FFmpeg. |
| Upload scripts | Captions, MP4 files, shared OAuth tokens | External publication | Uploads must match reviewed media and be safe to retry. |

All pipeline scripts live in exactly ONE place:
- top-level scripts (`caption.py`, `clip_quality.py`, `curate.py`, `cut.py`, `cut_smart.py`, `news_overlay.py`) at the repo root;
- utility scripts (`tiktok_upload.py`, `youtube_upload.py`, `youtube_transcript_to_segments.py`, `admin_queue.py`, `process_episode.py`, `build_compilation.py`, `top5_compilation.py`, etc.) under `<repo>/scripts/`.
Hermes-skill ships a thin shim per pipeline script in `hermes-skill/scripts/` that forwards argv/env/CWD to the maintained copy above. After a pipeline script change you only need to re-run `cp -r hermes-skill ~/.hermes/skills/media/podcast-clipping` if the install step changed (normally once per machine setup). Override env vars are provided for nonstandard checkouts: `PODCAST_CLIPS_REPO=<abs_path>` and `PODCAST_CLIPS_PYTHON=<abs_python_interp>`. Do NOT edit the shim copies independently; fix only the single maintained implementation.

### Current admin entry point

`/admin/login` accepts the password represented by `ADMIN_PASSWORD_HASH` and requires a stable `FLASK_SECRET_KEY`. Production startup rejects a missing key. `/admin` displays OAuth connection buttons and a YouTube URL form. The form writes normalized links to the SQLite queue at `ADMIN_DB_PATH` (`app/data/admin.sqlite3` by default). `scripts/admin_queue.py` exposes pending items to a local pipeline agent and allows status updates. Submission does not execute a download, render, or upload unless “Proses sekarang” is selected; pending or failed submissions can be retried from the admin queue without deleting their row. OAuth callbacks require configured expected account IDs and verify provider identity before atomically replacing a token. Live OAuth flows remain unverified in this checkout; configure `TIKTOK_EXPECTED_OPEN_ID` and reconnect both providers to verify.

## 2. Security design: OAuth and tokens

### Access control

- Protect `/auth` and `/tiktok-auth` at the application layer or with a trusted gateway that cannot be bypassed by direct access to Flask. Do not rely on an unpublished URL.
- Bind callback authorization to the operator's authenticated browser session. Unauthorized start requests must not create OAuth state; unauthorized callbacks must not write a token.
- Production startup requires a stable `FLASK_SECRET_KEY`; local development can still use its generated fallback. A random key generated on each process start breaks in-flight OAuth and sessions across workers.
- Keep the gallery routes public. OAuth protection should not block normal viewing.

### Callback rules

For both providers, the callback must require a stored state, a returned state, an authorization code, and any PKCE verifier needed by the provider. Compare states with `secrets.compare_digest`. After a valid comparison, consume state and verifier so the flow cannot be reused; an exchange failure requires a fresh start. Handle provider errors with a generic user-facing response and structured server-side logs that omit codes, tokens, and secrets.

Both callbacks now reject missing, mismatched, or reused state before token exchange and consume state and PKCE data. Callback errors are generic and do not expose provider responses.

### Token replacement

1. Exchange the code and validate the provider response; do not assume HTTP 200 means usable credentials.
2. Query the linked channel/account identity and compare it with an operator-configured expected identity before replacing the active token. If account verification is unavailable, retain an explicit operator approval step before activating it.
3. Write a candidate token to a temporary file in the token directory, restrict its permissions, then replace the active file atomically. Do not leave a truncated active token on failure.
4. Keep token paths outside `app/static/`; never log token contents. Apply equivalent protection to refresh-token updates in the uploaders.

**Security checks:** anonymous start is denied; callback with missing, mismatched, or reused state is denied without a provider token request; a valid callback succeeds once; an unexpected account leaves the old token unchanged; restart with the configured secret preserves a valid in-progress session.

**Current verification:** route and mocked callback tests pass. Live browser flows and restart persistence still need deployment verification. The YouTube allowlist value is recorded in `.env.example`; configure it and `TIKTOK_EXPECTED_OPEN_ID` in the runtime environment before reconnecting accounts.

## 3. Episode lifecycle and retry contract

### Discovery versus completion

The previous monitor marked an episode complete as soon as it printed `NEW`. It now uses an explicit lifecycle. The persisted record format is:

```json
{
  "video_id": "youtube-id",
  "podcast": "jelasin-dong",
  "published": "2026-09-27T00:00:00+00:00",
  "status": "discovered",
  "attempts": 0,
  "last_error": null
}
```

Allowed status transitions are `discovered -> in_progress -> completed`, and `in_progress -> failed -> in_progress` for retries. Discovery may update metadata but must not mark an episode completed. Completion occurs only after the required outputs and delivery steps have succeeded under the existing review contract. If review or publication is pending, keep a distinct pending state rather than treating the episode as complete.

State writes are atomic, the existing `NEW:<show>:<video_id>:<title>` output is preserved for the scheduler, and legacy processed IDs are migrated as completed records. Discovery and failed records are re-emitted until a worker claims them in progress.

**Lifecycle checks:** discovery reports a new ID; a simulated failure makes it retryable; a completed ID is not reprocessed; overlapping runs cannot corrupt state.

### Workspace contract

Every episode uses its own `PODCAST_WORK_DIR`. The expected files are `transcript.json`, `clips.json`, `episode_data.json`, root-level `captions.json`, and `clips/clipNN.mp4`. Both uploaders read the workspace-root captions and validate required files before a batch; a referenced missing clip fails the batch.

## 4. Clip data validation and rendering

### Input schema

Each clip needs a nonempty title and finite numeric `start` and `end`. Require `0 <= start < end`, an allowed duration, and an end time within the source video. The curation step can check transcript bounds; the rendering step must check actual media duration with `ffprobe`, because the source path is passed to the renderer. Confirm the transcript is ordered, nonempty, and covers each clip. Reject the whole manifest with a clear error before producing partial output.

Avoid accepting Python `bool` as a number and reject NaN or infinity. Keep one validator shared by `curate.py`, `cut.py`, and `cut_smart.py` where practical. If generated JSON fails validation, preserve the raw response for debugging only in the private workspace, without secrets.

### Rendering behavior

- Return a nonzero process exit code if any clip fails. Include the clip number and a concise FFmpeg error in logs.
- Write rendered clips to temporary filenames and rename them only after successful FFmpeg completion so partial MP4 files are not mistaken for finished output.
- In `cut_smart.py`, no-face fallback uses image center `w / 2`; crop positions stay within `[0, w - crop_w]`. `face_tracking.py` exposes the helper for tests. The renderer rejects invalid dimensions before tracking and checks configurable YuNet model and font paths at startup.
- Both renderers use `subtitle_timing.subtitle_chunks`; tests call this helper directly for short segments, boundary overlaps, and blank transcript text.

**Rendering checks:** invalid ranges fail before FFmpeg, a no-face clip is centered, one failed render makes the batch fail, and no incomplete MP4 is listed as finished.

## 5. Caption and publication contract

`caption.py` must fail when its API key is absent or a required LLM response is empty. Validate `search_results.json` URLs before putting them on the public site. Keep news links in web captions but produce a separate YouTube description without those links, matching `PIPELINE.md`. Validate that every caption entry points to the corresponding rendered MP4.

`episode_manifest.json` records source identity, clip filenames, SHA-256 hashes, captions, review status, and platform upload IDs. CLI release approval and retries validate the exact reviewed media. Before YouTube insertion or metadata repair, the uploader verifies the token's channel against `YOUTUBE_EXPECTED_CHANNEL_ID`; missing or mismatched configuration fails closed. The uploader records a platform ID immediately after insertion and repairs failed metadata updates without inserting a second video. Admin API upload history and digest-bound release approvals are stored in SQLite; quota failures enter a scheduled retry queue. Metadata verification compares persisted title, description, category and all requested tags. A mismatch keeps the admin record at `metadata_pending` and schedules repair using its retained video ID; the CLI returns nonzero. Default tags contain 15 entries, and title normalization preserves exactly one `#Shorts` within 100 characters.

YouTube uploads are public, so app-controlled upload paths require explicit release approval bound to the current file digest. The YouTube retry worker also rechecks the digest before publishing. TikTok delivery uses the provider inbox flow; the episode manifest records its publish ID before transfer and marks accepted inbox submissions so retries skip them. Live inbox behavior still requires provider-side verification.

**Publication checks:** missing media or captions stop the batch; a failed LLM call does not create a successful manifest; rerunning after a partial upload does not duplicate a completed clip; public visibility is impossible before the recorded review decision.

## 6. Gallery behavior

For `/clips/<podcast>/<episode>`, return the requested episode or HTTP 404. Do not render an unrelated first episode when metadata is missing. Pass `episode_title` to `clips.html`, preferring `episode_data.json` and falling back to a readable directory-derived title. Treat malformed episode JSON as an error for that episode, not a failure of the entire index. Keep path validation and use `url_for` when constructing media URLs so path components are escaped consistently.

**Gallery checks:** a valid episode has the correct heading and media; a missing/incomplete episode returns 404; one malformed metadata file does not make other episodes unavailable.

## 7. Tests, dependencies, and rollout

The regression suite uses Flask's test client, mocked provider calls, and temporary workspaces for file layout, range validation, render failures, lifecycle retry, upload approval, channel identity, and idempotency. It tests production helpers rather than copied logic, fails on unexpected HTTP 500 responses, and does not publish videos. The clean dependency install and 122-test suite pass in a fresh `uv` environment.

Direct Python dependencies are pinned; `scripts/check_setup.py` validates web and media prerequisites before service startup or processing. README documents FFmpeg, `yt-dlp`, face model, fonts, environment variables, and the systemd retry timer. Remote branch protection for `main` is still unverified and requires an authenticated repository administrator; the local pre-push hook is optional and cannot enforce remote policy.

Suggested rollout: first deploy OAuth access control and state validation; then repair workspace and retry behavior; then add validation and publication checkpoints; finally improve gallery and rendering. Test each change with local fixtures before using real credentials or publishing. Python tests were not run during the original audit because the available `python` command on this Windows host was an inaccessible WindowsApps alias.
