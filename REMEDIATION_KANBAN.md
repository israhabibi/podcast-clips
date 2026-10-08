# Remediation Kanban

**Read first:** [TECHNICAL_REMEDIATION.md](TECHNICAL_REMEDIATION.md) and [AUDIT_IMPROVEMENTS.md](AUDIT_IMPROVEMENTS.md). This board tracks proposed work and verified implementation status. `PIPELINE.md` remains the source for the existing episode review and delivery workflow. The admin page, metadata autofill, job progress endpoint, and manual YouTube queue are implemented; live OAuth flows and account identity verification remain unverified.

## Board

| Ready | Waiting / blocked | In progress | Review | Done |
| --- | --- | --- | --- | --- |
|  | [OPS-02](#ops-02-enforce-server-side-branch-protection) requires repository administrator access | [SEC-01](#sec-01-protect-oauth-routes-and-session-secret) | [ADMIN-01](#admin-01-add-top-5-compilation-ui)<br>[SEC-02](#sec-02-harden-oauth-callbacks-and-token-replacement) | [ADMIN-02](#admin-02-retry-failed-admin-submissions)<br>[PIPE-01](#pipe-01-fix-tiktok-caption-path)<br>[PIPE-02](#pipe-02-add-episode-lifecycle-and-retry)<br>[PIPE-03](#pipe-03-validate-clip-metadata)<br>[PIPE-04](#pipe-04-fail-incomplete-batches)<br>[PUB-01](#pub-01-add-review-gate-and-upload-idempotency)<br>[WEB-01](#web-01-correct-gallery-routing-and-title)<br>[MEDIA-01](#media-01-fix-face-track-fallback)<br>[QA-01](#qa-01-add-regression-tests)<br>[OPS-01](#ops-01-make-setup-reproducible) |

Move a task ID to **In progress** when work starts, to **Review** when its code and verification are ready, and to **Done** only when every acceptance checkbox is satisfied. Keep blockers in the task card. If a task needs a design choice, record the choice in the PR or an implementation note rather than silently changing the contract. Root modules and `scripts/` are canonical; files under `hermes-skill/scripts/` are forwarding shims, so update them only when shim behavior changes.

## Task cards

### SEC-01 Protect OAuth routes and session secret

- **Priority:** P0
- **Scope:** `app/__init__.py`, `app/routes/youtube.py`, `app/routes/tiktok.py`, deployment configuration
- **Depends on:** none
- **Implementation:** Add an administrator access check for both OAuth start routes and bind callbacks to that authenticated session. Require a stable `FLASK_SECRET_KEY` in production. Keep gallery routes public.

**Progress:** Login, admin session, CSRF protection, and OAuth route guards are implemented. A regression starts OAuth through the actual WSGI app, restarts in an independent Python process, and completes the mocked callback using the original session cookie; changing the secret rejects it before token exchange. The deployed service restart remains unverified. A read-only check on 2026-10-07 found `FLASK_SECRET_KEY` present in `app/.env`, both expected provider IDs missing there, and the existing YouTube token not currently valid; its authorized channel could not be inspected. Configure the IDs and complete both live provider flows before marking Done.

**Acceptance:**

- [x] Anonymous requests cannot start account linking or write token files.
- [ ] The intended operator can complete both OAuth flows in one browser session.
- [ ] A deployed service restart with the configured secret does not break an in-progress session (fresh-process WSGI regression passes; live restart still required).
- [x] Access-control tests cover both providers and callback routes.

### SEC-02 Harden OAuth callbacks and token replacement

- **Priority:** P0
- **Scope:** `app/routes/youtube.py`, `app/routes/tiktok.py`, token storage
- **Depends on:** SEC-01; coordinate because both tasks edit the same routes
- **Implementation:** Require present state and code, compare state securely, consume state and PKCE verifier, verify the linked account, and replace active tokens atomically. Return generic errors without leaking request arguments or provider token responses.

**Progress (2026-10-07):** State validation, one-time consumption, generic errors, and atomic writes are implemented. Callbacks now compare the returned YouTube channel ID or TikTok `open_id` against required environment allowlists before replacing active tokens. Mocked tests cover state mismatch/reuse, identity mismatch, and failed atomic replacement. Live provider flows and configuration of the actual intended account IDs still need operator verification.

**Acceptance:**

- [x] Missing, mismatched, and reused state values are rejected before token exchange.
- [x] An unexpected linked account leaves the existing token unchanged.
- [x] A failed token write leaves the previous token intact.
- [x] Provider calls are mocked in regression tests; no real account is linked by tests.

### PIPE-01 Fix TikTok caption path

- **Priority:** P1
- **Scope:** `scripts/tiktok_upload.py`, `hermes-skill/scripts/tiktok_upload.py`
- **Depends on:** none
- **Implementation:** Read `captions.json` from `PODCAST_WORK_DIR`, matching `caption.py` and the YouTube uploader. Validate that referenced MP4 files exist before upload.
- **Progress:** Root-level caption loading and MP4 preflight are implemented in both copies; regression tests pass without live TikTok requests.

**Acceptance:**

- [x] A temporary workspace with root-level `captions.json` is accepted.
- [x] A missing caption file or referenced clip returns a clear nonzero failure.
- [x] Both uploader copies have the same behavior.

### ADMIN-01 Add TOP 5 compilation UI

- **Priority:** P1
- **Scope:** `scripts/top5_compilation.py`, `app/routes/admin.py`, `app/templates/admin.html`
- **Depends on:** review gate and upload idempotency design
- **Progress (2026-10-07):** Workspace discovery, transcript candidates, optional LLM suggestions, background build, status polling, preview, deploy, and YouTube upload routes are implemented. Quota failures enter a persistent SQLite retry queue; `scripts/process_youtube_queue.py` and a 30-minute systemd timer process due items after 24 hours, with current-file SHA-256 approval checks. Tests cover queue claims, retry scheduling, successful upload, and changed-file rejection. Deployment of the timer and live metadata verification remain operator checks. See [TODO_COMPILATION_ADMIN.md](TODO_COMPILATION_ADMIN.md).

**Acceptance:**

- [x] Select an episode and five moments from `/admin`.
- [x] Build and preview the compilation without editing a script per request.
- [x] Upload only after review, with retry-safe metadata and quota handling.

**Remaining verification:** Exercise the deployed systemd timer and verify final tags/description with YouTube `videos.list` after a real upload. Until those runtime checks pass, keep this task in Review.

**Metadata retry checks (2026-10-07):** CLI, individual clip, TOP 5 and scheduled-worker tests cover metadata failure and repair using the same video ID. SQLite stores the insert ID before metadata requests and retains it across retry claims. Pending records remain visible in admin; incomplete metadata is not marked completed. Changed media cannot reuse the previous video's record for repair.

### ADMIN-02 Retry failed admin submissions

- **Priority:** P1
- **Scope:** `app/admin_store.py`, `app/routes/admin.py`, `app/templates/admin.html`
- **Progress (2026-10-07):** Pending and failed submission rows now expose an admin retry action. A conditional SQLite update claims the existing row before the worker starts; concurrent retries cannot launch duplicate workers, and a failed worker launch restores failed status with error detail.

**Acceptance:**

- [x] Retry does not require deleting or recreating the submission.
- [x] Anonymous requests and requests without CSRF are rejected.
- [x] Concurrent retry claims start at most one worker.
- [x] Regression tests cover retry launch, duplicate claim, and row retention.

**Admin display fix (2026-10-07):** The Detail toggle now removes/restores the HTML `hidden` attribute. Retry/process buttons appear in the source card header, independently of expanded details. Failed jobs mark progress as unknown in both initial rendering and the polling endpoint; regression tests execute the production JavaScript handler and check the rendered button/error.

### PIPE-02 Add episode lifecycle and retry

- **Priority:** P1
- **Scope:** `monitor.py`, scheduler integration, state migration
- **Depends on:** none; verify how the existing cron job consumes `NEW:` lines before changing output
- **Implementation:** Separate discovery from completion, add failed/retry state, make writes atomic, and prevent overlapping runs from corrupting state. Preserve existing processed IDs during migration and audit known failures.

**Progress (2026-10-07):** The lifecycle implementation now re-emits both `discovered` and `failed` episodes until the worker marks them `in_progress`, preventing an interrupted scheduler handoff from losing an episode. Status changes reject invalid transitions; state writes use fsync plus atomic replacement; the old processed-ID file is migrated as legacy completed records. `PIPELINE.md` documents the required worker status calls and preserves the scheduler's `NEW:` format. Four focused monitor tests cover rediscovery, retry, completion, atomic-write failure, and output compatibility.

**Acceptance:**

- [x] Discovery alone does not mark an episode completed.
- [x] A failed episode appears for retry; a completed one does not.
- [x] State survives interruption without a partial file.
- [x] The scheduler still consumes monitor output or is updated in the same change.

### PIPE-03 Validate clip metadata

- **Priority:** P1
- **Scope:** `curate.py`, `cut.py`, `cut_smart.py`, forwarding shims under `hermes-skill/scripts/`
- **Depends on:** none
- **Implementation:** Share validation for finite numeric timestamps, title, range order, duration, transcript coverage, and source-video bounds. Validate at curation and again before rendering.

**Progress (2026-10-07):** `clip_quality.py` now provides one renderer validator plus an `ffprobe` duration helper. Both `cut.py` and `cut_smart.py` reject malformed, nonfinite, out-of-bounds, overlong, untitled, or transcript-uncovered ranges before FFmpeg and preserve valid timestamps unchanged. `curate.py` continues to apply the same finite range and transcript-boundary validation before saving output. Hermes script entry points forward to the canonical root scripts.

**Acceptance:**

- [x] Negative, reversed, NaN, infinite, too-long, and past-end ranges fail before FFmpeg.
- [x] Valid ranges render unchanged.
- [x] All mirrored scripts use the same validation behavior.

### PIPE-04 Fail incomplete batches

- **Priority:** P1
- **Scope:** `caption.py`, `cut.py`, `cut_smart.py`, episode output manifest, forwarding shims under `hermes-skill/scripts/`
- **Depends on:** PIPE-03
- **Implementation:** Fail on absent API key or empty required LLM output. Return nonzero if any render fails. Produce clips through temporary files and record only finished outputs in a manifest.

**Progress (2026-10-07):** Captions now fail closed on missing keys, empty LLM responses, malformed search data, absent transcript text, invalid news URLs, and missing rendered media. `cut.py` writes temporary MP4s and exits nonzero on any failed render; `cut_smart.py` already has the same batch-failure behavior. `run_one_episode.py` atomically replaces the manifest with `processing` before work, then writes `ready_for_review` only after every output and matching nonempty caption has been checked. The manifest records filenames, SHA-256, sizes, captions, review state, and platform ID slots. Mocked LLM/render failure and manifest integrity tests pass.

**Acceptance:**

- [x] An LLM or FFmpeg failure gives the batch a nonzero exit status.
- [x] Partial MP4 files are never presented as completed clips.
- [x] The manifest lists exactly the media and captions ready for review.

### PUB-01 Add review gate and upload idempotency

- **Priority:** P1
- **Scope:** `scripts/youtube_upload.py`, `scripts/tiktok_upload.py`, forwarding shims under `hermes-skill/scripts/`, manifest
- **Depends on:** PIPE-04 and SEC-02
- **Implementation:** Require the recorded sample review/release decision before public publication. Save platform IDs after successful insert, resume failed metadata updates without re-inserting videos, and skip completed uploads on retry. Generate a YouTube description without the web-only news links.

**Acceptance:**

- [x] App-controlled YouTube publication requires release confirmation bound to the exact reviewed clip SHA-256.
- [x] Rerunning after a partial batch skips completed video IDs and repairs pending metadata without a second insert; unmanifested or changed files are rejected.
- [x] Web captions retain news links; YouTube descriptions omit them.
- [x] Retry and release-gate regressions use mocked upload/network calls.
- [x] TikTok inbox publish IDs and state are written to the episode manifest; accepted inbox items are skipped on retry.
- [x] YouTube insert and metadata repair fail closed when the token's channel is unset or mismatches `YOUTUBE_EXPECTED_CHANNEL_ID`.

**Progress (2026-10-07):** DB release approvals are digest-bound; TOP 5, API, and manual Studio flows require review confirmation tied to the current media digest before upload details are exposed or publication is recorded. CLI approval checks manifest hashes. The YouTube uploader verifies its OAuth token's channel against `YOUTUBE_EXPECTED_CHANNEL_ID` before inserting a video or repairing metadata, records IDs before metadata updates, and repairs metadata in place; TikTok records `publish_id` before transfer and marks accepted inbox items to prevent resubmission. Studio publication itself remains outside the application. Full suite passes (122 tests).

### WEB-01 Correct gallery routing and title

- **Priority:** P2
- **Scope:** `app/routes/clips.py`, `app/templates/clips.html`
- **Depends on:** none
- **Implementation:** Return 404 for a missing or incomplete requested episode, pass `episode_title` to the template, and isolate malformed metadata so the rest of the index still renders.

**Acceptance:**

- [x] A valid episode has the correct heading and clips.
- [x] A missing or incomplete episode returns 404, never another episode's content.
- [x] One malformed JSON file does not take down all gallery pages.

**Progress (2026-10-07):** Explicit episode URLs return 404 for malformed metadata, mismatched caption/clip entries, and missing media. The index skips incomplete episodes. The regression proves a healthy episode remains listed when another episode has malformed JSON and asserts the selected title in the page. The route code and acceptance checks are complete.

### MEDIA-01 Fix face-track fallback

- **Priority:** P2
- **Scope:** `cut_smart.py`, `hermes-skill/scripts/cut_smart.py`
- **Depends on:** none
- **Implementation:** Center the crop when no face is detected, clamp crop positions, and configure/check model and font paths before rendering.

**Progress (2026-10-07):** Extracted importable face tracking helpers. No-face tracks center at `w / 2`; every crop center stays within valid video bounds, including narrow-video edge cases. `cut_smart.py` rejects invalid dimensions before rendering and continues to check configured YuNet/font paths. Its Hermes entry point forwards to the canonical renderer. Focused helper tests pass.

**Acceptance:**

- [x] A no-face sample uses the image center within valid crop bounds.
- [x] Missing model or invalid video dimensions produce clear errors.
- [x] Root and skill copies remain aligned.

### QA-01 Add regression tests

- **Priority:** P1
- **Scope:** `test_pipeline.py` or a focused test package
- **Depends on:** implement alongside SEC-01 through MEDIA-01; finish after those changes
- **Implementation:** Replace tests that accept HTTP 500 or copy algorithm logic with tests of real helpers. Add fixtures for OAuth state, workspace paths, lifecycle retries, malformed gallery data, rendering failure, and upload idempotency.

**Acceptance:**

- [x] Fixed issues have regression coverage for lifecycle, validation, manifests, OAuth, gallery routing, review gates, and upload retries.
- [x] Tests use mocked OAuth/LLM/upload calls and do not publish real videos.
- [x] `python -m unittest discover -s . -p 'test_*.py'` exits nonzero on failure; 122 tests pass in a fresh dependency environment.

### OPS-01 Make setup reproducible

- **Priority:** P2
- **Scope:** `requirements.txt`, `README.md`, setup checks
- **Depends on:** none
- **Implementation:** Pin compatible Python package versions and document FFmpeg, `yt-dlp`, YuNet model, fonts, and required environment variables. Add a setup check that reports missing dependencies before processing.

**Acceptance:**

- [x] A clean environment can install the documented dependencies and run tests.
- [x] Missing model, font, and CLI tools are reported before a render starts.
- [x] The README documents the setup steps and prerequisites accurately.

**Progress (2026-10-07):** Exact direct dependency versions are pinned; Gunicorn and YouTube retry systemd service/timer templates are included. `scripts/check_setup.py` checks web or media prerequisites, and the episode runner invokes media preflight before downloading. A fresh `uv` environment installed all requirements and ran the regression suite successfully. A forced-missing dependency check reported FFmpeg, ffprobe, font, and YuNet model failures. `python3 -m venv` was unavailable because the host lacks `ensurepip`; `uv` verified the clean install requirement.

### OPS-02 Enforce server-side branch protection

- **Priority:** P2
- **Scope:** repository settings and `README.md`
- **Depends on:** repository administrator access
- **Implementation:** Require pull requests for `main` in GitHub settings or a ruleset. Explain that `hooks/pre-push` must be installed manually and is only local assistance. Record the setting in the repository documentation.

**Progress (2026-10-07):** README now correctly describes the local hook as optional and states that server-side protection is not verified. No authenticated GitHub administration tool is available in this workspace, so the remote ruleset still requires a repository administrator.

**Acceptance:**

- [ ] A direct push to `main` is rejected by the remote repository.
- [x] The README accurately distinguishes server-side protection from the optional local hook.

## Handoff notes

- Start with a Ready task and move only that task to In progress. Preserve unrelated work in the tree.
- Keep a small fixture workspace for pipeline tests; do not use real tokens or publish videos during verification.
- Update this board and the technical specification when an implementation changes a documented interface or workflow.
- The original audit did not run Python tests on this Windows host because its `python` alias was inaccessible. Run the suite in a working Python environment before marking code tasks Done.
