#!/usr/bin/env python3
"""Kurasi momen terbaik + rangkuman episode + x post dari transkrip podcast via LLM (SumoPod)."""
import json, sys, os, urllib.request, re
from pathlib import Path
from clip_quality import format_timed_transcript, validate_clips

KEY = os.environ.get("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY", "")
if not KEY:
    hermes_env = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(hermes_env):
        with open(hermes_env) as f:
            for line in f:
                if line.startswith("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY="):
                    KEY = line.strip().split("=", 1)[1].strip().strip('"')
if not KEY:
    sys.exit("API key not found")

# First arg is whisper model size (small/medium etc) — NOT the LLM model
# Always use MiniMax-M2.7-highspeed for LLM calls
LLM_MODEL = "MiniMax-M2.7-highspeed"
MODEL = LLM_MODEL
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

_DATE_HIT = re.search(r'(20\d{2})[-_. ]?(0[1-9]|1[0-2])[-_. ]?(0[1-9]|[12]\d|3[01])', EPISODE_TITLE_RAW)
if _DATE_HIT:
    EPISODE_DATE = f"{_DATE_HIT.group(1)}-{_DATE_HIT.group(2)}-{_DATE_HIT.group(3)}"
else:
    from datetime import datetime, timezone
    EPISODE_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _slug_filename_part(text, maxlen=80):
    clean = re.sub(r'[^a-z0-9]+', '-', str(text or "").lower()).strip('-')
    if len(clean) > maxlen:
        clean = clean[:maxlen].rstrip('-')
    return clean

def clip_filename(index, podcast=PODCAST_SLUG, date=EPISODE_DATE, title=EPISODE_SLUG, suffix=".mp4"):
    podcast_s = _slug_filename_part(podcast, 30)
    title_s = _slug_filename_part(title, 60)
    base = f"{podcast_s}_{date}_{title_s}_{int(index):02d}"
    if len(base) > 180:
        title_s = _slug_filename_part(title, max(10, 180 - (len(podcast_s) + len(date) + 10)))
        base = f"{podcast_s}_{date}_{title_s}_{int(index):02d}"
    return base + suffix

with open(WORK_DIR / "transcript.json") as f:
    segs = json.load(f)

transcript_text = format_timed_transcript(segs)

# Laughter signal (audio burst detection) — hint the LLM where the room exploded
laughter_hint = ""
laughter_file = WORK_DIR / "laughter.json"
if laughter_file.exists():
    try:
        ldata = json.loads(laughter_file.read_text())
        bursts = ldata.get("bursts", [])
        if bursts:
            # merge adjacent bursts and map to readable ranges
            ranges = ", ".join(f"{a}-{b}d" for a, b, _ in bursts[:40])
            laughter_hint = f"""
SINYAL AUDIO (deteksi ketawa/tawa kerumunan dari analisis loudness — rentang detik dengan energy burst tinggi):
{ranges}
Momen dengan burst ketawa biasanya adalah punchline terbaik. Prioritaskan rentang yang memiliki burst di dalamnya atau tepat setelahnya (ketawa = payoff). Klip TANPA burst hanya pilih kalimat benar-benar kuat secara naratif.
"""
    except Exception:
        laughter_hint = ""

prompt = f"""Kamu editor clip podcast. Dibawah ini transkrip podcast berbahasa Indonesia dengan timestamp.{laughter_hint}

Tugas kamuhasilkan SATUSATU respons JSON dengan tiga field:
1. "episode_summary": ringkasan episode 3-5 kalimat (untuk web gallery, buat orang paham episode ini tentang apa)
2. "x_post": objek dengan dua field:
   - "text": hook pendek 1-2 kalimat buat X/Twitter, tanpa hashtag, tanpa link — link akan disubtitusi depoisisi
   - "hashtags": array string hashtag Indonesia yang relevan (3-5 tags)
3. "clips": array 6-12 momen terbaik untuk jadi klip TikTok/Shorts (durasi 40-70 detik). Jangan selalu pilih 6; pilih sesuai banyaknya momen kuat yang benar-benar layak.

Kriteria klip:
- Ada punchline, hot take, cerita lucu, momen kaget, atau insight menarik
- Pembukaan klip harus langsung hook (kalimat pertama menarik, bukan kalimat lanjutan)
- Setiap klip punya alur mini: hook/pertanyaan, konteks secukupnya, lalu payoff atau insight
- Mulai dan akhiri pada batas kalimat yang utuh; jangan memotong kata atau membuang konteks yang diperlukan
- Pilih momen yang berbeda dan tidak mengulang bagian transkrip yang sama
- Topik politik diperbolehkan untuk podcast politik; jangan menambahkan klaim atau konteks yang tidak didukung transkrip
- Pilih start tepat pada awal segmen transkrip dan end tepat pada akhir segmen transkrip; jangan menebak timestamp di tengah segmen
- Field hook harus berupa kutipan verbatim dari teks pada rentang klip, bukan parafrasa
- Pilih rentang 40-70 detik setelah diselaraskan ke batas segmen

Balas HANYA JSON valid, tanpa markdown, tanpa penjelasan:
{{{{"episode_summary": "...", "x_post": {{"text": "...", "hashtags": ["#tag1", ...]}}, "clips": [{{"start": <detik>, "end": <detik>, "title": "<judul max 50 char>", "hook": "<kalimat hook dari klip>"}}]}}}}

Transkrip:
{transcript_text}"""

def call_llm():
    req = urllib.request.Request(
        "https://ai.sumopod.com/v1/chat/completions",
        data=json.dumps({
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=600))
    except urllib.error.URLError as e:
        sys.exit(f"LLM request failed: {e}")
    except json.JSONDecodeError as e:
        sys.exit(f"LLM response not valid JSON: {e}")
    return resp["choices"][0]["message"]["content"].strip()


# Retry up to 3x: transient LLM failures (bad JSON, invalid clip metadata, wrong clip count)
# are common; the worker aborts the whole episode otherwise.
MAX_CURATE_ATTEMPTS = 3
content = None
validated_data = None
for attempt in range(1, MAX_CURATE_ATTEMPTS + 1):
    content = call_llm()
    # Extract JSON object (may be wrapped in ``` or just raw)
    try:
        start = content.index('{')
        end = content.rindex('}') + 1
        candidate = json.loads(content[start:end])
        _summary = candidate.get("episode_summary", "")
        _xpost = candidate.get("x_post", {})
        _clips = candidate.get("clips", [])
        if not _summary or not _xpost.get("text"):
            raise ValueError("missing episode_summary or x_post.text")
        if not 6 <= len(_clips) <= 12:
            raise ValueError(f"got {len(_clips)} clips; expected 6-12")
        _clips = validate_clips(
            _clips, segs, min_duration=35, max_duration=75
        )  # lenient: LLM often lands just outside 40-70
        candidate["clips"] = _clips
        validated_data = candidate
        break  # fully valid
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"[curate] attempt {attempt}/{MAX_CURATE_ATTEMPTS} invalid: {exc}", file=sys.stderr)
        if attempt == MAX_CURATE_ATTEMPTS:
            sys.exit(f"curate failed after {MAX_CURATE_ATTEMPTS} attempts: {exc}")
        content = None

# Use the normalized object that passed validation. Re-parsing the raw LLM
# response here would discard snapped timestamps and repaired hooks/titles.
data = validated_data

episode_summary = data.get("episode_summary", "")
x_post = data.get("x_post", {})
clips = data.get("clips", [])

for i, c in enumerate(clips, 1):
    c["filename"] = clip_filename(i, suffix=".mp4")

# Build episode_data.json
episode_data = {
    "podcast_slug": PODCAST_SLUG,
    "episode_slug": EPISODE_SLUG,
    "episode_title": EPISODE_TITLE_RAW,
    "episode_date": EPISODE_DATE,
    "episode_summary": episode_summary,
    "x_post": x_post,
    "clips": clips,
}
with open(WORK_DIR / "episode_data.json", "w") as f:
    json.dump(episode_data, f, ensure_ascii=False, indent=2)

# Also write clips.json for cut_smart.py backward compat
with open(WORK_DIR / "clips.json", "w") as f:
    json.dump(clips, f, ensure_ascii=False, indent=2)

print(f"episode_summary: {episode_summary[:80]}...")
print(f"x_post: {x_post['text'][:60]}")
print(f"clips ({len(clips)}):")
for c in clips:
    print(f"  {c['start']:.0f}-{c['end']:.0f}s | {c['title']}")
