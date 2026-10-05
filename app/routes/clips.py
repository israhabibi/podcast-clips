"""Clips gallery — YouTube Shorts-style per podcast."""
from flask import render_template, send_from_directory
import json, os
from app import app, CLIPS_DIR

ALLOWED_PODCASTS = {'jelasin-dong', 'bocor-alus', 'tukang-kupas', 'tempodotco'}
NOT_FOUND = object()

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


def _load_episode(podcast, episode):
    """Load one complete, renderable gallery episode or return NOT_FOUND."""
    ep_path = _safe_path(podcast, episode)
    if podcast not in ALLOWED_PODCASTS or not ep_path or not os.path.isdir(ep_path):
        return NOT_FOUND
    try:
        captions = _load_json(os.path.join(ep_path, 'captions.json'))
        clips_meta = _load_json(os.path.join(ep_path, 'clips.json'))
        episode_data_path = os.path.join(ep_path, 'episode_data.json')
        episode_data = _load_json(episode_data_path) if os.path.exists(episode_data_path) else {}
    except (OSError, json.JSONDecodeError):
        return NOT_FOUND
    if (not isinstance(captions, dict) or not captions or
            not isinstance(clips_meta, list) or not clips_meta or
            not isinstance(episode_data, dict)):
        return NOT_FOUND

    # clips.html indexes metadata and captions by their 1-based array position.
    # Require each referenced video to exist so broken episodes never render with
    # missing players or accidentally appear to be an empty different episode.
    for index, clip_meta in enumerate(clips_meta, 1):
        caption = captions.get(str(index))
        if not isinstance(clip_meta, dict) or not isinstance(caption, dict):
            return NOT_FOUND
        filename = caption.get('clip')
        if not isinstance(filename, str) or not filename or os.path.basename(filename) != filename:
            return NOT_FOUND
        if not os.path.isfile(os.path.join(ep_path, filename)):
            return NOT_FOUND
    if len(captions) != len(clips_meta):
        return NOT_FOUND

    raw_title = episode_data.get('episode_title', '')
    title = raw_title if isinstance(raw_title, str) and raw_title.strip() and raw_title != episode else (
        episode.rsplit('-', 1)[0] if '-' in episode else episode
    ).replace('-', ' ').replace('_', ' ').title()
    x_post = episode_data.get('x_post', {})
    if not isinstance(x_post, dict):
        x_post = {}
    summary = episode_data.get('episode_summary', '')
    if not isinstance(summary, str):
        summary = ''
    return {
        'podcast': podcast,
        'episode': episode,
        'episode_title': title,
        'date': episode.split('_')[0] if '_' in episode else '',
        'captions': captions,
        'clips_meta': clips_meta,
        'episode_summary': summary,
        'x_post': x_post,
    }

def get_episodes():
    """Scan clips dir for podcast/episode structure."""
    episodes = []
    if not os.path.exists(CLIPS_DIR):
        return episodes
    for podcast in sorted(os.listdir(CLIPS_DIR)):
        podcast_path = os.path.join(CLIPS_DIR, podcast)
        if not os.path.isdir(podcast_path) or podcast.startswith('.'):
            continue
        try:
            episode_dirs = sorted(os.listdir(podcast_path), reverse=True)
        except OSError:
            continue
        for ep in episode_dirs:
            ep_path = os.path.join(podcast_path, ep)
            if not os.path.isdir(ep_path):
                continue
            if '..' in ep or '/' in ep:
                continue
            episode_data = _load_episode(podcast, ep)
            if episode_data is not NOT_FOUND:
                episodes.append(episode_data)
    return episodes

@app.route('/')
def index():
    episodes = get_episodes()
    return render_template('index.html', episodes=episodes)

@app.route('/clips')
@app.route('/clips/<podcast>/<episode>')
def clips_view(podcast=None, episode=None):
    if podcast and episode:
        selected = _load_episode(podcast, episode)
        if selected is NOT_FOUND:
            return 'Episode not found', 404
        return render_template('clips.html', captions=selected['captions'], clips_meta=selected['clips_meta'],
                               podcast=podcast, episode=episode,
                               episode_title=selected['episode_title'],
                               episode_summary=selected['episode_summary'],
                               x_post=selected['x_post'])
    episodes = get_episodes()
    if episodes:
        ep = episodes[0]
        return render_template('clips.html', captions=ep['captions'],
                             clips_meta=ep['clips_meta'],
                             podcast=ep['podcast'], episode=ep['episode'],
                             episode_title=ep.get('episode_title', ep['episode']),
                             episode_summary=ep.get('episode_summary', ''),
                             x_post=ep.get('x_post', {}))
    return 'Belum ada klip.'

@app.route('/static/clips/<podcast>/<episode>/<filename>')
def serve_clip(podcast, episode, filename):
    # Path traversal protection
    if podcast not in ALLOWED_PODCASTS:
        return 'Podcast not found', 404
    for part in (podcast, episode, filename):
        if not part or '..' in part or '/' in part or os.path.isabs(part):
            return 'Invalid path', 400
    directory = _safe_path(podcast, episode)
    if not directory or not os.path.isdir(directory):
        return 'Clip not found', 404
    return send_from_directory(directory, filename)

@app.route('/terms')
def terms():
    return '<h1>Terms of Service</h1><p>Personal project. Content belongs to Tempo.co.</p><p>Contact: isra.habibi@gmail.com</p>'

@app.route('/privacy')
def privacy():
    return ('<h1>Privacy Policy</h1><p>The admin service stores login attempts, submitted '
            'YouTube links, and OAuth tokens on the server to operate the clipping workflow. '
            'These records are not exposed in the public gallery.</p>')
