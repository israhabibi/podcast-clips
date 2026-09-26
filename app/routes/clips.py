"""Clips gallery — YouTube Shorts-style per podcast."""
from flask import render_template, send_from_directory
import json, os
from app import app, CLIPS_DIR

ALLOWED_PODCASTS = {'jelasin-dong', 'bocor-alus', 'tukang-kupas'}

def _safe_path(*parts):
    """Ensure path components are safe (no traversal) and join them."""
    for p in parts:
        if not p or '..' in p or '/' in p or os.path.isabs(p):
            return None
    return os.path.join(CLIPS_DIR, *parts)

def _load_json(path):
    """Safely load a JSON file with context manager."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)

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
            if '..' in ep or '/' in ep:
                continue
            caps = _load_json(os.path.join(ep_path, 'captions.json'))
            clips_meta = _load_json(os.path.join(ep_path, 'clips.json'))
            episode_data = _load_json(os.path.join(ep_path, 'episode_data.json')) or {}
            if caps is None or clips_meta is None:
                continue
            ep_title = ep.split('_', 1)[1] if '_' in ep else ep
            ep_title = ep_title.replace('-', ' ').title()
            episodes.append({
                'podcast': podcast,
                'episode': ep,
                'episode_title': ep_title,
                'date': ep.split('_')[0] if '_' in ep else '',
                'captions': caps,
                'clips_meta': clips_meta,
                'episode_summary': episode_data.get('episode_summary', ''),
                'x_post': episode_data.get('x_post', {}),
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
        # Validate podcast slug
        if podcast not in ALLOWED_PODCASTS:
            return 'Podcast not found', 404
        ep_path = _safe_path(podcast, episode)
        if not ep_path or not os.path.isdir(ep_path):
            return 'Episode not found', 404
        caps = _load_json(os.path.join(ep_path, 'captions.json'))
        clips_meta = _load_json(os.path.join(ep_path, 'clips.json'))
        episode_data = _load_json(os.path.join(ep_path, 'episode_data.json')) or {}
        if caps and clips_meta:
            return render_template('clips.html', captions=caps, clips_meta=clips_meta,
                                   podcast=podcast, episode=episode,
                                   episode_summary=episode_data.get('episode_summary', ''),
                                   x_post=episode_data.get('x_post', {}))
    episodes = get_episodes()
    if episodes:
        ep = episodes[0]
        return render_template('clips.html', captions=ep['captions'],
                               clips_meta=ep['clips_meta'],
                               podcast=ep['podcast'], episode=ep['episode'],
                               episode_summary=ep.get('episode_summary', ''),
                               x_post=ep.get('x_post', {}))
    return 'Belum ada klip.'

@app.route('/static/clips/<podcast>/<episode>/<filename>')
def serve_clip(podcast, episode, filename):
    # Path traversal protection
    for part in (podcast, episode, filename):
        if not part or '..' in part or '/' in part or os.path.isabs(part):
            return 'Invalid path', 400
    return send_from_directory(os.path.join(CLIPS_DIR, podcast, episode), filename)

@app.route('/terms')
def terms():
    return '<h1>Terms of Service</h1><p>Personal project. Content belongs to Tempo.co.</p><p>Contact: isra.habibi@gmail.com</p>'

@app.route('/privacy')
def privacy():
    return '<h1>Privacy Policy</h1><p>No user data collected.</p>'
