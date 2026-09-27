# Technical remediation specification

- **Audience:** an engineer or coding agent implementing the audit findings.
- **Source audit:** [AUDIT_IMPROVEMENTS.md](AUDIT_IMPROVEMENTS.md)
- **Work queue:** [REMEDIATION_KANBAN.md](REMEDIATION_KANBAN.md)

This document describes the desired behavior and implementation boundaries. Review `PIPELINE.md` before changing the episode workflow, especially its sample-review requirement. The current implementation includes a password-protected `/admin` page and a local manual YouTube-link queue. The remaining gaps below still require verification or follow-up work.

## 1. Current system and trust boundaries

| Component | Inputs | Output or side effect | Trust boundary |
| --- | --- | --- | --- |
| Flask gallery, `app/routes/clips.py` | URL path and deployed JSON | Public HTML and video responses | Public requests must not control filesystem access or OAuth credentials. |
| OAuth routes, `app/routes/youtube.py` and `app/routes/tiktok.py` | Browser session and provider callback | Replaces a shared platform token file | Only the operator may link an account; the callback must belong to the same session and intended account. |
| `monitor.py` | YouTube playlist feeds | New-episode records | Feed discovery is not proof that processing succeeded. |
| `curate.py` and `caption.py` | Transcript, search results, LLM responses | JSON used by renderers, gallery, and uploaders | External and generated content must be validated before use. |
| `cut.py` and `cut_smart.py` | Source video, clip JSON, transcript | Rendered MP4 files | Metadata must be valid before invoking FFmpeg. |
| Upload scripts | Captions, MP4 files, shared OAuth tokens | External publication | Uploads must match reviewed media and be safe to retry. |

The repository contains mirrored pipeline scripts under `hermes-skill/scripts/`. A fix to a root script or `scripts/` must also be applied to its mirror, or the duplication should be removed by making the skill invoke one maintained implementation. Avoid independent behavior in the two copies.

### Current admin entry point

`/admin/login` accepts the password represented by `ADMIN_PASSWORD_HASH` and requires a stable `FLASK_SECRET_KEY`. `/admin` displays OAuth connection buttons and a YouTube URL form. The form writes normalized links to the SQLite queue at `ADMIN_DB_PATH` (`app/data/admin.sqlite3` by default). `scripts/admin_queue.py` exposes pending items to a local pipeline agent and allows status updates. Submission does not execute a download, render, or upload. See `README.md` for setup. Live OAuth flows have not yet been verified in this checkout, and account-identity verification remains to be implemented.

## 2. Security design: OAuth and tokens

### Access control

- Protect `/auth` and `/tiktok-auth` at the application layer or with a trusted gateway that cannot be bypassed by direct access to Flask. Do not rely on an unpublished URL.
- Bind callback authorization to the operator's authenticated browser session. Unauthorized start requests must not create OAuth state; unauthorized callbacks must not write a token.
- Require `FLASK_SECRET_KEY` to be stable and secret in production. A random key generated on each process start breaks in-flight OAuth and sessions across workers. Update tests that currently expect the random fallback.
- Keep the gallery routes public. OAuth protection should not block normal viewing.

### Callback rules

For both providers, the callback must require a stored state, a returned state, an authorization code, and any PKCE verifier needed by the provider. Compare states with `secrets.compare_digest`. After a valid comparison, consume state and verifier so the flow cannot be reused; an exchange failure requires a fresh start. Handle provider errors with a generic user-facing response and structured server-side logs that omit codes, tokens, and secrets.

The existing TikTok comparison permits an absent state when the session also has none. The YouTube flow checks both states, but should also consume the values after validation.

### Token replacement

1. Exchange the code and validate the provider response; do not assume HTTP 200 means usable credentials.
2. Query the linked channel/account identity and compare it with an operator-configured expected identity before replacing the active token. If account verification is unavailable, retain an explicit operator approval step before activating it.
3. Write a candidate token to a temporary file in the token directory, restrict its permissions, then replace the active file atomically. Do not leave a truncated active token on failure.
4. Keep token paths outside `app/static/`; never log token contents. Apply equivalent protection to refresh-token updates in the uploaders.

**Security checks:** anonymous start is denied; callback with missing, mismatched, or reused state is denied without a provider token request; a valid callback succeeds once; an unexpected account leaves the old token unchanged; restart with the configured secret preserves a valid in-progress session.

## 3. Episode lifecycle and retry contract

### Discovery versus completion

`monitor.py` currently appends every newly seen ID to `tempo_podcast_processed.txt` as soon as it prints `NEW`. Replace this with an explicit lifecycle. A possible record format is:

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

Use an atomic state write and a lock or equivalent guard if cron jobs can overlap. Keep the current `NEW:<show>:<video_id>:<title>` output consumable until the scheduler is updated. Decide how to migrate existing processed IDs; preserve them as legacy records and audit any known failures instead of silently retrying everything or discarding the file.

**Lifecycle checks:** discovery reports a new ID; a simulated failure makes it retryable; a completed ID is not reprocessed; overlapping runs cannot corrupt state.

### Workspace contract

Every episode uses its own `PODCAST_WORK_DIR`. The expected files are `transcript.json`, `clips.json`, `episode_data.json`, `captions.json`, and `clips/clipNN.mp4`. The TikTok uploader currently looks for `clips/captions.json`; change it to the workspace root. Validate required files before a batch starts, and never silently skip a referenced missing clip.

## 4. Clip data validation and rendering

### Input schema

Each clip needs a nonempty title and finite numeric `start` and `end`. Require `0 <= start < end`, an allowed duration, and an end time within the source video. The curation step can check transcript bounds; the rendering step must check actual media duration with `ffprobe`, because the source path is passed to the renderer. Confirm the transcript is ordered, nonempty, and covers each clip. Reject the whole manifest with a clear error before producing partial output.

Avoid accepting Python `bool` as a number and reject NaN or infinity. Keep one validator shared by `curate.py`, `cut.py`, and `cut_smart.py` where practical. If generated JSON fails validation, preserve the raw response for debugging only in the private workspace, without secrets.

### Rendering behavior

- Return a nonzero process exit code if any clip fails. Include the clip number and a concise FFmpeg error in logs.
- Write rendered clips to temporary filenames and rename them only after successful FFmpeg completion so partial MP4 files are not mistaken for finished output.
- In `cut_smart.py`, no-face fallback should use image center `w / 2`; clamp the final crop offset to `[0, w - crop_w]`. Validate dimensions before face tracking. Make the YuNet model and font paths configurable and check them at startup.
- Keep subtitle timing logic in an importable helper and test that helper directly, including short segments and boundary overlaps.

**Rendering checks:** invalid ranges fail before FFmpeg, a no-face clip is centered, one failed render makes the batch fail, and no incomplete MP4 is listed as finished.

## 5. Caption and publication contract

`caption.py` must fail when its API key is absent or a required LLM response is empty. Validate `search_results.json` URLs before putting them on the public site. Keep news links in web captions but produce a separate YouTube description without those links, matching `PIPELINE.md`. Validate that every caption entry points to the corresponding rendered MP4.

Introduce a per-episode manifest that records the source ID, clip number, output filename, content hash, review status, and platform upload IDs. Use it for idempotency: a retry must not upload a clip again when its completed platform ID is already recorded. Record a platform ID immediately after successful publication. A failed metadata-update step after a successful YouTube insert must remain recoverable without a second insert.

`scripts/youtube_upload.py` currently sets `privacyStatus` to `public`. Require the workflow's sample review and explicit release decision before public publication. A private-first upload may help, but the final design must keep the review gate and avoid accidental release. TikTok inbox delivery also needs a recorded outcome so a retry does not submit duplicates.

**Publication checks:** missing media or captions stop the batch; a failed LLM call does not create a successful manifest; rerunning after a partial upload does not duplicate a completed clip; public visibility is impossible before the recorded review decision.

## 6. Gallery behavior

For `/clips/<podcast>/<episode>`, return the requested episode or HTTP 404. Do not render an unrelated first episode when metadata is missing. Pass `episode_title` to `clips.html`, preferring `episode_data.json` and falling back to a readable directory-derived title. Treat malformed episode JSON as an error for that episode, not a failure of the entire index. Keep path validation and use `url_for` when constructing media URLs so path components are escaped consistently.

**Gallery checks:** a valid episode has the correct heading and media; a missing/incomplete episode returns 404; one malformed metadata file does not make other episodes unavailable.

## 7. Tests, dependencies, and rollout

Use Flask's test client and mocked provider calls for OAuth behavior. Add temporary-workspace tests for file layout, range validation, failed render exit status, lifecycle retry, and uploader idempotency. Tests should exercise production helpers, not copy their logic. Do not accept HTTP 500 as a successful route test. Keep network publication disabled in tests.

Pin compatible Python package versions and document FFmpeg, `yt-dlp`, the face model, and fonts. A fresh environment should be able to run a setup check before processing media. Configure server-side protection for `main`; the tracked local pre-push hook is not installed by a clone and cannot enforce repository policy by itself.

Suggested rollout: first deploy OAuth access control and state validation; then repair workspace and retry behavior; then add validation and publication checkpoints; finally improve gallery and rendering. Test each change with local fixtures before using real credentials or publishing. Python tests were not run during the original audit because the available `python` command on this Windows host was an inaccessible WindowsApps alias.
