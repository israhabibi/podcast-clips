"""Small client for Meta's documented Threads OAuth and video publishing API."""

import json
import os
import shlex
import time
from pathlib import Path

import requests

from app import config
from app.admin_security import write_private_json

API = "https://graph.threads.net/v1.0"
AUTH_API = "https://graph.threads.net"


class ThreadsError(RuntimeError):
    """A provider error safe to display without tokens or response bodies."""

    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def request(method, path, *, token=None, data=None, params=None, oauth=False):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = requests.request(method, (AUTH_API if oauth else API) + path,
                                    headers=headers, data=data, params=params, timeout=30, allow_redirects=False)
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise ThreadsError("Threads tidak memberi respons yang valid. Coba lagi.", retryable=True) from None
    if not 200 <= response.status_code < 300 or not isinstance(payload, dict) or payload.get("error"):
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and error.get("code") == 190:
            raise ThreadsError("Sesi Threads kedaluwarsa. Hubungkan ulang akun Threads.")
        raise ThreadsError(f"Permintaan Threads gagal (HTTP {response.status_code}).",
                           retryable=response.status_code == 429 or response.status_code >= 500)
    return payload


def _numeric_id(value):
    value = str(value or "")
    if not value.isdecimal():
        raise ThreadsError("Threads tidak mengembalikan ID yang valid.")
    return value


def profile(token):
    result = request("GET", "/me", token=token, params={"fields": "id,username"})
    return {"user_id": _numeric_id(result.get("id")), "username": str(result.get("username") or "")}


def connect(code):
    short = request("POST", "/oauth/access_token", oauth=True, data={
        "client_id": config.THREADS_APP_ID, "client_secret": config.THREADS_APP_SECRET,
        "grant_type": "authorization_code", "redirect_uri": config.THREADS_REDIRECT, "code": code,
    })
    token = short.get("access_token")
    user_id = _numeric_id(short.get("user_id"))
    if not isinstance(token, str) or not token:
        raise ThreadsError("Threads tidak mengembalikan token akun.")
    long = request("GET", "/access_token", oauth=True, token=token, params={
        "grant_type": "th_exchange_token", "client_secret": config.THREADS_APP_SECRET,
    })
    long_token = long.get("access_token")
    lifetime = long.get("expires_in")
    if not isinstance(long_token, str) or not long_token or not isinstance(lifetime, (int, float)) or lifetime <= 0:
        raise ThreadsError("Threads tidak mengembalikan sesi jangka panjang yang valid.")
    account = profile(long_token)
    if account["user_id"] != user_id:
        raise ThreadsError("Identitas akun Threads tidak cocok.")
    now = time.time()
    credentials = {**account, "access_token": long_token, "issued_at": now, "expires_at": now + lifetime}
    write_private_json(config.THREADS_TOKEN_FILE, credentials)
    return account


def load_credentials():
    # Match techbro's user-token mode. Read only the selected credentials;
    # don't import its pipeline flags, data paths, or other API keys.
    shared = shared_settings()
    token, user_id = shared.get("THREADS_ACCESS_TOKEN"), shared.get("THREADS_USER_ID")
    if token or user_id:
        if not token or any(char.isspace() for char in token) or not user_id:
            raise ThreadsError("Konfigurasi Threads memerlukan user token dan THREADS_USER_ID yang cocok.")
        return {"access_token": token, "user_id": _numeric_id(user_id), "username": "",
                "expires_at": None, "source": "environment"}
    try:
        data = json.loads(Path(config.THREADS_TOKEN_FILE).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("access_token"), str) or not data["access_token"]:
            raise ValueError()
        data["user_id"] = _numeric_id(data.get("user_id"))
        if not isinstance(data.get("expires_at"), (int, float)):
            raise ValueError()
        return data
    except (OSError, ValueError, ThreadsError):
        raise ThreadsError("Hubungkan akun Threads dari halaman Admin.") from None


def account_info():
    """Read-only status; opening Admin never refreshes or deletes credentials."""
    try:
        data = load_credentials()
    except ThreadsError:
        return {"connected": False, "username": "", "expired": False}
    expired = data["expires_at"] is not None and data["expires_at"] <= time.time()
    return {"connected": not expired, "username": data.get("username", ""), "expired": expired,
            "user_id": data["user_id"], "shared": data.get("source") == "environment"}


def shared_settings():
    names = {"THREADS_ACCESS_TOKEN", "THREADS_USER_ID", "THREADS_APP_ID"}
    values = {}
    path = config.THREADS_ENV_FILE
    if path:
        try:
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if line.lstrip().startswith("export "):
                    line = line.lstrip()[7:]
                key, separator, raw = line.partition("=")
                if separator and key.strip() in names:
                    parts = shlex.split(raw, comments=True)
                    values[key.strip()] = " ".join(parts)
        except (OSError, ValueError):
            raise ThreadsError("File konfigurasi Threads tidak bisa dibaca.") from None
    for name in names:
        if os.environ.get(name):
            values[name] = os.environ[name]
    return values


def valid_credentials():
    data = load_credentials()
    now = time.time()
    if data.get("source") == "environment":
        if profile(data["access_token"])["user_id"] != data["user_id"]:
            raise ThreadsError("Token milik akun berbeda dari THREADS_USER_ID pada konfigurasi Threads.")
        return data  # Refresh belongs to the shared token's existing owner.
    if data["expires_at"] <= now:
        raise ThreadsError("Sesi Threads kedaluwarsa. Hubungkan ulang akun Threads.")
    if data["expires_at"] - now < 7 * 86400 and now - data.get("issued_at", now) >= 86400:
        refreshed = request("GET", "/refresh_access_token", oauth=True, token=data["access_token"],
                            params={"grant_type": "th_refresh_token"})
        token, lifetime = refreshed.get("access_token"), refreshed.get("expires_in")
        if not isinstance(token, str) or not token or not isinstance(lifetime, (int, float)) or lifetime <= 0:
            raise ThreadsError("Perpanjangan sesi Threads gagal. Hubungkan ulang akun.")
        data.update(access_token=token, issued_at=now, expires_at=now + lifetime)
        write_private_json(config.THREADS_TOKEN_FILE, data)
    if profile(data["access_token"])["user_id"] != str(data["user_id"]):
        raise ThreadsError("Identitas akun Threads berubah. Hubungkan ulang akun.")
    return data


def create_video(token, user_id, video_url, text):
    return _numeric_id(request("POST", f"/{_numeric_id(user_id)}/threads", token=token,
                              data={"media_type": "VIDEO", "video_url": video_url, "text": text}).get("id"))


def container_status(token, container_id):
    return request("GET", f"/{_numeric_id(container_id)}", token=token,
                   params={"fields": "id,status"}).get("status")


def publish(token, user_id, container_id):
    return _numeric_id(request("POST", f"/{_numeric_id(user_id)}/threads_publish", token=token,
                              data={"creation_id": _numeric_id(container_id)}).get("id"))


def permalink(token, post_id):
    result = request("GET", f"/{_numeric_id(post_id)}", token=token, params={"fields": "permalink"})
    return result.get("permalink", "")
