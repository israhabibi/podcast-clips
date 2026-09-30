# Remediation Kanban

**Read first:** [TECHNICAL_REMEDIATION.md](TECHNICAL_REMEDIATION.md) and [AUDIT_IMPROVEMENTS.md](AUDIT_IMPROVEMENTS.md). This board tracks proposed work; no task is marked complete. `PIPELINE.md` remains the source for the existing episode review and delivery workflow. The new `/admin` page and manual YouTube queue are implemented locally but have not been run with live OAuth credentials.

## Board

| Ready | Waiting / blocked | In progress | Review | Done |
| --- | --- | --- | --- | --- |
| [PIPE-02](#pipe-02-add-episode-lifecycle-and-retry)<br>[PIPE-03](#pipe-03-validate-clip-metadata)<br>[WEB-01](#web-01-correct-gallery-routing-and-title)<br>[MEDIA-01](#media-01-fix-face-track-fallback)<br>[OPS-01](#ops-01-make-setup-reproducible) | [PIPE-04](#pipe-04-fail-incomplete-batches) after PIPE-03<br>[PUB-01](#pub-01-add-review-gate-and-upload-idempotency) after PIPE-04 and SEC-02<br>[QA-01](#qa-01-add-regression-tests) alongside completed code tasks<br>[OPS-02](#ops-02-enforce-server-side-branch-protection) requires repository administrator access | [SEC-01](#sec-01-protect-oauth-routes-and-session-secret)<br>[SEC-02](#sec-02-harden-oauth-callbacks-and-token-replacement) | None | [PIPE-01](#pipe-01-fix-tiktok-caption-path) |

Move a task ID to **In progress** when work starts, to **Review** when its code and verification are ready, and to **Done** only when every acceptance checkbox is satisfied. Keep blockers in the task card. If a task needs a design choice, record the choice in the PR or an implementation note rather than silently changing the contract. Work on the `hermes-skill/scripts/` mirror whenever a mirrored script changes.

## Task cards

### SEC-01 Protect OAuth routes and session secret

- **Priority:** P0
- **Scope:** `app/__init__.py`, `app/routes/youtube.py`, `app/routes/tiktok.py`, deployment configuration
- **Depends on:** none
- **Implementation:** Add an administrator access check for both OAuth start routes and bind callbacks to that authenticated session. Require a stable `FLASK_SECRET_KEY` in production. Keep gallery routes public.

**Progress:** Login, admin session, CSRF protection, and OAuth route guards are implemented. Confirm both live provider flows and deployment configuration before marking Done.

**Acceptance:**

- [ ] Anonymous requests cannot start account linking or write token files.
- [ ] The intended operator can complete both OAuth flows in one browser session.
- [ ] A restart with the configured secret does not break an in-progress session.
- [ ] Access-control tests cover both providers and callback routes.

### SEC-02 Harden OAuth callbacks and token replacement

- **Priority:** P0
- **Scope:** `app/routes/youtube.py`, `app/routes/tiktok.py`, token storage
- **Depends on:** SEC-01; coordinate because both tasks edit the same routes
- **Implementation:** Require present state and code, compare state securely, consume state and PKCE verifier, verify the linked account, and replace active tokens atomically. Return generic errors without leaking request arguments or provider token responses.

**Progress:** State validation, one-time consumption, generic errors, and atomic writes are implemented. Expected account/channel verification remains open.

**Acceptance:**

- [ ] Missing, mismatched, and reused state values are rejected before token exchange.
- [ ] An unexpected linked account leaves the existing token unchanged.
- [ ] A failed token write leaves the previous token intact.
- [ ] Provider calls are mocked in regression tests; no real account is linked by tests.

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

### PIPE-02 Add episode lifecycle and retry

- **Priority:** P1
- **Scope:** `monitor.py`, scheduler integration, state migration
- **Depends on:** none; verify how the existing cron job consumes `NEW:` lines before changing output
- **Implementation:** Separate discovery from completion, add failed/retry state, make writes atomic, and prevent overlapping runs from corrupting state. Preserve existing processed IDs during migration and audit known failures.

**Acceptance:**

- [ ] Discovery alone does not mark an episode completed.
- [ ] A failed episode appears for retry; a completed one does not.
- [ ] State survives interruption without a partial file.
- [ ] The scheduler still consumes monitor output or is updated in the same change.

### PIPE-03 Validate clip metadata

- **Priority:** P1
- **Scope:** `curate.py`, `cut.py`, `cut_smart.py`, mirrored scripts
- **Depends on:** none
- **Implementation:** Share validation for finite numeric timestamps, title, range order, duration, transcript coverage, and source-video bounds. Validate at curation and again before rendering.

**Acceptance:**

- [ ] Negative, reversed, NaN, infinite, too-long, and past-end ranges fail before FFmpeg.
- [ ] Valid ranges render unchanged.
- [ ] All mirrored scripts use the same validation behavior.

### PIPE-04 Fail incomplete batches

- **Priority:** P1
- **Scope:** `caption.py`, `cut.py`, `cut_smart.py`, episode output manifest, mirrored scripts
- **Depends on:** PIPE-03
- **Implementation:** Fail on absent API key or empty required LLM output. Return nonzero if any render fails. Produce clips through temporary files and record only finished outputs in a manifest.

**Acceptance:**

- [ ] An LLM or FFmpeg failure gives the batch a nonzero exit status.
- [ ] Partial MP4 files are never presented as completed clips.
- [ ] The manifest lists exactly the media and captions ready for review.

### PUB-01 Add review gate and upload idempotency

- **Priority:** P1
- **Scope:** `scripts/youtube_upload.py`, `scripts/tiktok_upload.py`, mirrored scripts, manifest
- **Depends on:** PIPE-04 and SEC-02
- **Implementation:** Require the recorded sample review/release decision before public publication. Save platform IDs after successful insert, resume failed metadata updates without re-inserting videos, and skip completed uploads on retry. Generate a YouTube description without the web-only news links.

**Acceptance:**

- [ ] Public YouTube publication cannot occur before the review decision.
- [ ] Rerunning after a partial batch does not duplicate uploaded clips.
- [ ] Web captions retain news links; YouTube descriptions omit them.
- [ ] Network calls are mocked in retry tests.

### WEB-01 Correct gallery routing and title

- **Priority:** P2
- **Scope:** `app/routes/clips.py`, `app/templates/clips.html`
- **Depends on:** none
- **Implementation:** Return 404 for a missing or incomplete requested episode, pass `episode_title` to the template, and isolate malformed metadata so the rest of the index still renders.

**Acceptance:**

- [ ] A valid episode has the correct heading and clips.
- [ ] A missing or incomplete episode returns 404, never another episode's content.
- [ ] One malformed JSON file does not take down all gallery pages.

### MEDIA-01 Fix face-track fallback

- **Priority:** P2
- **Scope:** `cut_smart.py`, `hermes-skill/scripts/cut_smart.py`
- **Depends on:** none
- **Implementation:** Center the crop when no face is detected, clamp crop positions, and configure/check model and font paths before rendering.

**Acceptance:**

- [ ] A no-face sample uses the image center within valid crop bounds.
- [ ] Missing model or invalid video dimensions produce clear errors.
- [ ] Root and skill copies remain aligned.

### QA-01 Add regression tests

- **Priority:** P1
- **Scope:** `test_pipeline.py` or a focused test package
- **Depends on:** implement alongside SEC-01 through MEDIA-01; finish after those changes
- **Implementation:** Replace tests that accept HTTP 500 or copy algorithm logic with tests of real helpers. Add fixtures for OAuth state, workspace paths, lifecycle retries, malformed gallery data, rendering failure, and upload idempotency.

**Acceptance:**

- [ ] Each fixed issue has a regression test that would fail on the original behavior.
- [ ] Tests use no live OAuth credentials, LLM calls, or real publication.
- [ ] The test summary states what was actually exercised and exits nonzero on failure.

### OPS-01 Make setup reproducible

- **Priority:** P2
- **Scope:** `requirements.txt`, `README.md`, setup checks
- **Depends on:** none
- **Implementation:** Pin compatible Python package versions and document FFmpeg, `yt-dlp`, YuNet model, fonts, and required environment variables. Add a setup check that reports missing dependencies before processing.

**Acceptance:**

- [ ] A clean environment can install the documented dependencies and run tests.
- [ ] Missing model or CLI tools are reported before a render starts.
- [ ] The README does not promise capabilities that setup does not provide.

### OPS-02 Enforce server-side branch protection

- **Priority:** P2
- **Scope:** repository settings and `README.md`
- **Depends on:** repository administrator access
- **Implementation:** Require pull requests for `main` in GitHub settings or a ruleset. Explain that `hooks/pre-push` must be installed manually and is only local assistance. Record the setting in the repository documentation.

**Acceptance:**

- [ ] A direct push to `main` is rejected by the remote repository.
- [ ] The README accurately distinguishes server-side protection from the optional local hook.

## Handoff notes

- Start with a Ready task and move only that task to In progress. Preserve unrelated work in the tree.
- Keep a small fixture workspace for pipeline tests; do not use real tokens or publish videos during verification.
- Update this board and the technical specification when an implementation changes a documented interface or workflow.
- The original audit did not run Python tests on this Windows host because its `python` alias was inaccessible. Run the suite in a working Python environment before marking code tasks Done.
