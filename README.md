# Podcast Clips — Automated Pipeline

Clipping & auto-upload system for Tempo podcast episodes (Jelasin Dong!, Bocor Alus Politik, Tukang Kupas Perkara).

## Struktur

```
podcast-clips/
├── app/                   # Flask web app (clips.gcp.my.id)
│   ├── __init__.py
│   ├── run.py              # Entry point
│   ├── config.py          # OAuth credentials & config
│   ├── routes/
│   │   ├── clips.py       # Gallery YouTube-Shorts style
│   │   ├── youtube.py     # YouTube OAuth
│   │   └── tiktok.py      # TikTok OAuth
│   ├── templates/
│   │   └── clips.html     # Shorts-style gallery
│   └── static/clips/      # Video files + metadata
├── scripts/               # Upload and transcript utility scripts
├── work/                  # Archived local episode workspaces (ignored generated media/data)
│   ├── ep1/               # Source video, transcript, metadata, subtitles, clips
│   ├── ep2/
│   └── ai-tutorial/
├── curate.py              # LLM: episode summary + x_post + moment selection
├── cut_smart.py           # Face-track 9:16 crop + subtitles
├── caption.py             # LLM caption + news links
├── monitor.py             # RSS monitor (3 playlists)
├── credentials/           # API keys & OAuth info
└── README.md
```

## Pipeline

1. **Monitor** — `monitor.py` cek RSS 3 playlist Tempo tiap hari
2. **Download** — simpan source video ke `/tmp/podcast-clips/<episode-id>/`
3. **Transcribe** — tulis segments JSON ke `/tmp/podcast-clips/<episode-id>/transcript.json`
4. **Curate** — SumoPod LLM: episode summary + X post + 6–12 momen terbaik → `episode_data.json` + `clips.json`
5. **Cut** — Face-track 9:16 + subtitle burn-in ke `/tmp/podcast-clips/<episode-id>/clips/`
6. **Caption** — LLM generate caption + search berita terkait di folder episode
7. **Deploy** — Copy ke Flask static + restart
8. **Upload** — YouTube Shorts & TikTok inbox; video kompilasi TOP 5 bisa diteruskan ke Meta Threads

## Yang Bekerja

| Tahap | Komponen | Yang mengerjakan | Output |
|---|---|---|---|
| Monitor | `monitor.py` | CPU lokal + network RSS YouTube | Episode baru |
| Download | `yt-dlp` | CPU lokal + network YouTube | Video di `/tmp/podcast-clips/<episode-id>/` |
| Transcribe | faster-whisper | CPU lokal, cukup berat dan lama | `transcript.json` |
| Transcribe alternatif | YouTube transcript API | YouTube API, hampir tanpa beban CPU | `transcript.json` |
| Curate | `curate.py` | LLM SumoPod/API | `episode_data.json` (summary + x_post + clips), `clips.json` |
| Cut dan reframe | `cut_smart.py` + OpenCV + FFmpeg | CPU lokal, tahap paling berat | Video 9:16 di `clips/` |
| Caption | `caption.py` | LLM SumoPod/API + data search | `captions.json` |
| Web gallery | Flask | CPU/server lokal | `clips.gcp.my.id` |
| Upload | YouTube/TikTok API | Network + API platform | Video terpublikasi atau masuk inbox |

### Ringkasnya

- **LLM/API:** kurasi momen dan pembuatan caption di `curate.py` dan `caption.py`.
- **CPU lokal:** transkripsi faster-whisper, deteksi wajah, reframe crop, subtitle, dan encoding FFmpeg.
- **Network/API:** download video, RSS monitor, YouTube transcript, OAuth, dan upload.
- **Folder `/tmp/podcast-clips/` bersifat sementara:** video dan hasil pipeline tidak masuk Git; yang dikirim ke GitHub hanya kode, dokumentasi, dan template konfigurasi. Folder `work/` hanya arsip lokal lama.

## Cronjob

`jelasin-shorts` — tiap hari 09:00 WIB, monitor trigger → auto pipeline → kirim sample ke Telegram.

## Setup

```bash
cd ~/podcast-clips
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
python scripts/check_setup.py
```

The setup check validates the Python packages, `ffmpeg`/`ffprobe`, `yt-dlp`, the YuNet ONNX face model, and the DejaVu subtitle font. Set `FACE_YUNET_MODEL`, `PODCAST_FONT_DIR`, or `PODCAST_FONT_FILE` when these assets are installed elsewhere. The media preflight also runs before an episode download starts:

```bash
python scripts/check_setup.py --media
```

### Production web service

The repository includes a Gunicorn WSGI entry point and service template. Install the pinned requirements, confirm `app/.env` and `~/.hermes/.env` are readable by the service user, then install/reload the user service:

```bash
cp deploy/flask-app.service ~/.config/systemd/user/flask-app.service
systemctl --user daemon-reload
systemctl --user enable --now flask-app
systemctl --user status flask-app --no-pager
```

Gunicorn listens on `127.0.0.1:5000`; keep the existing HTTPS reverse proxy/tunnel in front of it. Its startup check validates web dependencies before the service starts. To enable quota retry processing, install the matching timer; quota failures are queued for a retry after 24 hours, and the timer checks for due items every 30 minutes:

```bash
cp deploy/youtube-retry.service deploy/youtube-retry.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now youtube-retry.timer
systemctl --user list-timers youtube-retry.timer
```

The worker uploads only clips whose release approval still matches the current file SHA-256. Install the timer only after the YouTube token and environment files are configured for the service user.

After inserting a video, the uploader verifies its persisted title, description, category and tags. If verification fails, the admin page shows `metadata_pending` and schedules a repair. Retrying uses the saved video ID, without inserting another video; replacing the media file prevents repair of the previous video's metadata. The CLI returns nonzero while metadata is pending. Default tags contain 15 entries, and titles end in one `#Shorts` within YouTube's 100-character limit.

### Admin page

The Flask app has a password-protected `/admin` page for connecting YouTube/TikTok upload accounts and submitting a YouTube episode URL. Set these environment variables in the service or shell that starts `app/run.py`:

```bash
# Generate a password hash and a session secret with Python on the server.
python -c 'from werkzeug.security import generate_password_hash; import getpass; print(generate_password_hash(getpass.getpass("Admin password: ")))'
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Assign the outputs to `ADMIN_PASSWORD_HASH` and `FLASK_SECRET_KEY`. Keep both private and persistent across restarts. Configure the existing YouTube/TikTok OAuth variables in `.env.example` in the same environment. Copying `.env.example` alone does not load it into the process. Use absolute token-file paths if the web app and upload scripts have different working directories. HTTPS deployments should keep `SESSION_COOKIE_SECURE=true` (the default); for local HTTP testing set it to `false`.

The provided systemd service sets `APP_ENV=production`; Flask refuses to start if `FLASK_SECRET_KEY` is missing. Local development keeps the random-key fallback so the public gallery can be run without production credentials, while admin login remains unavailable until both admin variables are configured.

OAuth account replacement and YouTube publication are fail-closed: `YOUTUBE_EXPECTED_CHANNEL_ID` is set in `.env.example` to the channel ID for [@inhabibi-my-id](https://www.youtube.com/@inhabibi-my-id) (`UCuqm8syvmu1DOqkwzy9usZA`). Copy/configure it in `app/.env` before reconnecting YouTube or uploading; the uploader checks the token's current channel before inserting or repairing video metadata. Set `TIKTOK_EXPECTED_OPEN_ID` to the intended TikTok account before reconnecting TikTok. Callbacks verify returned identities and leave existing token files untouched on mismatch. These are public account identifiers, not credentials; never put access tokens in these variables.

For a shell session, export the values before starting the app (keep the hash in single quotes because it contains `$` characters):

```bash
export ADMIN_PASSWORD_HASH='paste-generated-hash-here'
export FLASK_SECRET_KEY='paste-generated-secret-here'
python app/run.py
```

Open `/admin`, sign in, and use the account buttons to start OAuth. The old `/auth` and `/tiktok-auth` URLs now require the same admin session. Pasting a YouTube URL fetches its public title and tries to identify the podcast from the title or the known Tempo playlist feeds; if it cannot identify the show, select it manually. Submitted links are validated, normalized, and saved to `app/data/admin.sqlite3` by default; this database is ignored by Git. A submission is a **pending queue item**. It does not start transcription, clipping, or publishing automatically unless you choose “Proses sekarang”. Pending and failed submissions expose a retry action in the queue; retries reuse and atomically claim the existing database row.

Each failed or pending source card shows its retry/process button even while Details is collapsed. Opening Details reveals the submission and saved error; failed jobs show unavailable progress instead of 100% completion.

### Auto-post video TOP 5 ke Meta Threads

Setelah video kompilasi TOP 5 disetujui untuk rilis dan upload beserta metadata YouTube berhasil, aplikasi mengantrekan **video yang sama + caption** ke Threads. Klip biasa tidak ikut. Antrean dan status Threads tersimpan di SQLite, terpisah dari keberhasilan upload YouTube. Admin → Beranda menyediakan pengaturan auto-post; panel TOP 5 menampilkan status, tombol retry, dan tautan posting jika tersedia. Mengaktifkan akun tidak otomatis memosting ulang video lama; untuk TOP 5 yang sudah diupload, gunakan tombol posting di panelnya.

Integrasi mengikuti mode user token yang sudah dipakai `/home/isra/techbro/techbro-pipeline`. Untuk memakai akun yang sama, set `THREADS_ENV_FILE=/home/isra/techbro/techbro-pipeline/.env` pada environment **Flask, youtube-retry, dan threads-post**. Loader hanya membaca `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID`, dan `THREADS_APP_ID`; konfigurasi pipeline lain tidak diimpor. Token tidak disalin dan file referensi tidak diubah. Pembaruan token tetap dilakukan oleh techbro; worker membaca token terbaru setiap memproses antrean. Alternatifnya, set `THREADS_ACCESS_TOKEN` dan `THREADS_USER_ID` langsung di environment layanan ini. Sebelum posting, aplikasi memeriksa bahwa token memang milik ID akun yang dikonfigurasi.

Template `deploy/threads-shared-account.conf` bisa dipasang sebagai drop-in `~/.config/systemd/user/<nama-layanan>.service.d/30-podcast-threads-techbro.conf` untuk ketiga layanan tersebut. Setelah `systemctl --user daemon-reload`, restart `flask-app` agar proses web membaca environment baru. Ini tidak memerlukan perubahan `app/.env`.

Untuk akun lain melalui OAuth, konfigurasi `THREADS_APP_ID`, `THREADS_APP_SECRET`, dan `THREADS_REDIRECT`, lalu hubungkan akun dari Beranda. App Meta harus memiliki use case Threads dan izin `threads_basic,threads_content_publish`. Token OAuth disimpan privat di `THREADS_TOKEN_FILE`; token mode environment lebih diutamakan bila dikonfigurasi.

Meta mengambil MP4 dari URL HTTPS publik di `THREADS_PUBLIC_BASE_URL` (default `https://clips.gcp.my.id`), sehingga video perlu sudah dideploy ke `app/static/clips`. Pembuatan container dan publish mengikuti [Threads API resmi Meta](https://www.postman.com/meta/threads/request/mev9xf8/1-3-create-video-container). Install worker timer sesudah environment layanan dikonfigurasi:

```bash
cp deploy/threads-post.service deploy/threads-post.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now threads-post.timer
```

Worker dijalankan segera setelah enqueue dan timer memeriksa antrean setiap menit. Jika respons publish hilang, status menjadi `needs_review`: tombol **Cek status Threads** hanya memeriksa container sebelumnya, tanpa mengirim publish lagi. Periksa profil Threads bila status tetap belum terkonfirmasi. Container yang sudah `PUBLISHED` atau post ID yang sudah tersimpan tidak diposting ulang.

### Antrean episode

The existing pipeline agent can read and update the queue with:

```bash
python scripts/admin_queue.py list --status pending
python scripts/admin_queue.py set-status VIDEO_ID in_progress
python scripts/admin_queue.py set-status VIDEO_ID ready_for_review
python scripts/admin_queue.py set-status VIDEO_ID completed
# Use failed if processing does not succeed.
```

Set `ADMIN_DB_PATH` to an absolute path if the web app and pipeline run from different working directories on the same machine; both must use the same database. A separate-host deployment needs a shared queue or database service instead of this local SQLite file. The episode worker sets `ready_for_review` after local clips and captions are generated. Mark an item `completed` only after the review and delivery workflow succeeds. The admin page shows the latest 50 submissions and their status.

Run `python -m unittest test_admin test_pipeline` before deploying changes to the admin routes.

Untuk upload hasil episode tertentu, arahkan uploader ke workspace episode tersebut:

```bash
PODCAST_WORK_DIR=/tmp/podcast-clips/<episode-id> python scripts/youtube_upload.py all
PODCAST_WORK_DIR=/tmp/podcast-clips/<episode-id> python scripts/tiktok_upload.py all
```

Ganti `work/<episode>` dengan `/tmp/podcast-clips/<episode-id>` untuk episode baru. Token OAuth tetap tersimpan di `app/`.

## Replicate di mesin baru

```bash
# Clone repo
git clone https://github.com/israhabibi/podcast-clips.git
cd podcast-clips

# Setup venv
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Install skill (recommended: symlink)
mkdir -p ~/.hermes/skills/media/
# Symlink the hermes-skill folder into Hermes skills dir (RECOMMENDED).
# After this: edit files in /path/to/podcast-clips/hermes-skill/ and the
# installed skill changes instantly — no re-install ever again. This also
# fixes drift: repo SKILL.md becomes the runtime SKILL.md Hermes reads.
ln -s "$(pwd)/hermes-skill" ~/.hermes/skills/media/podcast-clipping

# --- Fallback: copy-only install (if symlinks are unavailable) ---
# rm -rf ~/.hermes/skills/media/podcast-clipping
# cp -r hermes-skill ~/.hermes/skills/media/podcast-clipping
# ^ Re-run this every time you edit SKILL.md or any shim in hermes-skill/.

# The shims in hermes-skill/scripts/*.py forward each call to the single
# maintained copy in the repo root (either <repo>/*.py or <repo>/scripts/*.py).
# No manual mirroring of pipeline logic required.

# Optional: if you cloned the repo somewhere OTHER than ~/podcast-clips,
# set these two env vars in your shell profile (otherwise defaults are fine
# for the standard checkout):
#   export PODCAST_CLIPS_REPO=/path/to/your/checkout/podcast-clips
#   export PODCAST_CLIPS_PYTHON=/path/to/your/checkout/.venv/bin/python

# Setup credentials
cp .env.example app/.env
# Edit app/.env dengan OAuth keys yang sesuai
```

## Branch Protection

Gunakan Pull Request workflow:

```bash
# 1. Buat branch baru
git checkout -b fix/nama-perbaikan

# 2. Commit perubahan
git add .
git commit -m "fix: deskripsi perbaikan"

# 3. Push branch
git push origin fix/nama-perbaikan

# 4. Buat Pull Request di GitHub
#    Buka https://github.com/israhabibi/podcast-clips
#    Klik "Compare & pull request"
#    Isi deskripsi, klik "Create pull request"

# 5. Merge via GitHub UI setelah review
```

`hooks/pre-push` adalah bantuan lokal yang harus dipasang manual dan dapat dilewati; clone baru tidak menegakkan kebijakan ini. Proteksi server GitHub belum diverifikasi atau dikonfigurasi oleh repo ini. Pemilik/admin repo perlu membuka **GitHub > Repo > Settings > Branches > Add branch protection rule** dan menetapkan:
- Branch: `main`
- ✅ Require pull request before merging
- ✅ Dismiss stale pull request approvals
- ✅ Require branches to be up-to-date
- ✅ Do not allow bypassing the above settings

## Biaya per Episode

**Model: MiniMax-M2.7-highspeed (SumoPod)**

Contoh biaya untuk 1 episode (6 clips, ~945 transcript segments, 6 caption search + generate):

| Tahap | Token (input) | Token (output) | Biaya |
|-------|------------:|------------:|------:|
| Curate (summary + clips) | ~3.2M | ~25K | ~$0.09 |
| Caption (6 clips + search) | ~120K | ~5K | ~$0.01 |
| **Total per episode** | **~3.35M** | **~30K** | **~$0.10 ≈ Rp 1.800** |

Ringkasan: **~Rp 1.800 per episode** (6 Shorts) — 1 clip ≈ Rp 300.

## Twitter / X Promo

X post (hook + link web gallery) di-generate otomatis oleh `curate.py` dan disimpan di `episode_data.json`. Posting manual — belum ada auto-post API (X API berbayar $100/mo).
