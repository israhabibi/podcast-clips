# Code audit: improvements needed

This report covers the Flask gallery, OAuth routes, episode pipeline, upload scripts, and tests in this repository. Findings are based on the checked-in code. No production configuration, running service, or external API was tested.

**Status note:** This is the original audit baseline. The local `/admin` implementation now addresses parts of the OAuth access and state findings. See [REMEDIATION_KANBAN.md](REMEDIATION_KANBAN.md) for current progress and remaining verification.

## Priority 0: secure account linking

### 1. Restrict OAuth account linking

**Files:** [`app/routes/youtube.py`](app/routes/youtube.py), [`app/routes/tiktok.py`](app/routes/tiktok.py), [`app/__init__.py`](app/__init__.py)

`/auth` and `/tiktok-auth` are available without an admin check. Their callbacks write credentials to shared token files. On a publicly reachable deployment, another person can start an OAuth flow for their own account and replace the credentials used by later uploads. File permissions protect the token from local readers but do not prevent this replacement through the application.

**Change needed:**

1. Require administrator access before starting either OAuth flow. Keep callbacks bound to the same authenticated session and verify the OAuth state before exchanging a code.
2. Verify the account or channel returned by the provider against the intended account before replacing the active token. Reject unexpected accounts.
3. Require a persistent, high-entropy `FLASK_SECRET_KEY` in production. The current random fallback in `app/__init__.py` invalidates in-progress sessions on restart or across workers.
4. Write tokens atomically to files outside the public static directory, with restrictive permissions. Keep a known-good token until the new account is verified.

**Done when:** An unauthenticated visitor cannot initiate account linking; a callback from another browser or an unexpected account cannot replace either active token; OAuth still works after a normal application restart.

### 2. Reject missing TikTok OAuth state

**File:** [`app/routes/tiktok.py`](app/routes/tiktok.py)

The callback compares `request.args.get('state')` with `session.get('tiktok_state')`. When both are absent, the values compare equal and the request proceeds to token exchange. The YouTube callback already checks that both values exist.

**Change needed:** Require nonempty stored and returned state values, compare them with `secrets.compare_digest`, and remove the state and PKCE verifier from the session after a successful exchange. Return a generic error without echoing request arguments or provider responses that may contain sensitive values.

**Done when:** Missing, mismatched, and reused state values return HTTP 400 without making a token request. A valid state succeeds once.

## Priority 1: make the pipeline reliable

### 3. Fix the TikTok captions path

**Files:** [`scripts/tiktok_upload.py`](scripts/tiktok_upload.py), [`caption.py`](caption.py)

`caption.py` writes `WORK_DIR/captions.json`, but the TikTok uploader reads `WORK_DIR/clips/captions.json`. A normal upload therefore fails before it reaches TikTok.

**Change needed:** Read `WORK_DIR / "captions.json"`, matching the YouTube uploader. Check that the file and each referenced clip exist before starting a batch. Keep the mirrored copy in `hermes-skill/scripts/tiktok_upload.py` synchronized.

**Done when:** A workspace produced by `caption.py` can be used by the TikTok uploader without moving files.

### 4. Mark episodes processed only after success

**File:** [`monitor.py`](monitor.py)

The monitor appends newly discovered video IDs to its processed file immediately. If download, curation, rendering, review, or upload later fails, the next poll will not report that episode again.

**Change needed:** Separate `discovered`, `in_progress`, `failed`, and `completed` states. Make the monitor report candidates without acknowledging them. Mark an episode completed only after the required pipeline steps succeed; preserve a failed workspace and make retry explicit. Create the state directory if needed and write state atomically.

**Done when:** A failed episode is offered for retry, while a completed episode is not processed twice.

### 5. Validate generated clip ranges before rendering

**Files:** [`curate.py`](curate.py), [`cut.py`](cut.py), [`cut_smart.py`](cut_smart.py)

The curation step checks the number of LLM-selected clips but does not validate each clip's timestamps. The renderers pass those values to FFmpeg. A negative, reversed, nonfinite, or out-of-range interval can fail rendering or produce the wrong output.

**Change needed:** Before writing `clips.json`, require numeric finite `start` and `end`, `0 <= start < end`, an allowed duration, and `end` within the measured source-video duration. Confirm each clip has transcript coverage. Validate again at the renderer boundary so manually edited metadata cannot bypass the check. Fail the batch with a clear error before producing partial output.

**Done when:** Invalid clip metadata is rejected before FFmpeg runs; valid clips render with the expected duration.

### 6. Prevent silent partial publication

**Files:** [`caption.py`](caption.py), [`cut.py`](cut.py), [`cut_smart.py`](cut_smart.py), [`scripts/youtube_upload.py`](scripts/youtube_upload.py), [`scripts/tiktok_upload.py`](scripts/tiktok_upload.py)

`caption.py` can write empty captions after an LLM request fails. The renderers print per-clip failures but finish without a failing process exit code. The YouTube uploader publishes with `privacyStatus: public` and has no batch-level checkpoint or duplicate-upload record. These behaviors make it easy to treat incomplete output as ready for deployment or to publish twice after a retry.

**Change needed:** Validate required API keys and response content; exit nonzero if any required caption or clip fails. Produce a manifest of expected files and completion status. Add an explicit reviewed/approved publishing step, persist platform video IDs, and skip already uploaded clips on retry. Consider uploading YouTube clips as private first, then changing visibility after review. Keep web captions with news links separate from YouTube descriptions as specified in `PIPELINE.md`.

**Done when:** A failed batch has a nonzero exit status and cannot be mistaken for complete; rerunning an upload does not duplicate already published clips.

## Priority 2: fix gallery behavior and operational checks

### 7. Return the requested episode and its title consistently

**Files:** [`app/routes/clips.py`](app/routes/clips.py), [`app/templates/clips.html`](app/templates/clips.html)

When a requested episode lacks metadata, `clips_view` falls back to the first available episode. It also omits `episode_title` from the template context, leaving the page heading and document title empty.

**Change needed:** Return HTTP 404 for a missing or incomplete requested episode. Pass `episode_title` from `episode_data.json`, with a readable folder-name fallback. Handle malformed JSON per episode so one bad metadata file does not break the entire index.

**Done when:** Each valid URL shows its own title and clips; an incomplete episode returns 404; other episodes remain available if one JSON file is malformed.

### 8. Improve face-track fallback and portability

**File:** [`cut_smart.py`](cut_smart.py)

When no face is found, `smooth_track` returns `(w - crop_w) / 2` as though it were a face center, but the later crop expression subtracts `crop_w / 2` again. This shifts the fallback crop left. The YuNet model and font paths are also hard-coded to Linux locations.

**Change needed:** Return `w / 2` for a centered fallback, clamp the final crop coordinate to the video bounds, and configure or package model and font paths. Check source dimensions and model availability before processing a batch.

**Done when:** A clip with no detected face uses a centered crop, and missing dependencies produce a clear setup error.

### 9. Replace smoke tests with behavior tests

**File:** [`test_pipeline.py`](test_pipeline.py)

The OAuth test accepts HTTP 500 as success, and the subtitle test reimplements timing logic instead of exercising the renderer. Several tests only check that files or strings exist. The final message says the pipeline is ready to deploy whenever these tests pass.

**Change needed:** Use Flask's test client with mocked OAuth providers to test access control, missing/reused state, and token replacement. Use temporary episode workspaces to test caption paths, invalid clip ranges, retry state, and gallery fallbacks. Test the actual subtitle helper after moving it out of top-level script execution. Make the test summary describe only what was exercised.

**Done when:** Each regression above fails a test before its fix and passes after it; test success does not claim an untested deployment is ready.

### 10. Make dependencies and branch protection reproducible

**Files:** [`requirements.txt`](requirements.txt), [`README.md`](README.md), [`hooks/pre-push`](hooks/pre-push)

Python packages are unpinned, while FFmpeg, `yt-dlp`, and the face model are external setup requirements. The README says direct pushes to `main` are blocked, but the tracked pre-push hook is not installed automatically by cloning the repository. A local hook is also bypassable.

**Change needed:** Pin compatible dependency versions, document all non-Python dependencies and the model installation, and add a repeatable environment check. Configure repository-side GitHub branch protection or a ruleset for `main`; describe the local hook as optional developer assistance.

**Done when:** A fresh machine can follow the setup instructions to run the pipeline, and server-side rules enforce the intended review workflow.

## Suggested implementation order

1. Secure OAuth routes and state handling before exposing the service publicly.
2. Fix the TikTok path and the monitor's completion tracking.
3. Add clip validation and make pipeline failures stop publication.
4. Fix gallery and crop behavior, then strengthen tests and deployment setup.

The scripts under `hermes-skill/scripts/` currently mirror the root and `scripts/` versions. Apply each script fix to both copies, or remove the duplication and have the skill invoke a single maintained implementation.
