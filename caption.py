#!/usr/bin/env python3
"""Caption + link berita terkait per klip via LLM + web search."""
import json, sys, os, urllib.request, urllib.parse
from pathlib import Path

KEY = os.environ.get("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY", "")
if not KEY:
    hermes_env = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(hermes_env):
        with open(hermes_env) as f:
            for line in f:
                if line.startswith("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY="):
                    KEY = line.strip().split("=", 1)[1].strip().strip('"')

WORK_DIR_VALUE = os.environ.get("PODCAST_WORK_DIR")
if not WORK_DIR_VALUE:
    sys.exit("PODCAST_WORK_DIR is required, e.g. /tmp/podcast-clips/episode-id")
WORK_DIR = Path(WORK_DIR_VALUE)
WORK_DIR.mkdir(parents=True, exist_ok=True)

with open(WORK_DIR / "clips.json") as f:
    clips = json.load(f)
with open(WORK_DIR / "transcript.json") as f:
    segs = json.load(f)

LLM_MODEL = os.environ.get("CAPTION_LLM_MODEL", "MiniMax-M2.7-highspeed")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "300"))

def llm(prompt):
    req = urllib.request.Request(
        "https://ai.sumopod.com/v1/chat/completions",
        data=json.dumps({"model": LLM_MODEL,
                         "messages": [{"role": "user", "content": prompt}],
                         "temperature": 0.3}).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=LLM_TIMEOUT))["choices"][0]["message"]["content"]
    except urllib.error.URLError as e:
        print(f"  LLM request failed: {e}")
        return ""
    except json.JSONDecodeError as e:
        print(f"  LLM response not valid JSON: {e}")
        return ""

results = {}
search_file = WORK_DIR / "search_results.json"
search_data = json.load(open(search_file)) if search_file.exists() else {}

for i, c in enumerate(clips, 1):
    t0, t1 = float(c["start"]), float(c["end"])
    text = " ".join(s["text"] for s in segs if s["end"] > t0 and s["start"] < t1)
    # 1. LLM buat search query dari isi klip
    q = llm(f"Berdasarkan transkrip klip podcast ini, buat SATU search query berbahasa Indonesia untuk mencari berita terkait topiknya. Balas HANYA query-nya, tanpa penjelasan.\n\nTranskrip: {text[:1500]}").strip().strip('"')
    print(f"clip{i:02d} query: {q}")
    # 2. cari berita (pakai hasil pre-fetched)
    found = search_data.get(str(i)) or search_data.get(i)
    if not found:
        found = []
    # 3. LLM rangkai caption (tanpa link — link ditambahkan setelah)
    cap = llm(f"""Kamu social media manager. Klip podcast ini akan diposting ke TikTok.
Buat caption TikTok: 1-2 kalimat hook + 1-3 hashtag relevan bahasa Indonesia.
JANGAN sertakan link apapun.

Transkrip klip: {text[:1200]}

Balas HANYA caption-nya.""")

    # FORCE semua link dari search_results ke akhir caption
    if found:
        cap = cap.strip()
        for f in found:
            cap += f"\n{f['url']}"
    results[str(i)] = {"clip": f"clip{i:02d}.mp4", "title": c["title"], "caption": cap, "query": q}
    print(f"  caption: {cap[:100]}")

with open(WORK_DIR / "captions.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("\nsaved captions.json")
