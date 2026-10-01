#!/usr/bin/env python3
"""Kurasi momen terbaik dari transkrip podcast via LLM (SumoPod)."""
import json, sys, os, urllib.request, re
from pathlib import Path
from clip_quality import format_timed_transcript, validate_clips

KEY = os.environ.get("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY", "")
if not KEY:
    for line in open(os.path.expanduser("~/.hermes/.env")):
        if line.startswith("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY="):
            KEY = line.strip().split("=", 1)[1].strip().strip('"')
if not KEY:
    sys.exit("API key not found")

LLM_MODEL = "MiniMax-M2.7-highspeed"
MODEL = LLM_MODEL
WORK_DIR_VALUE = os.environ.get("PODCAST_WORK_DIR")
if not WORK_DIR_VALUE:
    sys.exit("PODCAST_WORK_DIR is required, e.g. /tmp/podcast-clips/episode-id")
WORK_DIR = Path(WORK_DIR_VALUE)
WORK_DIR.mkdir(parents=True, exist_ok=True)
PODCAST_SLUG = sys.argv[2] if len(sys.argv) > 2 else "unknown"
EPISODE_TITLE_RAW = sys.argv[3] if len(sys.argv) > 3 else "episode"
EPISODE_SLUG = re.sub(r'[^a-z0-9]+', '-', EPISODE_TITLE_RAW.lower()).strip('-')

with open(WORK_DIR / "transcript.json") as f:
    segs = json.load(f)

transcript_text = format_timed_transcript(segs)

laughter_hint = ""
laughter_file = WORK_DIR / "laughter.json"
if laughter_file.exists():
    try:
        bursts = json.loads(laughter_file.read_text()).get("bursts", [])
        if bursts:
            ranges = ", ".join(f"{a}-{b}d" for a, b, _ in bursts[:40])
            laughter_hint = f"""
SINYAL AUDIO (deteksi ketawa/tawa kerumunan dari analisis loudness — rentang detik dengan energy burst tinggi):
{ranges}
Momen dengan burst ketawa biasanya adalah punchline terbaik. Prioritaskan rentang yang memiliki burst di dalamnya atau tepat setelahnya (ketawa = payoff). Klip TANPA burst hanya pilih kalimat benar-benar kuat secara naratif.
"""
    except Exception:
        laughter_hint = ""

prompt = f"""Kamu editor clip podcast. Dibawah ini transkrip podcast berbahasa Indonesia dengan timestamp.{laughter_hint}

Pilih 6 momen TERBAIK untuk dijadikan klip TikTok (durasi 40-70 detik). Kriteria:
- Ada punchline, hot take, cerita lucu, momen kaget, atau insight menarik
- Pembukaan klip harus langsung hook (kalimat pertama menarik, bukan kalimat lanjutan)
- Setiap klip punya alur mini: hook/pertanyaan, konteks secukupnya, lalu payoff atau insight
- Mulai dan akhiri pada batas kalimat yang utuh; jangan memotong kata atau membuang konteks yang diperlukan
- Pilih momen yang berbeda dan tidak mengulang bagian transkrip yang sama
- Topik politik diperbolehkan untuk podcast politik; jangan menambahkan klaim atau konteks yang tidak didukung transkrip
- Pilih start tepat pada awal segmen transkrip dan end tepat pada akhir segmen transkrip; jangan menebak timestamp di tengah segmen
- Field hook harus berupa kutipan verbatim dari teks pada rentang klip, bukan parafrasa
- Pilih rentang 40-70 detik setelah diselaraskan ke batas segmen

Balas HANYA JSON valid, tanpa markdown:
[{{"start": <detik awal>, "end": <detik akhir>, "title": "<judul klip max 50 char>", "hook": "<kalimat hook dari klip>"}}]

Transkrip:
{transcript_text}"""

def call_llm():
    req = urllib.request.Request(
        "https://ai.sumopod.com/v1/chat/completions",
        data=json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=600))
    except urllib.error.URLError as e:
        sys.exit(f"LLM request failed: {e}")
    except json.JSONDecodeError as e:
        sys.exit(f"LLM response not valid JSON: {e}")
    return resp["choices"][0]["message"]["content"].strip()


content = None
for attempt in range(1, 4):
    content = call_llm()
    try:
        start = content.index('{')
        end = content.rindex('}') + 1
        candidate = json.loads(content[start:end])
        episode_summary = candidate.get("episode_summary", "")
        x_post = candidate.get("x_post", {})
        clips = candidate.get("clips", [])
        if not episode_summary or not x_post.get("text"):
            raise ValueError("missing episode_summary or x_post.text")
        if not 6 <= len(clips) <= 12:
            raise ValueError(f"got {len(clips)} clips; expected 6-12")
        validate_clips(clips, segs, min_duration=35, max_duration=75)
        break
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"[curate] attempt {attempt}/3 invalid: {exc}", file=sys.stderr)
        if attempt == 3:
            sys.exit(f"curate failed after 3 attempts: {exc}")

episode_data = {
    "podcast_slug": PODCAST_SLUG,
    "episode_slug": EPISODE_SLUG,
    "episode_title": EPISODE_TITLE_RAW,
    "episode_summary": episode_summary,
    "x_post": x_post,
    "clips": clips,
}
with open(WORK_DIR / "episode_data.json", "w") as f:
    json.dump(episode_data, f, ensure_ascii=False, indent=2)
with open(WORK_DIR / "clips.json", "w") as f:
    json.dump(clips, f, ensure_ascii=False, indent=2)

print(f"episode_summary: {episode_summary[:80]}...")
print(f"x_post: {x_post['text'][:60]}")
print(f"clips ({len(clips)}):")
for clip in clips:
    print(f"  {clip['start']:.0f}-{clip['end']:.0f}s | {clip['title']}")
