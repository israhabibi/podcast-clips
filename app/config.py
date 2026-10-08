"""Konfigurasi OAuth & API keys."""
import os
from pathlib import Path

TOKEN_DIR = Path(__file__).resolve().parent

# --- YouTube ---
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REDIRECT = os.getenv("YOUTUBE_REDIRECT", "https://clips.gcp.my.id/oauth")
YOUTUBE_EXPECTED_CHANNEL_ID = os.getenv("YOUTUBE_EXPECTED_CHANNEL_ID", "")
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
YOUTUBE_TOKEN_FILE = os.getenv(
	"YOUTUBE_TOKEN_FILE", str(TOKEN_DIR / "youtube_token.json")
)

# --- TikTok ---
TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
TIKTOK_REDIRECT = os.getenv("TIKTOK_REDIRECT", "https://clips.gcp.my.id/tiktok-oauth")
TIKTOK_EXPECTED_OPEN_ID = os.getenv("TIKTOK_EXPECTED_OPEN_ID", "")
TIKTOK_SCOPES = ["video.upload"]
TIKTOK_TOKEN_FILE = os.getenv(
	"TIKTOK_TOKEN_FILE", str(TOKEN_DIR / "tiktok_token.json")
)

# --- Meta Threads ---
THREADS_APP_ID = os.getenv("THREADS_APP_ID", "")
THREADS_APP_SECRET = os.getenv("THREADS_APP_SECRET", "")
THREADS_REDIRECT = os.getenv("THREADS_REDIRECT", "https://clips.gcp.my.id/threads-oauth")
THREADS_TOKEN_FILE = os.getenv("THREADS_TOKEN_FILE", str(TOKEN_DIR / "threads_token.json"))
THREADS_PUBLIC_BASE_URL = os.getenv("THREADS_PUBLIC_BASE_URL", "https://clips.gcp.my.id")
# Optional existing user-token configuration, compatible with techbro-pipeline.
THREADS_ENV_FILE = os.getenv("THREADS_ENV_FILE", "")
