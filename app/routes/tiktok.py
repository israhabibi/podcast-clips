"""TikTok OAuth routes."""
from flask import redirect, request, session
import secrets, hashlib, base64, urllib.parse, requests as _req, json, os
from app import app
from app.config import TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REDIRECT, TIKTOK_TOKEN_FILE

@app.route('/tiktok-auth')
def tiktok_auth():
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
def tiktok_oauth_callback():
    if request.args.get('state') != session.get('tiktok_state'):
        return '❌ State mismatch', 400
    code = request.args.get('code')
    if not code:
        return f'❌ No code: {request.args}', 400
    r = _req.post('https://open.tiktokapis.com/v2/oauth/token/', data={
        'client_key': TIKTOK_CLIENT_KEY,
        'client_secret': TIKTOK_CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': TIKTOK_REDIRECT,
        'code_verifier': session.get('tiktok_verifier'),
    })
    if r.status_code != 200:
        return f'❌ Token error: {r.text}', 400
    token_data = r.json()
    with open(TIKTOK_TOKEN_FILE, 'w') as f:
        json.dump(token_data, f, indent=2)
    os.chmod(TIKTOK_TOKEN_FILE, 0o600)
    return '✅ TikTok OAuth berhasil! Token tersimpan. Tutup tab ini.'
