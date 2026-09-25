#!/usr/bin/env python3
"""Kurasi momen terbaik dari transkrip podcast via LLM (SumoPod)."""
import json, sys, os, urllib.request

KEY = os.environ.get("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY", "")
if not KEY:
    for line in open(os.path.expanduser("~/.hermes/.env")):
        if line.startswith("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY="):
            KEY = line.strip().split("=", 1)[1].strip().strip('"')
if not KEY:
    sys.exit("API key not found")

MODEL = sys.argv[1] if len(sys.argv) > 1 else "MiniMax-M2.7-highspeed"
segs = json.load(open("transcript.json"))

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

Pilih jumlah momen TERBAIK yang paling sesuai, antara 6 sampai 12 momen, untuk dijadikan klip TikTok (durasi 40-70 detik). Jangan selalu memilih 6; gunakan 6-12 sesuai banyaknya momen kuat yang benar-benar layak. Kriteria:
- Ada punchline, hot take, cerita lucu, momen kaget, atau insight menarik
- Pembukaan klip harus langsung hook (kalimat pertama menarik, bukan kalimat lanjutan)
- Klip harus berdiri sendiri (gak perlu konteks sebelumnya)
- HINDARI topik politik/sara, pilih yang hiburan/cerita/insight netral

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
if not 6 <= len(clips) <= 12:
    sys.exit(f"LLM returned {len(clips)} clips; expected between 6 and 12")
json.dump(clips, open("clips.json", "w"), ensure_ascii=False, indent=2)
for c in clips:
    print(f"{c['start']:.0f}-{c['end']:.0f}s | {c['title']}")
