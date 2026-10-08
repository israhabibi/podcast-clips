"""YouTube OAuth routes."""
from flask import flash, redirect, request, session, url_for
import google_auth_oauthlib.flow
from app import app
from app.admin_security import admin_required, write_private_json
from app.config import YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REDIRECT, YOUTUBE_SCOPES, YOUTUBE_TOKEN_FILE, YOUTUBE_EXPECTED_CHANNEL_ID
import json, secrets, requests as _req

CLIENT_CONFIG = {
    "web": {
        "client_id": YOUTUBE_CLIENT_ID,
        "client_secret": YOUTUBE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [YOUTUBE_REDIRECT]
    }
}

@app.route('/auth')
@admin_required()
def youtube_auth():
    if not YOUTUBE_CLIENT_ID or not YOUTUBE_CLIENT_SECRET:
        return 'YouTube OAuth is not configured.', 503
    if not YOUTUBE_EXPECTED_CHANNEL_ID:
        return 'Expected YouTube channel is not configured. Set YOUTUBE_EXPECTED_CHANNEL_ID first.', 503
    flow = google_auth_oauthlib.flow.Flow.from_client_config(CLIENT_CONFIG, scopes=YOUTUBE_SCOPES)
    flow.redirect_uri = YOUTUBE_REDIRECT
    auth_url, state = flow.authorization_url(prompt='consent', access_type='offline',
                                               include_granted_scopes='true')
    session['oauth_state'] = state
    session['code_verifier'] = flow.code_verifier
    return redirect(auth_url)

@app.route('/oauth')
@admin_required(callback=True)
def youtube_oauth_callback():
    stored_state = session.get('oauth_state')
    returned_state = request.args.get('state')
    verifier = session.get('code_verifier')
    code = request.args.get('code')
    if not stored_state or not returned_state or not verifier or not code or not secrets.compare_digest(stored_state, returned_state):
        return '❌ State mismatch. Possible CSRF attack.', 400
    session.pop('oauth_state', None)
    session.pop('code_verifier', None)
    flow = google_auth_oauthlib.flow.Flow.from_client_config(CLIENT_CONFIG, scopes=YOUTUBE_SCOPES,
                                                               state=stored_state)
    flow.code_verifier = verifier
    flow.redirect_uri = YOUTUBE_REDIRECT
    try:
        response_url = f"{YOUTUBE_REDIRECT}?{request.query_string.decode('ascii')}"
        flow.fetch_token(authorization_response=response_url)
        identity_response = _req.get(
            'https://www.googleapis.com/youtube/v3/channels',
            params={'part': 'id', 'mine': 'true'},
            headers={'Authorization': f'Bearer {flow.credentials.token}'},
            timeout=20,
        )
        identity_response.raise_for_status()
        items = identity_response.json().get('items', [])
        linked_channel_id = items[0].get('id') if items and isinstance(items[0], dict) else None
        if not linked_channel_id or not secrets.compare_digest(
            str(linked_channel_id), YOUTUBE_EXPECTED_CHANNEL_ID,
        ):
            return 'The authorized YouTube channel does not match the configured channel.', 403
        write_private_json(YOUTUBE_TOKEN_FILE, json.loads(flow.credentials.to_json()))
    except Exception as exc:
        app.logger.warning('YouTube OAuth failed: %s', type(exc).__name__)
        return 'YouTube account connection failed. Start again from Admin.', 502
    flash('YouTube account connected.', 'success')
    return redirect(url_for('admin_page'))
