#!/usr/bin/env python3
"""Kurasi momen terbaik dari transkrip podcast via LLM (SumoPod)."""
import json, sys, os, urllib.request
from clip_quality import format_timed_transcript, validate_clips

KEY = os.environ.get("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY", "")
if not KEY:
    for line in open(os.path.expanduser("~/.hermes/.env")):
        if line.startswith("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY="):
            KEY = line.strip().split("=", 1)[1].strip().strip('"')
if not KEY:
    sys.exit("API key not found")

MODEL = sys.argv[1] if len(sys.argv) > 1 else "MiniMax-M2.7-highspeed"
segs = json.load(open("transcript.json"))

transcript_text = format_timed_transcript(segs)

prompt = f"""Kamu editor clip podcast. Dibawah ini transkrip podcast berbahasa Indonesia dengan timestamp.

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
content = content[content.index("["):content.rindex("]")+1]
clips = json.loads(content)
try:
    clips = validate_clips(clips, segs)
except ValueError as exc:
    sys.exit(f"Invalid clip metadata: {exc}")
json.dump(clips, open("clips.json", "w"), ensure_ascii=False, indent=2)
for c in clips:
    print(f"{c['start']:.0f}-{c['end']:.0f}s | {c['title']}")
