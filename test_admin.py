"""Behavior tests for the admin login and manual YouTube queue."""

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from werkzeug.security import generate_password_hash


class AdminPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["FLASK_SECRET_KEY"] = "admin-test-session-secret"
        os.environ["ADMIN_PASSWORD_HASH"] = generate_password_hash("correct horse battery staple")
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        from app import app
        from app.routes import admin, clips, tiktok, youtube  # noqa: F401 - registers routes
        app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        cls.app = app

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_override = patch.dict(os.environ, {
            "ADMIN_DB_PATH": str(Path(self.tempdir.name) / "admin.sqlite3"),
        })
        self.database_override.start()
        self.client = self.app.test_client()

    def tearDown(self):
        self.database_override.stop()
        self.tempdir.cleanup()

    def csrf(self):
        with self.client.session_transaction() as session:
            return session["admin_csrf"]

    def login(self):
        self.client.get("/admin/login")
        return self.client.post("/admin/login", data={
            "csrf_token": self.csrf(),
            "password": "correct horse battery staple",
        })

    def test_anonymous_cannot_use_admin_or_oauth(self):
        for path in ("/admin", "/auth", "/tiktok-auth"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/admin/login", response.headers["Location"])
        for path in ("/oauth?code=x&state=y", "/tiktok-oauth?code=x&state=y"):
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_login_needs_csrf_and_correct_password(self):
        self.client.get("/admin/login")
        self.assertEqual(self.client.post("/admin/login", data={
            "password": "correct horse battery staple",
        }).status_code, 400)
        self.assertEqual(self.client.post("/admin/login", data={
            "csrf_token": self.csrf(), "password": "incorrect",
        }).status_code, 200)
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.client.get("/admin").status_code, 200)

    def test_add_link_validates_and_deduplicates(self):
        from app.admin_store import list_submissions, set_submission_status
        self.login()
        data = {
            "csrf_token": self.csrf(),
            "url": "https://youtu.be/abcdefghijk?t=10",
            "podcast": "jelasin-dong",
            "title": "Test episode",
        }
        self.assertEqual(self.client.post("/admin/youtube-links", data=data).status_code, 302)
        rows = list_submissions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(rows[0]["status"], "pending")
        self.client.post("/admin/youtube-links", data=data)
        self.assertEqual(len(list_submissions()), 1)
        set_submission_status("abcdefghijk", "completed")
        self.assertEqual(list_submissions(status="pending"), [])
        self.assertEqual(list_submissions(status="completed")[0]["video_id"], "abcdefghijk")
        data["url"] = "https://youtube.com.evil.example/watch?v=abcdefghijk"
        self.client.post("/admin/youtube-links", data=data)
        self.assertEqual(len(list_submissions()), 1)

    def test_submission_requires_csrf_and_login(self):
        data = {"url": "https://youtu.be/abcdefghijk", "podcast": "jelasin-dong"}
        self.assertEqual(self.client.post("/admin/youtube-links", data=data).status_code, 302)
        self.login()
        self.assertEqual(self.client.post("/admin/youtube-links", data=data).status_code, 400)

    def test_tiktok_callback_rejects_missing_state_without_token_request(self):
        self.login()
        with patch("app.routes.tiktok._req.post") as token_request:
            response = self.client.get("/tiktok-oauth?code=example")
        self.assertEqual(response.status_code, 400)
        token_request.assert_not_called()

    def test_youtube_callback_rejects_missing_state_without_token_request(self):
        self.login()
        with patch("app.routes.youtube.google_auth_oauthlib.flow.Flow.from_client_config") as flow:
            response = self.client.get("/oauth?code=example")
        self.assertEqual(response.status_code, 400)
        flow.assert_not_called()

    def test_tiktok_callback_saves_token_once(self):
        self.login()
        with self.client.session_transaction() as session:
            session["tiktok_state"] = "expected-state"
            session["tiktok_verifier"] = "expected-verifier"
        token_path = Path(self.tempdir.name) / "tiktok_token.json"
        token_response = Mock(status_code=200)
        token_response.json.return_value = {"access_token": "test-token", "open_id": "test-account"}
        with patch("app.routes.tiktok.TIKTOK_TOKEN_FILE", str(token_path)), \
             patch("app.routes.tiktok._req.post", return_value=token_response) as token_request:
            first = self.client.get("/tiktok-oauth?code=test-code&state=expected-state")
            second = self.client.get("/tiktok-oauth?code=test-code&state=expected-state")
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(json.loads(token_path.read_text())["access_token"], "test-token")
        token_request.assert_called_once()

    def test_youtube_callback_saves_token_once(self):
        self.login()
        with self.client.session_transaction() as session:
            session["oauth_state"] = "expected-state"
            session["code_verifier"] = "expected-verifier"
        token_path = Path(self.tempdir.name) / "youtube_token.json"
        flow = Mock()
        flow.credentials.to_json.return_value = '{"token":"test-token"}'
        with patch("app.routes.youtube.YOUTUBE_TOKEN_FILE", str(token_path)), \
             patch("app.routes.youtube.google_auth_oauthlib.flow.Flow.from_client_config", return_value=flow):
            first = self.client.get("/oauth?code=test-code&state=expected-state")
            second = self.client.get("/oauth?code=test-code&state=expected-state")
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(json.loads(token_path.read_text())["token"], "test-token")
        flow.fetch_token.assert_called_once()

    def test_five_failed_logins_are_throttled(self):
        self.client.get("/admin/login")
        for _ in range(5):
            self.client.post("/admin/login", data={
                "csrf_token": self.csrf(), "password": "wrong",
            })
        response = self.client.post("/admin/login", data={
            "csrf_token": self.csrf(), "password": "correct horse battery staple",
        })
        self.assertEqual(response.status_code, 429)

    def test_password_change_invalidates_existing_session(self):
        self.login()
        self.assertEqual(self.client.get("/admin").status_code, 200)
        with patch.dict(os.environ, {"ADMIN_PASSWORD_HASH": generate_password_hash("new password")}):
            response = self.client.get("/admin")
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
