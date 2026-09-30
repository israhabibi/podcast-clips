---
name: podcast-clipping
description: Use when clipping a podcast video into TikTok/Shorts clips and publishing to YouTube, web, or Telegram.
---

# Podcast Clipping Pipeline

Automated pipeline on this machine, all in `~/podcast-clips/` (venv at `.venv` with faster-whisper, opencv-headless, scipy). No GPU needed; CPU whisper `small` runs ~0.6x realtime on 8 cores.

## Pipeline (in order)

1. **Download**: `~/.local/bin/yt-dlp -f "bv*[height<=720]+ba/b[height<=720]" --merge-output-format mp4 -o "/tmp/podcast-clips/<episode-id>/source.%(ext)s" <url>`. If a video file already exists (e.g., downloaded via youtube-content skill), place it in the isolated temp workspace as `source.mp4` and skip this step.

   **Episode dir naming**: Use `<episode-id>` as the directory name. For episodes with a visible YouTube publish date, prefix with `YYYY-MM-DD_` (e.g., `2026-09-19_chaos-behind-purbaya-removal`). For episodes without a date, use the slug only (e.g., `bocor-alus-jokow-prabowo-2029-scenarios`). Podcast slug in deploy path: `jelasin-dong` | `bocor-alus` | `tukang-kupas`.

   **Known video IDs** (verify before searching):
   - Bocor Alus #4 (Prabowo-Gibran 2029): `rLfVk3ZN7S4` — "Separah Apa Hubungan Buruk Prabowo-Gibran | Bocor Alus Politik"
   - Bocor Alus #2 (Revisi UU Pemilu): `manuver-partai-koalisi-prabowo-revisi-uu-pemilu` (already deployed)
   - Jelasin Dong (Purbaya): `0acXb24X91s` — "The Chaos Behind Minister Purbayas Removal"
2. **Transcribe**: Two options —  
   - **faster-whisper** (offline, 15+ min for long videos): run with `background=true, notify=true`, model `small`, `int8`, `language="id"`, `vad_filter=True` → `transcript.json` (`[{start,end,text}]`).  
   - **YouTube API transcript** (instant, requires subtitles available): the script prints to **stdout**, not to a file. Two-step approach (env vars don't propagate through shell pipes):
     ```
     /home/isra/podcast-clips/.venv/bin/python /home/isra/.hermes/skills/media/youtube-content/scripts/fetch_transcript.py <URL> --timestamps --text-only --language id,en > /tmp/podcast-clips/<episode-id>/transcript_raw.txt
     PODCAST_TRANSCRIPT_FILE=/tmp/podcast-clips/<episode-id>/transcript.json /home/isra/podcast-clips/.venv/bin/python scripts/youtube_transcript_to_segments.py < /tmp/podcast-clips/<episode-id>/transcript_raw.txt
     ```
     Verify segment count and first-line text match the episode before proceeding with curate.
3. **Curate**: `PODCAST_WORK_DIR=/tmp/podcast-clips/<episode-id> .venv/bin/python curate.py small <podcast_slug> "<episode_title>"` — one LLM call returns episode summary + X post + 6-12 clip moments → `episode_data.json` + `clips.json`.
   - The `curate.py` script reads `HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY` from `~/.hermes/.env` automatically. Do NOT pass the key explicitly in the command — the environment's own key (read via `printenv HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY`) is the correct one; passing a wrong explicit value causes `HTTP 401 Unauthorized`.
   - `podcast_slug`: jelasin-dong | bocor-alus | tukang-kupas
   - `episode_title`: original episode title (auto-slugified for URL)
   - Output: `episode_summary` (web), `x_post` {text, hashtags} for X hook + link to web, `clips` array.
4. **Workspace reuse vs clean start**: 
   - **Fresh start (video never downloaded)**: `run_one_episode.py` creates its own timestamped dir — clean first is fine.
   - **Admin-panel retry after partial failure**: `run_one_episode.py` IGNORES `PODCAST_WORK_DIR` env var — it always creates a new `<video_id>-<fresh_timestamp>` directory. If the source video was already downloaded to a specific timestamp dir (e.g., `1790660368`), manually chain steps against that exact dir instead:
     ```
     # 1. Transcribe (reuse downloaded video)
     PODCAST_WORK_DIR=$WDIR PODCAST_TRANSCRIPT_FILE=$WDIR/transcript.json \
       .venv/bin/python -c "
       from faster_whisper import WhisperModel; from pathlib import Path; import json
       model = WhisperModel('small', device='cpu', compute_type='int8')
       segments, _ = model.transcribe('$WDIR/source.mp4', language='id', vad_filter=True)
       out = [{'start': float(s.start), 'end': float(s.end), 'text': s.text.strip()} for s in segments if s.text.strip()]
       Path('$WDIR/transcript.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))
       "
     # 2. Curate
     PODCAST_WORK_DIR=$WDIR .venv/bin/python curate.py MiniMax-M2.7-highspeed <podcast_slug> "<title>"
     # 3. search_results.json then caption
     # 4. Cut (ffmpeg/ffprobe absolute paths already patched in cut_smart.py)
     PODCAST_WORK_DIR=$WDIR .venv/bin/python cut_smart.py $WDIR/source.mp4
     ```
   - **Never clean a workspace that has a valid `source.mp4`** — deleting it forces a re-download which risks YouTube 403 rate limits. Verify with `ls /tmp/podcast-clips/<id>-*/source.mp4` before starting.
   - Old test episodes (ep1, ai-tutorial, etc.) left in the workspace produce wrong captions on subsequent runs — verify with `ls /tmp/podcast-clips/` and `ls app/static/clips/` before starting.

5. **Cut**: `PODCAST_WORK_DIR=/tmp/podcast-clips/<episode-id> .venv/bin/python cut_smart.py /tmp/podcast-clips/<episode-id>/source.mp4` — face-tracked 9:16 crop + burned-in ASS subtitles → `clips/clipNN.mp4`. Reads `clips.json` from `PODCAST_WORK_DIR`.

6. **Caption**: Before running, create `search_results.json` — do a `web_search` for each clip's topic (title/hook), take top 3 results per clip, write as `{"1": [{"title": ..., "url": ...}, ...], "2": [...], ...}`. **Must be a dict keyed by clip number as string** (e.g. `"1"`, `"2"`). A flat list `[{...}, {...}]` will cause `AttributeError: 'list' object has no attribute 'get'` in caption.py line 56. Select reputable Indonesian media (Tempo, Kompas, CNN Indonesia, MetroTV, IDN Times, RMOL, tribun) — user rejects generic analysis sites (windonesia.com, cockatoo.com, suarakita.net). Then run `PODCAST_WORK_DIR=/tmp/podcast-clips/<episode-id> .venv/bin/python caption.py` — LLM writes TikTok hook + hashtags only (prompt says "JANGAN sertakan link apapun"), then **programmatically force-appends all 3 news links** after the LLM call as raw `\n{url}` lines — no header text, no emoji, no title prefix. This bypasses the LLM's unreliable link inclusion entirely and avoids truncation from header text eating character budget.
   - The `caption.py` script reads `HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY` from `~/.hermes/.env` automatically. Do NOT pass the key explicitly.

7. **Deliver video + caption TOGETHER, every time**: the user requires the video file (copy to `~/.hermes/cache/scratch/`, then a `MEDIA:/path` line) AND its caption with all 3 related-news links in the SAME chat message.

8. **Deploy to web gallery**: Clips are served via Flask at `~/podcast-clips/app/`. 

   **Folder structure** (per-podcast, per-episode — the user requires this):
   ```
   static/clips/
   └── <podcast-name>/                 # e.g., jelasin-dong, bocor-alus, tukang-kupas
       └── <YYYY-MM-DD_episode-slug>/   # e.g., 2026-09-19_huru-hara-pencopotan-purbaya
           ├── clip01.mp4 ... clip06.mp4
           ├── captions.json
           ├── clips.json
           └── episode_data.json         # summary + x_post + clips metadata
   ```
   Episode folder name = date (from RSS `published` field) + `_` + slugified title. Always create the folder before copying files.

   Copy pipeline output (all 4 files required — missing any one causes 404 on episode page):
   ```bash
   PODCAST=jelasin-dong
   EP_DIR="2026-09-19_episode-slug"   # or bare slug if no date (e.g. bocor-alus-jokow-prabowo-2029-scenarios)
   mkdir -p ~/podcast-clips/app/static/clips/$PODCAST/$EP_DIR
   cp /tmp/podcast-clips/$EPISODE/clips/clip*.mp4 ~/podcast-clips/app/static/clips/$PODCAST/$EP_DIR/
   cp /tmp/podcast-clips/$EPISODE/captions.json ~/podcast-clips/app/static/clips/$PODCAST/$EP_DIR/
   cp /tmp/podcast-clips/$EPISODE/clips.json ~/podcast-clips/app/static/clips/$PODCAST/$EP_DIR/   # REQUIRED — clips.py checks this to render page
   cp /tmp/podcast-clips/$EPISODE/episode_data.json ~/podcast-clips/app/static/clips/$PODCAST/$EP_DIR/
   ```
   - `captions.json` + `clips.json` + `episode_data.json` + at least one `clip*.mp4` must all be present — route returns 404 if any is missing.
   - `clips.json` is written by curate.py to `PODCAST_WORK_DIR/`, not to the clips/ subdirectory. Make sure to copy from `$EPISODE/` root, not `$EPISODE/clips/`.
   - Flask does NOT need to be restarted for new static files — only for route logic changes. A restart IS needed after adding new JSON files that route handlers read at startup (e.g. `clips.json`). Static file changes (new clip MP4s) are picked up immediately.
   - **Episode URL: no trailing slash** — `/clips/bocor-alus/bocor-alus-jokow-prabowo-2029-scenarios` (200) ≠ `/clips/bocor-alus/bocor-alus-jokow-prabowo-2029-scenarios/` (404). The `clips.html` template links use the no-slash form.

   Restart: check if port 5000 is already in use (`curl -s -o /dev/null -w '%{http_code}' http://localhost:5000/` returns 200 = already running). If not, start with: `cd ~/podcast-clips/app && /home/isra/podcast-clips/.venv/bin/python run.py` (NOT `flask run` — that fails with "Could not locate Flask application").
   
   Restart IS needed when: route logic changes, or new JSON files that route handlers read at startup (e.g. `clips.json`, `captions.json`). Restart is NOT needed for new static clip MP4s — Flask picks those up immediately.
   
   Restart procedure: `kill <PID>` (check via `ps aux | grep "python run.py"`) then start in background. "no job control" bash warning is harmless — the process starts normally. Verify: `curl http://localhost:5000/` returns 200.

   **CRITICAL — systemd service config**: The service file MUST load `.env` via `EnvironmentFile=` and use the correct venv path. Without `EnvironmentFile`, YouTube/TikTok credentials are empty strings at runtime even if `.env` exists on disk — OAuth flows fail with `missing required parameter: client_id`. Service file must have:
   ```
   EnvironmentFile=/home/isra/podcast-clips/app/.env
   Environment="PATH=/home/isra/podcast-clips/.venv/bin"
   ExecStart=/home/isra/podcast-clips/.venv/bin/python run.py
   ```
   Never point `ExecStart` or `PATH` at `~/Documents/project-test/venv/` or any other app's venv.

   **Route layout**: `/` renders an index page listing all episodes grouped by podcast. `/clips/<podcast>/<episode>` renders the Shorts-style gallery with an episode landing card (episode_summary + x_post) at the top before the clips scroll. Video files are served via `/static/clips/<podcast>/<episode>/<filename>` (a dedicated `send_from_directory` route so Flask finds them in nested dirs). The clips.html template receives `podcast`, `episode`, `episode_title`, `episode_summary`, `x_post`, `captions`, and `clips_meta` variables and constructs video src as `/static/clips/{{ podcast }}/{{ episode }}/{{ filename }}`.

   **App package structure** (Flask factory pattern):
   `app/__init__.py` (Flask init + ProxyFix), `app/run.py` (entry point), `app/config.py` (API credentials), `app/routes/clips.py` (gallery + terms/privacy), `app/routes/youtube.py` (YouTube OAuth), `app/routes/tiktok.py` (TikTok OAuth), `app/templates/clips.html` (Shorts viewer), `app/templates/index.html` (episode list).

   **Systemd**: Update `~/.config/systemd/user/flask-app.service` WorkingDirectory to `~/podcast-clips/app` and ExecStart to `python run.py`. Run `systemctl --user daemon-reload && systemctl --user restart flask-app` after any change.

8. **Automated cronjob** (when the user requests it): Set up a cronjob that monitors Tempo podcast playlists (Jelasin Dong!, Bocor Alus, Tukang Kupas Perkara) for new episodes. Playlist IDs:
   - `jelasin-dong`: `PLkiSnq8pdz9TC0rHIiguTsgelilKeR3pw`
   - `bocor-alus`: `PLkiSnq8pdz9T3QQVbczz4XVgsHjjJK3vd`
   - `tukang-kupas`: `PLkiSnq8pdz9SBWNzd65VKlI4uzLv4_p41`

   The monitor script (`~/.hermes/scripts/jelasin_monitor.py`, referenced as just `jelasin_monitor.py`) fetches RSS for all playlists via `https://www.youtube.com/feeds/videos.xml?playlist_id=<ID>`, finds entries not in a `processed.txt` state file, prints `NEW:<show>:<video_id>:<title>` for each new one, and appends IDs to the state file. Returns `NO_NEW` when nothing changed.
   
   **Cronjob monitor constraint**: scripts must be placed in `~/.hermes/scripts/` and referenced by **filename only** (not absolute path). The cronjob tool rejects absolute paths for `monitor` and `script` parameters.

   Register via `cronjob_manage action=create` with `monitor=jelasin_monitor.py`, `skills=["podcast-clipping", "youtube-content"]`, `workdir=/home/isra/podcast-clips`, `schedule="every day at 9am"`, `deliver="origin"`. The monitor gates the agent: unchanged output skips the run; a different hash triggers the pipeline on the newest episode. The prompt must instruct the agent to create a per-podcast folder in `static/clips/` and copy clips + JSON there, then restart Flask. End with "Kirim 1 sample klip + caption ke user untuk review. Jangan batch-post sebelum disetujui."

10. **YouTube upload** (after user authorizes OAuth): Upload finished clips to YouTube Shorts.
   - **Concurrent-run prevention**: NEVER run upload scripts in both foreground and background simultaneously. Always check `ps aux | grep upload_bocor\|upload_go` before starting an upload. Running two upload batches concurrently produces duplicate videos on YouTube. If duplicates exist, delete one set from YouTube Studio — deletion propagates asynchronously and recently-deleted videos may return 404 for a few minutes.
   - **Upload script must target correct WORK_DIR**: The `upload_bocor.py` script hardcodes `lslu0` clips path. When processing a different episode (e.g. `Go_ZovP5Hd0`), write a fresh episode-specific script (`upload_<slug>.py`) pointing to the correct `CLIPS_DIR`. Verify clip filenames in the script match the current episode before running.
   - **YouTube upload quota**: ~10-20 uploads/day. When `uploadLimitExceeded` or `ResumableUploadError` occurs, do NOT retry immediately — quota resets in ~24h from last upload. Set a cron for ~6 AM the next day to retry.
   - **YouTube playlist per podcast**: After uploading all clips for an episode, add all new video IDs to the playlist. Always include source YouTube link in every Short description: `\nhttps://www.youtube.com/watch?v=<source_video_id>`. Playlist IDs: Bocor Alus `PLbiJk6w7ZkDM`, Jelasin Dong `PLDRYqNe_mO4w`.
   - The upload script reads `HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY` from `~/.hermes/.env` automatically. Do NOT pass the key explicitly.
   - OAuth: Google Cloud Console → YouTube Data API v3 → OAuth client ID (Web application) with redirect URI `https://clips.gcp.my.id/oauth`.
   - **Scope**: Use `['https://www.googleapis.com/auth/youtube']` (full scope) NOT just `youtube.upload`. The upload-only scope cannot update video metadata (title, description) after upload. With full scope, one OAuth covers both upload and later metadata updates.
   - **OAuth test-user gate**: If the Google OAuth app is in "Testing" mode (not "Published"), only explicitly-added test users can authorize. Regular users get `403 access_denied`. Fix: in Google Cloud Console → APIs & Services → OAuth consent screen → Test users → add the user's email. Publishing the app removes this restriction but requires Google verification (1-7 days).
   - **OAuth token refresh with ISO expiry format**: After refreshing the YouTube token, write the new `expiry` as `datetime.now(timezone.utc) + timedelta(seconds=new_tok["expires_in"])` then `.strftime("%Y-%m-%dT%H:%M:%SZ")`. The `Credentials` class expects `expiry` as an ISO timestamp string with timezone — a naive datetime or integer causes `invalid_grant` on every subsequent API call. The `google_auth_oauthlib` `Flow` object generates a code verifier at creation time, which is held in memory. Since `/auth` and `/oauth` are separate Flask routes, the state **must persist in session** across the redirect round-trip: save both `flow.authorization_url()`'s returned `state` AND `flow.code_verifier` to Flask `session` in `/auth`; restore both when reconstructing `Flow.from_client_config(...)` in `/oauth`. Without the code_verifier, the callback errors `InvalidGrantError: Missing code verifier`.
     - Behind Cloudflare tunnel, Flask sees HTTP even though the external request is HTTPS. OAuthlib rejects `request.url` as insecure. Fix: apply `werkzeug.middleware.proxy_fix.ProxyFix(app.wsgi_app, x_proto=1, x_host=1)` so Flask reads the `X-Forwarded-Proto` header from the tunnel and constructs `https://` URLs.
     - Set `app.secret_key` (e.g. `os.urandom(24).hex()`) — Flask sessions require it.
     - After changing scope or deleting token, the user must re-authorize at `https://clips.gcp.my.id/auth`.
   - **Deleting the token file is enough to switch accounts.** Cloud Console credentials (client ID/secret) belong to the app, not an account — every Google account can authorize against them. `rm ~/podcast-clips/app/youtube_token.json` then re-visit `/auth` and pick the account in the consent screen.
   - Flask app routes: `/auth` → Google consent screen, `/oauth` → callback saves token to `youtube_token.json`.
   - **YouTube Shorts format**: Append `#Shorts` to every video title so YouTube classifies the vertical 9:16 clip as a Short. Also add `shorts` to the tags array.
   - **Caption whitespace**: Strip leading \n\n from captions before uploading (`description.strip()`) — the caption.py script prefixes them and raw newlines make the YouTube description start with blank lines.
   - **Update descriptions after upload (MANDATORY)**: After each upload, call `youtube.videos().update()` to write the full description with all 3 news links. The `videos().insert()` call during upload does not reliably embed all metadata. User explicitly rejects descriptions with only 1 link as "tidak lengkap". Always update YouTube descriptions to match the latest `captions.json` — they do not auto-sync.
- Upload script at `~/podcast-clips/scripts/youtube_upload.py`: `python scripts/youtube_upload.py <num>` uploads one clip; `python scripts/youtube_upload.py all` uploads all 6. Reads `captions.json` from `WORK_DIR` root (not `WORK_DIR/clips/` — that was a past bug).
   - If token missing or scope insufficient, user opens `https://clips.gcp.my.id/auth` to re-authorize.
   - **Channel mismatch pitfall**: Google accounts can have multiple YouTube channels (main + brand channels). When the user authorizes OAuth in the consent screen, Google shows a channel picker. If they select a different channel than the one used for a previous upload, the new token points to a different channel ID. Uploads still succeed (to the new channel), but metadata updates on old videos fail with `Forbidden`. The fix: delete old videos and re-upload under the correct channel, or re-authorize explicitly selecting the right channel. To verify which channel the current token points to: `youtube.channels().list(part='snippet', mine=True).execute()` — check the returned `channelId`.
   - Credentials reference at `~/.hermes/references/youtube-api-credentials.md`.

11. **TikTok upload** (after user's TikTok developer app is approved + OAuth authorized): Upload finished clips to TikTok inbox for user review before posting.
   - **Current status** (as of Sep 2026): App registered, domain verified (TXT DNS record), OAuth code in place. App review submission pending — user must check https://developers.tiktok.com for approval status before OAuth can proceed.
   - **Prerequisites**: TikTok Business account, developer app registered at https://developers.tiktok.com, domain verified (TXT DNS record `tiktok-developers-site-verification=<value>` added in Cloudflare DNS), app submitted for review.
   - **OAuth**: App uses PKCE OAuth 2.0. The client key and secret are read from env vars (`TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`) via `app/config.py` — never hardcoded, never committed. Routes at `/tiktok-auth` and `/tiktok-oauth`. Redirect URI: `https://clips.gcp.my.id/tiktok-oauth`. Scope: `video.upload`.
   - **Upload script** at `~/podcast-clips/scripts/tiktok_upload.py`: uses the Content Posting API v2. Flow: initialize upload → PUT file to upload_url → POST to inbox. Videos go to `PUBLISH_TO_INBOX` mode — the user must open the TikTok app, check Inbox, and manually post.
   - **Usage**: `python scripts/tiktok_upload.py <num>` for one clip, `python scripts/tiktok_upload.py all` for all 6.
   - **Token storage**: `~/podcast-clips/app/tiktok_token.json` after successful OAuth.
   - **Domain verification for TikTok**: TikTok requires verifying ownership of the URL prefix (`clips.gcp.my.id`) before the Content Posting API works. Two ways:
     a) **DNS TXT record** (recommended): Add a TXT record for `clips.gcp.my.id` in Cloudflare DNS with value `tiktok-developers-site-verification=<string_from_tiktok>`. Verify with `dig TXT clips.gcp.my.id +short`.
     b) **File upload**: Place the verification `.txt` file at the web root (served by Flask static or route).

## cut_smart.py — absolute ffmpeg path required

`cut_smart.py` calls `subprocess.run(["ffmpeg", ...])` and `subprocess.run(["ffprobe", ...])` with **bare executable names**. The Flask systemd service (`flask-app`) runs with a restricted PATH that excludes `/usr/bin`, so these calls fail with `FileNotFoundError` at runtime even though `ffmpeg` exists on the system.

**Fix**: At the top of `cut_smart.py`, define constants:
```python
FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"
```
Then replace all bare `["ffmpeg"` and `["ffprobe"` with `[FFMPEG` and `[FFPROBE`. This was the root cause of "failed" clip status in the admin panel — not a YouTube download issue, not a transcription issue.

**Worker wrapper (`process_episode.py`) must set `PODCAST_WORK_DIR`**: The wrapper script creates a timestamped work dir but only passes it via `PODCAST_WORK_DIR` env var to `run_one_episode.py`. Without this env var, `run_one_episode.py` creates its own timestamped dir — and if the Flask admin panel's submission record points to a *different* timestamp dir, the worker finds nothing to process and exits 0 without doing work (silent no-op). Always verify the `PODCAST_WORK_DIR` timestamp matches the admin DB record, or set it explicitly in the wrapper.

- **YouTube 403 on download**: Rate limiting from the same IP with repeated downloads. The download still works (often on retry after 60s) — the 403 is transient. Before creating a new timestamped work dir, check if the video was already downloaded to an existing dir: `ls /tmp/podcast-clips/<id>-*/source.mp4`. Reusing an existing downloaded file avoids triggering 403 again. If a new timestamp dir is forced (by `run_one_episode.py`'s fresh-dir behavior), the download may hit 403 — retry with backoff.

## Hard rules

- **Credentials must use os.getenv(), never hardcoded values** in `app/config.py`. YouTube client ID/secret, TikTok client key/secret, and SumoPod API keys must come from environment variables or `.env` files (already in .gitignore). The `.gitignore` must exclude `*_token.json`, `credentials/`, `.env`, and app-specific files like `app/youtube_token.json`. Verify with `git grep` before committing — any 30+ char alphanumeric string could be a secret. Before making this repo public, run the full audit (tracked files AND `git log -p --all` history) per the `repo-secret-audit` skill — a key deleted in a later commit is still readable in history. Note that docs describing where credentials live drift: `app/config.py` reads every value from `os.getenv()` with `""` defaults, so any doc claiming secrets are "in app/config.py" is stale and must be corrected, not trusted.

- **Wrong deploy path pitfall**: Two Flask apps exist — `~/podcast-clips/app/` (the pipeline's Flask app, served at clips.gcp.my.id) and `~/Documents/project-test/` (a separate project). Deploy targets ONLY `~/podcast-clips/app/static/clips/`. Copying to `~/Documents/project-test/static/clips/` creates files that the pipeline's Flask never reads.
- **Never reuse a `transcript.json` or `search_results.json` across episodes.** Both files are shared scratch names. After any re-transcribe and re-search, verify segment count and first-line text before running curate/caption. Leftover search data or a previous episode's transcript produces clips whose subtitles and captions describe a different video — this has happened and shipped to the user.
- **SKILL.md is the source of truth, not PIPELINE.md.** When in doubt, SKILL.md has the correct commands and conventions. PIPELINE.md in the repo may lag behind — always cross-reference SKILL.md. The skill is loaded by Hermes on every relevant session; PIPELINE.md is a repo file an agent might read without the skill context.
- **Never deploy test/non-Tempo episodes to the production web gallery.** The pipeline is for Tempo podcasts (jelasin-dong, bocor-alus, tukang-kupas) only. A test run on a random video (e.g. ai-tutorial, a standalone tutorial) should remain in the isolated temp workspace — do not copy to `app/static/clips/` or restart Flask. Only deploy episodes the user has approved after reviewing the sample clip. The `work/` directories are local archives only; never reference them as deploy targets.
- **Long transcribes must run with `background=true, notify=true`.** A 28-min video takes ~25 min on CPU; foreground calls hit the 600s cap. A 43-min video can take 45-60 min — the process is healthy (300%+ CPU, 18 threads, stable memory) as long as it stays alive. Do not kill a transcribe that is consuming CPU and memory even if it has been running "too long" — check for actual I/O deadlock first.
- **Update search_results.json BEFORE running caption.py in background.** If you update search_results.json after starting a background caption process, the running process will use the stale data. The caption process reads search_results.json at startup — any writes after that are invisible until re-run.
- **YouTube descriptions must match captions.json after re-generation.** If captions are re-generated (e.g. to fix links), update all 6 YouTube video descriptions immediately via `youtube.videos().update()`. YouTube descriptions do not auto-sync from `captions.json` — they are written once during upload and remain stale until explicitly updated.
- **Deliver ONE sample video + its caption to the user for review before batch-posting anything.** LLM caption quality is ~70% — the user reviews and corrects topic errors; captions are the step that fails, video rarely.
- **Caption news links must be from reputable Indonesian media** (Tempo, Kompas, CNN Indonesia, MetroTV, IDN Times, RMOL, tribun). User rejects generic analysis sites (windonesia.com, cockatoo.com, suarakita.net) as irrelevant. Filter search_results.json to only include links from trusted sources before running caption.py.
- **Run with PODCAST_WORK_DIR set, always.** Every script reads `clips.json`, `transcript.json`, and `search_results.json` from `$PODCAST_WORK_DIR` — not from a `work/` subdirectory. Always set `PODCAST_WORK_DIR=/tmp/podcast-clips/<episode-id>` before running curate.py, caption.py, cut_smart.py, and upload scripts. Running from any other directory or forgetting the env var causes stale file reads from a previous episode.
- **Caption format: raw URLs only, no header/emoji/title**. The user explicitly rejected "📰 Baca selengkapnya:" headers, emoji prefixes, and title lines before URLs. The force-append must add only `\n{url}` per link — no descriptive text, no section headers. Header text eats character budget and pushes the actual URLs below the YouTube description fold, making them appear "truncated".
- **Caption must be generated from the transcript of the exact clip being posted** — transcribe the user-sent video file directly when verifying a caption, do not reason from stale state.
- **yt-dlp is a uv tool** (`uv tool install yt-dlp`), not on PATH via pip.

## Pitfalls baked into scripts (do not regress)

- **AV1 decode**: OpenCV `VideoCapture` cannot decode AV1 (YouTube's default 720p codec) — extract sample frames via ffmpeg (`fps=...,scale=320:-2`) then `cv2.imread` them. Symptom if regressed: 0 face samples, silent fallback to center crop.
- **ASS subtitle header**: ffmpeg's libass requires `[V4+ Styles]` with the full 23-field Format line and `ScriptType: v4.00+`. A short `[V4 Styles]` header parses as garbage — raw format-code text gets burned into the video instead of subtitles.
- **Crop smoothing**: face-track x-positions need median-filter (window 5) → CubicSpline interpolation → max speed clamp (250 px/s) → moving average. Plain linear interp between samples makes the crop jitter/jump; users notice immediately.
- **Crop smoothing (Sep 2026 updates)**: 48 face samples per clip (was 24), interpolation grid 90 steps (was 60), max velocity 180 px/s (was 250), double-pass smoothing (median filter → velocity clamp → rolling average). Result: clips with 250-314px total face shift look smooth without jump cuts.
  - Recipe: median 5-point → CubicSpline interp → velocity clamp 180px/s → moving average (k=5).
- **Subtitles**: split transcript into ≤4-word chunks timed proportionally within each segment (TikTok style); style 72pt white + 5px black outline, MarginV 300, PlayRes 1080x1920.
- **Video muted by default**: Web browsers block autoplay with sound. Starting a `<video>` with `muted` attribute kills audio entirely and users miss it. Keep `muted` for autoplay compliance, but provide a visible mute/unmute toggle button (bottom-right overlay, `.mute-btn` with speaker icons). Toggle `vid.muted` on click and call `vid.play()` as a user-gesture path to unmuted playback.

## Web gallery: clips.html template

When serving clips via Flask/Bootstrap:
- **Layout**: Episode landing card (episode_summary + x_post) as the first snap item (100dvh), followed by YouTube Shorts-style vertical clip scroll. Progress dots on the right side (`position: fixed`) link to each clip index.
- **Episode header**: podcast badge, episode title, summary paragraph, X post preview (text + web URL), and "Tonton Klip" CTA button that scrolls to the first clip.
- **Auto-play on scroll**: detect the active item by checking each `.short-item`'s bounding rect midpoint against viewport height. Play the active video, pause all others.
- **Audio**: browsers block autoplay with sound. Start videos **muted** (HTML `muted` attribute or JS `vid.muted = true`). Provide a mute/unmute toggle button (`.mute-btn` positioned bottom-right of the video wrapper, icon `bi-volume-mute-fill` / `bi-volume-up-fill`). Unmute triggers `vid.play()` as a user-gesture path to unmuted playback.
- **Never truncate captions** — the news URL at the end (30-60 chars) gets cut off first.
- **Auto-link URLs** inside captions — split on `https://`, wrap in `<a>` with `target="_blank"`. Use a short label like `🔗 baca selengkapnya` instead of dumping the raw URL.
- Use `static/clips/` to serve video files + `clips.json` + `captions.json` + `episode_data.json`.
- Route template reads `captions.json`, `clips.json`, and `episode_data.json` from the clips dir.

## Promo copy (X thread / Telegram)

When the user asks for promo text for this pipeline:

- **Lead with the input the pipeline actually takes: a YouTube link.** Do not frame RSS monitoring as the user-facing entry point — the user rejected that framing ("kita kan bisa kasih link YouTube nya juga"). RSS monitoring is an internal scheduling detail; the story is "kasih link YouTube → 6 Shorts + web gallery".
- Include the concrete numbers, since they are the selling point: ~$0.10 / ~Rp 1.800 per episode for 6 Shorts, model `MiniMax-M2.7-highspeed` via SumoPod.
- Link set: live gallery (`clips.gcp.my.id`), repo, blog writeup. The X version is a ~5-post thread; the Telegram version is ONE message, not a thread.
- Cost figures to cite (MiniMax-M2.7-highspeed, 1 episode / 6 clips): ~3.35M input + ~30K output tokens ≈ $0.104 ≈ Rp 1.797.

## Scripts

Working reference implementations live in `~/podcast-clips/` (curate.py, cut_smart.py, caption.py) — these are the canonical working copies and are updated when the pipeline changes.
