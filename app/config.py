"""Konfigurasi OAuth & API keys."""
import os

# --- YouTube ---
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REDIRECT = os.getenv("YOUTUBE_REDIRECT", "https://clips.gcp.my.id/oauth")
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
YOUTUBE_TOKEN_FILE = os.getenv(
	"YOUTUBE_TOKEN_FILE", os.path.expanduser("~/podcast-clips/app/youtube_token.json")
)

# --- TikTok ---
TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
TIKTOK_REDIRECT = os.getenv("TIKTOK_REDIRECT", "https://clips.gcp.my.id/tiktok-oauth")
TIKTOK_SCOPES = ["video.upload"]
TIKTOK_TOKEN_FILE = os.getenv(
	"TIKTOK_TOKEN_FILE", os.path.expanduser("~/podcast-clips/app/tiktok_token.json")
)
