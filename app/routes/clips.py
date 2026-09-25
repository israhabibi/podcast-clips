"""Clips gallery — YouTube Shorts-style per podcast."""
from flask import render_template, send_from_directory
import json, os
from app import app, CLIPS_DIR

def get_episodes():
    """Scan clips dir for podcast/episode structure."""
    episodes = []
    if not os.path.exists(CLIPS_DIR):
        return episodes
    for podcast in sorted(os.listdir(CLIPS_DIR)):
        podcast_path = os.path.join(CLIPS_DIR, podcast)
        if not os.path.isdir(podcast_path) or podcast.startswith('.'):
            continue
        for ep in sorted(os.listdir(podcast_path), reverse=True):
            ep_path = os.path.join(podcast_path, ep)
            if not os.path.isdir(ep_path):
                continue
            caps_file = os.path.join(ep_path, 'captions.json')
            clips_file = os.path.join(ep_path, 'clips.json')
            if os.path.exists(caps_file) and os.path.exists(clips_file):
                caps = json.load(open(caps_file))
                clips_meta = json.load(open(clips_file))
                ep_title = ep.split('_', 1)[1] if '_' in ep else ep
                ep_title = ep_title.replace('-', ' ').title()
                episodes.append({
                    'podcast': podcast,
                    'episode': ep,
                    'episode_title': ep_title,
                    'date': ep.split('_')[0] if '_' in ep else '',
                    'captions': caps,
                    'clips_meta': clips_meta,
                })
    return episodes

@app.route('/')
def index():
    episodes = get_episodes()
    return render_template('index.html', episodes=episodes)

@app.route('/clips')
@app.route('/clips/<podcast>/<episode>')
def clips_view(podcast=None, episode=None):
    if podcast and episode:
        ep_path = os.path.join(CLIPS_DIR, podcast, episode)
        caps_file = os.path.join(ep_path, 'captions.json')
        clips_file = os.path.join(ep_path, 'clips.json')
        if os.path.exists(caps_file) and os.path.exists(clips_file):
            caps = json.load(open(caps_file))
            clips_meta = json.load(open(clips_file))
            return render_template('clips.html', captions=caps, clips_meta=clips_meta,
                                   podcast=podcast, episode=episode)
    episodes = get_episodes()
    if episodes:
        ep = episodes[0]
        return render_template('clips.html', captions=ep['captions'],
                               clips_meta=ep['clips_meta'],
                               podcast=ep['podcast'], episode=ep['episode'])
    return 'Belum ada klip.'

@app.route('/static/clips/<podcast>/<episode>/<filename>')
def serve_clip(podcast, episode, filename):
    return send_from_directory(os.path.join(CLIPS_DIR, podcast, episode), filename)

@app.route('/terms')
def terms():
    return '<h1>Terms of Service</h1><p>Personal project. Content belongs to Tempo.co.</p><p>Contact: isra.habibi@gmail.com</p>'

@app.route('/privacy')
def privacy():
    return '<h1>Privacy Policy</h1><p>No user data collected.</p>'
