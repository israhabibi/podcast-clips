"""Podcast Clips — entry point."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from app.routes import clips, youtube, tiktok

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
