"""WSGI entry point for Gunicorn."""

from app import app
from app.routes import admin, clips, tiktok, youtube, threads  # noqa: F401 - register routes
