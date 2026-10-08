"""Authenticated Threads account linking and TOP 5 automation settings."""

import secrets
from urllib.parse import urlencode

from flask import flash, redirect, request, session, url_for

from app import app, config
from app.admin_security import admin_required, check_csrf
from app.admin_store import set_setting
from app import threads_client as client


@app.route('/threads-auth')
@admin_required()
def threads_auth():
    if not config.THREADS_APP_ID or not config.THREADS_APP_SECRET:
        return 'Konfigurasi Threads App ID dan App Secret dahulu.', 503
    state = secrets.token_urlsafe(32)
    session['threads_state'] = state
    return redirect('https://threads.net/oauth/authorize?' + urlencode({
        'client_id': config.THREADS_APP_ID, 'redirect_uri': config.THREADS_REDIRECT,
        'scope': 'threads_basic,threads_content_publish', 'response_type': 'code', 'state': state,
    }))


@app.route('/threads-oauth')
@admin_required(callback=True)
def threads_oauth():
    stored, returned, code = session.get('threads_state'), request.args.get('state'), request.args.get('code')
    if not stored or not returned or not secrets.compare_digest(stored, returned):
        return 'State OAuth Threads tidak cocok. Mulai ulang dari Admin.', 400
    session.pop('threads_state', None)
    if not code or request.args.get('error'):
        return 'Otorisasi Threads dibatalkan. Mulai ulang dari Admin.', 400
    try:
        account = client.connect(code)
    except (client.ThreadsError, OSError):
        return 'Koneksi Threads gagal. Periksa konfigurasi aplikasi dan mulai ulang dari Admin.', 502
    flash(f"Threads terhubung: @{account['username']}. Video TOP 5 dapat diposting otomatis setelah upload YouTube.", 'success')
    return redirect(url_for('admin_page'))


@app.route('/admin/threads/settings', methods=['POST'])
@admin_required()
def threads_settings():
    if not check_csrf():
        return 'Invalid form token.', 400
    enabled = request.form.get('auto_post') == '1'
    set_setting('threads_top5_auto_post', '1' if enabled else '0')
    flash('Auto-post video TOP 5 ke Threads ' + ('diaktifkan.' if enabled else 'dinonaktifkan.'), 'success')
    return redirect(url_for('admin_page'))
