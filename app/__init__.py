"""Podcast Clips — Flask App"""
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
import os

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

CLIPS_DIR = os.path.join(app.static_folder, 'clips')
TOKEN_DIR = os.path.dirname(os.path.abspath(__file__))
