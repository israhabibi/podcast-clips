# Podcast Clips — Automated Pipeline

Clipping & auto-upload system for Tempo podcast episodes (Jelasin Dong!, Bocor Alus Politik, Tukang Kupas Perkara).

## Struktur

```
podcast-clips/
├── app/                   # Flask web app (clips.gcp.my.id)
│   ├── __init__.py
│   ├── app.py             # Entry point
│   ├── config.py          # OAuth credentials & config
│   ├── routes/
│   │   ├── clips.py       # Gallery YouTube-Shorts style
│   │   ├── youtube.py     # YouTube OAuth
│   │   └── tiktok.py      # TikTok OAuth
│   ├── templates/
│   │   └── clips.html     # Shorts-style gallery
│   └── static/clips/      # Video files + metadata
├── scripts/               # Pipeline scripts
│   ├── download.py        # yt-dlp wrapper
│   ├── transcribe.py      # YouTube API transcript → segments
│   ├── curate.py          # LLM-based moment selection
│   ├── cut_smart.py       # Face-track 9:16 crop + subtitles
│   ├── caption.py         # LLM caption + news links
│   ├── youtube_upload.py  # Upload to YouTube Shorts
│   └── tiktok_upload.py   # Upload to TikTok inbox
├── clips/                 # Working directory for pipeline
├── monitor.py             # RSS monitor (3 playlists)
├── credentials/           # API keys & OAuth info
└── README.md
```

## Pipeline

1. **Monitor** — `monitor.py` cek RSS 3 playlist Tempo tiap hari
2. **Download** — yt-dlp 720p mp4
3. **Transcribe** — YouTube API transcript → segments JSON
4. **Curate** — SumoPod LLM pilih 6 momen terbaik
5. **Cut** — Face-track 9:16 + subtitle burn-in
6. **Caption** — LLM generate caption + search berita terkait
7. **Deploy** — Copy ke Flask static + restart
8. **Upload** — YouTube Shorts & TikTok inbox

## Cronjob

`jelasin-shorts` — tiap hari 09:00 WIB, monitor trigger → auto pipeline → kirim sample ke Telegram.

## Setup

```bash
cd ~/podcast-clips
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```
