#!/usr/bin/env python3
"""Kurasi momen terbaik + rangkuman episode + x post dari transkrip podcast via LLM (SumoPod)."""
import json, sys, os, urllib.request, re
from pathlib import Path

KEY = os.environ.get("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY", "")
if not KEY:
    for line in open(os.path.expanduser("~/.hermes/.env")):
        if line.startswith("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY="):
            KEY = line.strip().split("=", 1)[1].strip().strip('"')
if not KEY:
    sys.exit("API key not found")

MODEL = sys.argv[1] if len(sys.argv) > 1 else "MiniMax-M2.7-highspeed"
WORK_DIR_VALUE = os.environ.get("PODCAST_WORK_DIR")
if not WORK_DIR_VALUE:
    sys.exit("PODCAST_WORK_DIR is required, e.g. /tmp/podcast-clips/episode-id")
WORK_DIR = Path(WORK_DIR_VALUE)
WORK_DIR.mkdir(parents=True, exist_ok=True)

# Args: curate.py <model> <podcast_slug> <episode_title>
# podcast_slug: jelasin-dong | bocor-alus | tukang-kupas
# episode_title: judul episode (akan di-slugify)
PODCAST_SLUG = sys.argv[2] if len(sys.argv) > 2 else "unknown"
EPISODE_TITLE_RAW = sys.argv[3] if len(sys.argv) > 3 else "episode"
EPISODE_SLUG = re.sub(r'[^a-z0-9]+', '-', EPISODE_TITLE_RAW.lower()).strip('-')

segs = json.load(open(WORK_DIR / "transcript.json"))

# buat transkrip bertimestamp (per ~30s chunk biar ringkas)
lines = []
cur_start = None
buf = []
def flush():
    global cur_start, buf
    if buf:
        lines.append(f"[{cur_start:.0f}s] " + " ".join(buf))
    buf = []
for s in segs:
    if cur_start is None:
        cur_start = s["start"]
    buf.append(s["text"])
    if s["end"] - cur_start >= 30:
        flush(); cur_start = None
flush()
transcript_text = "\n".join(lines)

prompt = f"""Kamu editor clip podcast. Dibawah ini transkrip podcast berbahasa Indonesia dengan timestamp.

Tugas kamuhasilkan SATUSATU respons JSON dengan tiga field:
1. "episode_summary": ringkasan episode 3-5 kalimat (untuk web gallery, buat orang paham episode ini tentang apa)
2. "x_post": objek dengan dua field:
   - "text": hook pendek 1-2 kalimat buat X/Twitter, tanpa hashtag, tanpa link — link akan disubtitusi depoisisi
   - "hashtags": array string hashtag Indonesia yang relevan (3-5 tags)
3. "clips": array 6-12 momen terbaik untuk jadi klip TikTok/Shorts (durasi 40-70 detik). Jangan selalu pilih 6; pilih sesuai banyaknya momen kuat yang benar-benar layak.

Kriteria klip:
- Ada punchline, hot take, cerita lucu, momen kaget, atau insight menarik
- Pembukaan klip harus langsung hook (kalimat pertama menarik, bukan kalimat lanjutan)
- Klip harus berdiri sendiri (gak perlu konteks sebelumnya)
- HINDARI topik politik/sara, pilih yang hiburan/cerita/insight netral

Balas HANYA JSON valid, tanpa markdown, tanpa penjelasan:
{{{{"episode_summary": "...", "x_post": {{"text": "...", "hashtags": ["#tag1", ...]}}, "clips": [{{"start": <detik>, "end": <detik>, "title": "<judul max 50 char>", "hook": "<kalimat hook dari klip>"}}]}}}}

Transkrip:
{transcript_text}"""

req = urllib.request.Request(
    "https://ai.sumopod.com/v1/chat/completions",
    data=json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }).encode(),
    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
)
resp = json.load(urllib.request.urlopen(req, timeout=300))
content = resp["choices"][0]["message"]["content"].strip()
# Extract JSON object (may be wrapped in ``` or just raw)
try:
    # Try to find outermost braces
    start = content.index('{')
    end = content.rindex('}') + 1
    content = content[start:end]
    data = json.loads(content)
except ValueError:
    sys.exit(f"Failed to parse JSON from LLM response: {content[:200]}")

episode_summary = data.get("episode_summary", "")
x_post = data.get("x_post", {})
clips = data.get("clips", [])

if not episode_summary:
    sys.exit("LLM returned no episode_summary")
if not x_post or not x_post.get("text"):
    sys.exit("LLM returned no x_post.text")
if not 6 <= len(clips) <= 12:
    sys.exit(f"LLM returned {len(clips)} clips; expected between 6 and 12")

# Build episode_data.json
episode_data = {
    "podcast_slug": PODCAST_SLUG,
    "episode_slug": EPISODE_SLUG,
    "episode_title": EPISODE_TITLE_RAW,
    "episode_summary": episode_summary,
    "x_post": x_post,
    "clips": clips,
}
json.dump(episode_data, open(WORK_DIR / "episode_data.json", "w"), ensure_ascii=False, indent=2)

# Also write clips.json for cut_smart.py backward compat
json.dump(clips, open(WORK_DIR / "clips.json", "w"), ensure_ascii=False, indent=2)

print(f"episode_summary: {episode_summary[:80]}...")
print(f"x_post: {x_post['text'][:60]}")
print(f"clips ({len(clips)}):")
for c in clips:
    print(f"  {c['start']:.0f}-{c['end']:.0f}s | {c['title']}")