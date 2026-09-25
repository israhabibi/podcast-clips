#!/usr/bin/env python3
"""Caption + link berita terkait per klip via LLM + web search."""
import json, sys, os, urllib.request, urllib.parse

KEY = os.environ.get("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY", "")
if not KEY:
    for line in open(os.path.expanduser("~/.hermes/.env")):
        if line.startswith("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY="):
            KEY = line.strip().split("=", 1)[1].strip().strip('"')

clips = json.load(open("clips.json"))
segs = json.load(open("transcript.json"))

def llm(prompt):
    req = urllib.request.Request(
        "https://ai.sumopod.com/v1/chat/completions",
        data=json.dumps({"model": "MiniMax-M2.7-highspeed",
                         "messages": [{"role": "user", "content": prompt}],
                         "temperature": 0.3}).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=300))["choices"][0]["message"]["content"]

def search(q, limit=4):
    """Web search via Hermes CLI bridge (dipanggil dari luar) — di sini dummy output file."""
    # dipakai kalau search_results.json ada
    pass

results = {}
search_data = json.load(open("search_results.json")) if os.path.exists("search_results.json") else {}

for i, c in enumerate(clips, 1):
    t0, t1 = float(c["start"]), float(c["end"])
    text = " ".join(s["text"] for s in segs if s["end"] > t0 and s["start"] < t1)
    # 1. LLM buat search query dari isi klip
    q = llm(f"Berdasarkan transkrip klip podcast ini, buat SATU search query berbahasa Indonesia untuk mencari berita terkait topiknya. Balas HANYA query-nya, tanpa penjelasan.\n\nTranskrip: {text[:1500]}").strip().strip('"')
    print(f"clip{i:02d} query: {q}")
    # 2. cari berita (pakai hasil pre-fetched kalau ada, trigger ke hermes kalau gak)
    found = search_data.get(str(i)) or search_data.get(i)
    if not found:
        # fallback: tanya LLM langsung (pengetahuan model, tandai sebagai suggestion)
        found = []
    # 3. LLM rangkai caption
    refs = "\n".join(f"- {f['title']}: {f['url']}" for f in found[:3]) if found else "(tidak ada hasil search)"
    cap = llm(f"""Kamu social media manager. Klip podcast ini akan diposting ke TikTok.
Buat caption TikTok: 1-2 kalimat hook + 1-3 hashtag relevan bahasa Indonesia.
{'JANGAN sertakan link' if not found else 'Sertakan MAXIMAL 1 link berita paling relevan dari daftar ini, taruh di akhir:' if found else ''}
{refs}

Transkrip klip: {text[:1200]}

Balas HANYA caption-nya.""")
    results[str(i)] = {"clip": f"clip{i:02d}.mp4", "title": c["title"], "caption": cap, "query": q}
    print(f"  caption: {cap[:100]}")

json.dump(results, open("captions.json", "w"), ensure_ascii=False, indent=2)
print("\nsaved captions.json")
