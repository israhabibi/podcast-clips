"""Fetch public YouTube metadata and identify known podcast playlists."""

import json
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.admin_store import parse_youtube_url


PODCAST_PLAYLISTS = {
    "jelasin-dong": "PLkiSnq8pdz9TC0rHIiguTsgelilKeR3pw",
    "bocor-alus": "PLkiSnq8pdz9T3QQVbczz4XVgsHjjJK3vd",
    "tukang-kupas": "PLkiSnq8pdz9SBWNzd65VKlI4uzLv4_p41",
}
YOUTUBE_NS = {"yt": "http://www.youtube.com/xml/schemas/2015"}


class MetadataLookupError(Exception):
    """Raised when public YouTube metadata cannot be retrieved."""


def _match_title(title, author_name):
    searchable = f"{title} {author_name}".casefold()
    if "jelasin dong" in searchable:
        return "jelasin-dong"
    if "bocor alus" in searchable:
        return "bocor-alus"
    if "tukang kupas" in searchable:
        return "tukang-kupas"
    return None


def _playlist_contains_video(playlist_id, video_id):
    feed_url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
    try:
        request = Request(feed_url, headers={"User-Agent": "podcast-clips-admin/1.0"})
        with urlopen(request, timeout=5) as response:
            root = ET.fromstring(response.read())
        return any(
            element.text == video_id
            for element in root.findall(".//yt:videoId", YOUTUBE_NS)
        )
    except (OSError, URLError, ET.ParseError, ValueError):
        return False


def _match_playlist(video_id):
    matches = []
    with ThreadPoolExecutor(max_workers=len(PODCAST_PLAYLISTS)) as executor:
        futures = {
            executor.submit(_playlist_contains_video, playlist_id, video_id): podcast
            for podcast, playlist_id in PODCAST_PLAYLISTS.items()
        }
        for future in as_completed(futures):
            if future.result():
                matches.append(futures[future])
    return matches[0] if len(matches) == 1 else None


def get_youtube_video_metadata(url):
    """Return title, channel, ID, and a known podcast slug for a YouTube URL."""
    video_id = parse_youtube_url(url)
    if not video_id:
        raise ValueError("Enter a valid YouTube video URL.")

    video_url = f"https://www.youtube.com/watch?v={video_id}"
    endpoint = "https://www.youtube.com/oembed?" + urlencode({"url": video_url, "format": "json"})
    request = Request(endpoint, headers={"User-Agent": "podcast-clips-admin/1.0"})
    try:
        with urlopen(request, timeout=8) as response:
            metadata = json.loads(response.read())
    except (OSError, URLError, json.JSONDecodeError, ValueError) as exc:
        raise MetadataLookupError("Could not retrieve public metadata for this YouTube video.") from exc

    title = metadata.get("title")
    author_name = metadata.get("author_name", "")
    if not isinstance(title, str) or not title.strip():
        raise MetadataLookupError("YouTube did not return a public video title.")
    if not isinstance(author_name, str):
        author_name = ""

    podcast = _match_title(title, author_name) or _match_playlist(video_id)
    return {
        "video_id": video_id,
        "title": title.strip()[:200],
        "author_name": author_name.strip()[:200],
        "podcast": podcast,
    }