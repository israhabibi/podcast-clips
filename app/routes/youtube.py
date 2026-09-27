"""YouTube OAuth routes."""
from flask import flash, redirect, request, session, url_for
import google_auth_oauthlib.flow
from app import app
from app.admin_security import admin_required, write_private_json
from app.config import YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REDIRECT, YOUTUBE_SCOPES, YOUTUBE_TOKEN_FILE
import json, secrets

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
        write_private_json(YOUTUBE_TOKEN_FILE, json.loads(flow.credentials.to_json()))
    except Exception as exc:
        app.logger.warning('YouTube OAuth failed: %s', type(exc).__name__)
        return 'YouTube account connection failed. Start again from Admin.', 502
    flash('YouTube account connected.', 'success')
    return redirect(url_for('admin_page'))
