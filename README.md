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
