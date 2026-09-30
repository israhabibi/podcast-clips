#!/usr/bin/env python3
"""Caption + link berita terkait per klip via LLM + web search.
LLM generates hook + hashtags only. All 3 news links are force-appended
programmatically after the LLM call as raw URLs (no header/emoji/title).
"""
import json, sys, os, urllib.request

KEY = os.environ.get("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY", "")
if not KEY:
    for line in open(os.path.expanduser("~/.hermes/.env")):
        if line.startswith("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY="):
            KEY = line.strip().split("=", 1)[1].strip().strip('"')

MODEL = sys.argv[1] if len(sys.argv) > 1 else "MiniMax-M2.7-highspeed"
clips = json.load(open("clips.json"))
segs = json.load(open("transcript.json"))

print(f"transkrip: {len(segs)} segmen, awal: {segs[0]['text'][:80]}")

search_data = json.load(open("search_results.json")) if os.path.exists("search_results.json") else {}

def llm(prompt):
    req = urllib.request.Request(
        "https://ai.sumopod.com/v1/chat/completions",
        data=json.dumps({"model": MODEL,
                         "messages": [{"role": "user", "content": prompt}],
                         "temperature": 0.3}).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=300))["choices"][0]["message"]["content"]

results = {}
for i, c in enumerate(clips, 1):
    t0, t1 = float(c["start"]), float(c["end"])
    text = " ".join(s["text"] for s in segs if s["end"] > t0 and s["start"] < t1)
    found = search_data.get(str(i)) or search_data.get(i) or []
    cap = llm(f"""Klip podcast ini tentang rokok ilegal & pita cukai di Indonesia.
Buat caption TikTok: 1-2 kalimat hook menarik + 1-3 hashtag bahasa Indonesia.
TOPIK KLIP INI: {c["title"]}
JANGAN tulis link apapun.

Transkrip: {text[:1200]}

Balas HANYA caption-nya, tanpa header/emoji.""")
    if found:
        for f in found:
            cap += f"\n{f['url']}"
    results[str(i)] = {"clip": f"clip{i:02d}.mp4", "title": c["title"], "caption": cap.strip()}
    print(f"clip{i:02d}: {c['title']}")
    print(f"  => {cap.strip()[:80]}")

json.dump(results, open("captions.json","w"), ensure_ascii=False, indent=2)
print("saved captions.json")
