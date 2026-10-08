"""TikTok OAuth routes."""
from flask import flash, redirect, request, session, url_for
import secrets, hashlib, base64, urllib.parse, requests as _req
from app import app
from app.admin_security import admin_required, write_private_json
from app.config import TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REDIRECT, TIKTOK_TOKEN_FILE, TIKTOK_EXPECTED_OPEN_ID

@app.route('/tiktok-auth')
@admin_required()
def tiktok_auth():
    if not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET:
        return 'TikTok OAuth is not configured.', 503
    if not TIKTOK_EXPECTED_OPEN_ID:
        return 'Expected TikTok account is not configured. Set TIKTOK_EXPECTED_OPEN_ID first.', 503
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    session['tiktok_state'] = state
    session['tiktok_verifier'] = verifier
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    params = {
        'client_key': TIKTOK_CLIENT_KEY,
        'scope': 'video.upload',
        'redirect_uri': TIKTOK_REDIRECT,
        'state': state,
        'response_type': 'code',
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    url = f"https://www.tiktok.com/v2/auth/authorize/?{urllib.parse.urlencode(params)}"
    return redirect(url)

@app.route('/tiktok-oauth')
@admin_required(callback=True)
def tiktok_oauth_callback():
    stored_state = session.get('tiktok_state')
    returned_state = request.args.get('state')
    verifier = session.get('tiktok_verifier')
    code = request.args.get('code')
    if not stored_state or not returned_state or not verifier or not code or not secrets.compare_digest(stored_state, returned_state):
        return '❌ State mismatch', 400
    session.pop('tiktok_state', None)
    session.pop('tiktok_verifier', None)
    try:
        r = _req.post('https://open.tiktokapis.com/v2/oauth/token/', data={
            'client_key': TIKTOK_CLIENT_KEY,
            'client_secret': TIKTOK_CLIENT_SECRET,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': TIKTOK_REDIRECT,
            'code_verifier': verifier,
        }, timeout=20)
    except _req.RequestException:
        return 'TikTok account connection failed. Start again from Admin.', 502
    if r.status_code != 200:
        return 'TikTok account connection failed. Start again from Admin.', 502
    try:
        token_data = r.json()
        if not isinstance(token_data, dict) or not token_data.get('access_token'):
            return 'TikTok did not provide an access token.', 502
        linked_open_id = token_data.get('open_id')
        if not linked_open_id or not secrets.compare_digest(
            str(linked_open_id), TIKTOK_EXPECTED_OPEN_ID,
        ):
            return 'The authorized TikTok account does not match the configured account.', 403
        write_private_json(TIKTOK_TOKEN_FILE, token_data)
    except (ValueError, OSError):
        return 'TikTok account connection failed. Start again from Admin.', 502
    flash('TikTok account connected.', 'success')
    return redirect(url_for('admin_page'))
