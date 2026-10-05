"""WSGI entry point for Gunicorn."""

from app import app
from app.routes import admin, clips, tiktok, youtube  # noqa: F401 - register routes
