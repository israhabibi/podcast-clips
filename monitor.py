#!/usr/bin/env python3
"""Monitor Tempo podcast playlists for new episodes."""
import urllib.request, xml.etree.ElementTree as ET, os

PLAYLISTS = {
    "jelasin-dong": "PLkiSnq8pdz9TC0rHIiguTsgelilKeR3pw",
    "bocor-alus": "PLkiSnq8pdz9T3QQVbczz4XVgsHjjJK3vd",
    "tukang-kupas": "PLkiSnq8pdz9SBWNzd65VKlI4uzLv4_p41",
}

STATE_FILE = os.path.expanduser("~/.hermes/cache/scratch/tempo_podcast_processed.txt")

processed = set()
if os.path.exists(STATE_FILE):
    with open(STATE_FILE) as f:
        processed = set(line.strip() for line in f if line.strip())

ns = {"yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/",
      "atom": "http://www.w3.org/2005/Atom"}

new_episodes = []
for show, pl_id in PLAYLISTS.items():
    try:
        tree = ET.parse(urllib.request.urlopen(
            f"https://www.youtube.com/feeds/videos.xml?playlist_id={pl_id}", timeout=15))
        for entry in tree.findall("atom:entry", ns):
            vid = entry.find("yt:videoId", ns)
            title = entry.find("atom:title", ns)
            pub = entry.find("atom:published", ns)
            if vid is not None and vid.text not in processed:
                new_episodes.append((pub.text or "", vid.text, title.text or "", show))
    except Exception as e:
        print(f"ERROR:{show}:{e}")
        continue

new_episodes.sort(reverse=True)

if new_episodes:
    for pub, vid, title, show in new_episodes:
        print(f"NEW:{show}:{vid}:{title[:80]}")
    with open(STATE_FILE, "a") as f:
        for _, vid, _, _ in new_episodes:
            f.write(f"{vid}\n")
else:
    print("NO_NEW")
