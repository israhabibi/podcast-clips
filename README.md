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
8. **Upload** — YouTube Shorts & TikTok inbox

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
```

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

# Install skill
mkdir -p ~/.hermes/skills/media/
cp -r hermes-skill ~/.hermes/skills/media/podcast-clipping

# Setup credentials
cp .env.example app/.env
# Edit app/.env dengan OAuth keys yang sesuai
```

## Branch Protection

Push langsung ke `main` diblokir oleh pre-push hook lokal. Gunakan Pull Request workflow:

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

Untuk proteksi tambahan di sisi server, aktifkan Branch Protection Rules di:
**GitHub > Repo > Settings > Branches > Add branch protection rule >**
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
