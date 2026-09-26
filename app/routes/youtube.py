"""YouTube OAuth routes."""
from flask import redirect, request, session
import google_auth_oauthlib.flow
from app import app
from app.config import YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REDIRECT, YOUTUBE_SCOPES, YOUTUBE_TOKEN_FILE
import json, os

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
def youtube_auth():
    flow = google_auth_oauthlib.flow.Flow.from_client_config(CLIENT_CONFIG, scopes=YOUTUBE_SCOPES)
    flow.redirect_uri = YOUTUBE_REDIRECT
    auth_url, state = flow.authorization_url(prompt='consent', access_type='offline',
                                               include_granted_scopes='true')
    session['oauth_state'] = state
    session['code_verifier'] = flow.code_verifier
    return redirect(auth_url)

@app.route('/oauth')
def youtube_oauth_callback():
    # Validate state before proceeding (CSRF protection)
    stored_state = session.get('oauth_state')
    returned_state = request.args.get('state')
    if not stored_state or not returned_state or stored_state != returned_state:
        return '❌ State mismatch. Possible CSRF attack.', 400

    flow = google_auth_oauthlib.flow.Flow.from_client_config(CLIENT_CONFIG, scopes=YOUTUBE_SCOPES,
                                                               state=stored_state)
    flow.code_verifier = session.get('code_verifier')
    flow.redirect_uri = YOUTUBE_REDIRECT
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    with open(YOUTUBE_TOKEN_FILE, 'w') as f:
        f.write(creds.to_json())
    os.chmod(YOUTUBE_TOKEN_FILE, 0o600)
    return '✅ YouTube OAuth berhasil! Token tersimpan. Tutup tab ini.'
