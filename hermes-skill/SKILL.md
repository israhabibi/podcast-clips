---
name: podcast-clipping
description: Use when clipping a podcast video into TikTok/Shorts clips and publishing to YouTube, web, or Telegram.
---

# Podcast Clipping Pipeline

Automated pipeline on this machine, all in `~/podcast-clips/` (venv at `.venv` with faster-whisper, opencv-headless, scipy). No GPU needed; CPU whisper `small` runs ~0.6x realtime on 8 cores.

## Pipeline (in order)

1. **Download**: `~/.local/bin/yt-dlp -f "bv*[height<=720]+ba/b[height<=720]" --merge-output-format mp4 -o "/tmp/podcast-clips/<episode-id>/source.%(ext)s" <url>`. If a video file already exists (e.g., downloaded via youtube-content skill), place it in the isolated temp workspace as `source.mp4` and skip this step.
2. **Transcribe**: Two options —  
   - **faster-whisper** (offline, 15+ min for long videos): run with `background=true, notify=true`, model `small`, `int8`, `language="id"`, `vad_filter=True` → `transcript.json` (`[{start,end,text}]`).  
   - **YouTube API transcript** (instant, requires subtitles available): use the youtube-content skill's `fetch_transcript.py --timestamps --text-only --language id,en` for the text, pipe into the conversion script:
     ```
     uv run python /home/isra/.hermes/skills/media/youtube-content/scripts/fetch_transcript.py <URL> --timestamps --text-only --language id,en | uv run python scripts/youtube_transcript_to_segments.py
     ```
     The script parses `MM:SS text` lines, merges same-timestamp utterances, and estimates end times from the next line's start. Verify segment count and first-line text match the episode before proceeding with curate.
3. **Curate**: `curate.py <model> <podcast_slug> <episode_title>` — one LLM call returns episode summary + X post + 6-12 clip moments → `episode_data.json` + `clips.json`.
   - `podcast_slug`: jelasin-dong | bocor-alus | tukang-kupas
   - `episode_title`: original episode title (auto-slugified for URL)
   - Output: `episode_summary` (web), `x_post` {text, hashtags} for X hook + link to web, `clips` array.
4. **Cut**: `PODCAST_WORK_DIR=/tmp/podcast-clips/<episode-id> .venv/bin/python cut_smart.py /tmp/podcast-clips/<episode-id>/source.mp4` — face-tracked 9:16 crop + burned-in ASS subtitles → `clips/clipNN.mp4`. Reads `clips.json` from `PODCAST_WORK_DIR`.
5. **Caption**: Before running, create `search_results.json` — do a `web_search` for each clip's topic (title/hook), take top 3 results per clip, write as `{"1": [{"title": ..., "url": ...}, ...], ...}`. Pilih sumber berita dari **media Indonesia bereputasi** (Tempo, Kompas, CNN Indonesia, MetroTV, IDN Times, RMOL, tribun) — jangan pakai situs analisis generik (windonesia.com, cockatoo.com, suarakita.net) karena user tolak itu. Kemudian jalankan `PODCAST_WORK_DIR=/tmp/podcast-clips/<episode-id> python caption.py` → LLM writes TikTok hook + hashtags only (prompt says "JANGAN sertakan link apapun"), then **programmatically force-appends all 3 news links** after the LLM call as raw `\n{url}` lines — no header text, no emoji, no title prefix. This bypasses the LLM's unreliable link inclusion entirely and avoids truncation from header text eating character budget.

6. **Deliver video + caption TOGETHER, every time**: the user requires the video file (copy to `~/.hermes/cache/scratch/`, then a `MEDIA:/path` line) AND its caption with all 3 related-news links in the SAME chat message.

7. **Deploy to web gallery**: Clips are served via Flask at `~/podcast-clips/app/`. 

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

   Copy pipeline output:
   ```bash
   PODCAST=jelasin-dong
   EP_DIR="2026-09-19_episode-slug"
   mkdir -p ~/podcast-clips/app/static/clips/$PODCAST/$EP_DIR
   cp /tmp/podcast-clips/$EPISODE/clips/clip*.mp4 ~/podcast-clips/app/static/clips/$PODCAST/$EP_DIR/
   cp /tmp/podcast-clips/$EPISODE/clips.json /tmp/podcast-clips/$EPISODE/captions.json /tmp/podcast-clips/$EPISODE/episode_data.json ~/podcast-clips/app/static/clips/$PODCAST/$EP_DIR/
   ```

   Restart: `systemctl --user daemon-reload && systemctl --user restart flask-app`. Verify: `curl http://localhost:5000/` (index with episode list) and `curl http://localhost:5000/clips/$PODCAST/$EP_DIR` (Shorts viewer).

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

9. **YouTube upload** (after user authorizes OAuth): Upload finished clips to YouTube Shorts.
   - OAuth: Google Cloud Console → YouTube Data API v3 → OAuth client ID (Web application) with redirect URI `https://clips.gcp.my.id/oauth`.
   - **Scope**: Use `['https://www.googleapis.com/auth/youtube']` (full scope) NOT just `youtube.upload`. The upload-only scope cannot update video metadata (title, description) after upload. With full scope, one OAuth covers both upload and later metadata updates.
   - **OAuth pitfalls**:
     - Google now requires PKCE for all OAuth flows. The `google_auth_oauthlib` `Flow` object generates a code verifier at creation time, which is held in memory. Since `/auth` and `/oauth` are separate Flask routes, the state **must persist in session** across the redirect round-trip: save both `flow.authorization_url()`'s returned `state` AND `flow.code_verifier` to Flask `session` in `/auth`; restore both when reconstructing `Flow.from_client_config(...)` in `/oauth`. Without the code_verifier, the callback errors `InvalidGrantError: Missing code verifier`.
     - Behind Cloudflare tunnel, Flask sees HTTP even though the external request is HTTPS. OAuthlib rejects `request.url` as insecure. Fix: apply `werkzeug.middleware.proxy_fix.ProxyFix(app.wsgi_app, x_proto=1, x_host=1)` so Flask reads the `X-Forwarded-Proto` header from the tunnel and constructs `https://` URLs.
     - Set `app.secret_key` (e.g. `os.urandom(24).hex()`) — Flask sessions require it.
     - After changing scope or deleting token, the user must re-authorize at `https://clips.gcp.my.id/auth`.
   - Flask app routes: `/auth` → Google consent screen, `/oauth` → callback saves token to `youtube_token.json`.
   - **YouTube Shorts format**: Append `#Shorts` to every video title so YouTube classifies the vertical 9:16 clip as a Short. Also add `shorts` to the tags array.
   - **Caption whitespace**: Strip leading \n\n from captions before uploading (`description.strip()`) — the caption.py script prefixes them and raw newlines make the YouTube description start with blank lines.
   - **Update descriptions after upload (MANDATORY)**: After each upload, call `youtube.videos().update()` to write the full description with all 3 news links. The `videos().insert()` call during upload does not reliably embed all metadata. User explicitly rejects descriptions with only 1 link as "tidak lengkap". Always update YouTube descriptions to match the latest `captions.json` — they do not auto-sync.
   - Upload script at `~/podcast-clips/scripts/youtube_upload.py`: `python scripts/youtube_upload.py <num>` uploads one clip; `python scripts/youtube_upload.py all` uploads all 6.
   - If token missing or scope insufficient, user opens `https://clips.gcp.my.id/auth` to re-authorize.
   - **Channel mismatch pitfall**: Google accounts can have multiple YouTube channels (main + brand channels). When the user authorizes OAuth in the consent screen, Google shows a channel picker. If they select a different channel than the one used for a previous upload, the new token points to a different channel ID. Uploads still succeed (to the new channel), but metadata updates on old videos fail with `Forbidden`. The fix: delete old videos and re-upload under the correct channel, or re-authorize explicitly selecting the right channel. To verify which channel the current token points to: `youtube.channels().list(part='snippet', mine=True).execute()` — check the returned `channelId`.
   - Credentials reference at `~/.hermes/references/youtube-api-credentials.md`.

10. **TikTok upload** (after user's TikTok developer app is approved + OAuth authorized): Upload finished clips to TikTok inbox for user review before posting.
   - **Prerequisites**: TikTok Business account, developer app registered at https://developers.tiktok.com, domain verified (TXT DNS record `tiktok-developers-site-verification=<value>` added in Cloudflare DNS), app submitted for review.
   - **OAuth**: App uses PKCE OAuth 2.0. The client key and secret are in `app/config.py`. Routes at `/tiktok-auth` and `/tiktok-oauth`. Redirect URI: `https://clips.gcp.my.id/tiktok-oauth`. Scope: `video.upload`.
   - **Upload script** at `~/podcast-clips/scripts/tiktok_upload.py`: uses the Content Posting API v2. Flow: initialize upload → PUT file to upload_url → POST to inbox. Videos go to `PUBLISH_TO_INBOX` mode — the user must open the TikTok app, check Inbox, and manually post.
   - **Usage**: `python scripts/tiktok_upload.py <num>` for one clip, `python scripts/tiktok_upload.py all` for all 6.
   - **Token storage**: `~/podcast-clips/app/tiktok_token.json` after successful OAuth.
   - **Domain verification for TikTok**: TikTok requires verifying ownership of the URL prefix (`clips.gcp.my.id`) before the Content Posting API works. Two ways:
     a) **DNS TXT record** (recommended): Add a TXT record for `clips.gcp.my.id` in Cloudflare DNS with value `tiktok-developers-site-verification=<string_from_tiktok>`. Verify with `dig TXT clips.gcp.my.id +short`.
     b) **File upload**: Place the verification `.txt` file at the web root (served by Flask static or route).

## Hard rules

- **Credentials must use os.getenv(), never hardcoded values** in `app/config.py`. YouTube client ID/secret, TikTok client key/secret, and SumoPod API keys must come from environment variables or `.env` files (already in .gitignore). The `.gitignore` must exclude `*_token.json`, `credentials/`, `.env`, and app-specific files like `app/youtube_token.json`. Verify with `git grep` before committing — any 30+ char alphanumeric string could be a secret.

- **Never reuse a `transcript.json` or `search_results.json` across episodes.** Both files are shared scratch names. After any re-transcribe and re-search, verify segment count and first-line text before running curate/caption. Leftover search data or a previous episode's transcript produces clips whose subtitles and captions describe a different video — this has happened and shipped to the user.
- **Never deploy test/non-Tempo episodes to the production web gallery.** The pipeline is for Tempo podcasts (jelasin-dong, bocor-alus, tukang-kupas) only. A test run on a random video (e.g. a tutorial) should remain in the isolated temp workspace — do not copy to `app/static/clips/` or restart Flask. Only deploy episodes the user has approved after reviewing the sample clip.
- **Long transcribes must run with `background=true, notify=true`.** A 28-min video takes ~25 min on CPU; foreground calls hit the 600s cap.
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

## Scripts

Working reference implementations live in `~/podcast-clips/` (curate.py, cut_smart.py, caption.py) — these are the canonical working copies and are updated when the pipeline changes.
