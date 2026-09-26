"""Podcast Clips — Flask App"""
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
import os

SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = os.urandom(24).hex()
    import sys as _sys
    print("WARNING: FLASK_SECRET_KEY not set. Session keys reset on every restart.", file=_sys.stderr)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

CLIPS_DIR = os.path.join(app.static_folder, 'clips')
TOKEN_DIR = os.path.dirname(os.path.abspath(__file__))
