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
        metadata_response = self.client.get("/admin/youtube-metadata?url=https://youtu.be/abcdefghijk")
        self.assertEqual(metadata_response.status_code, 302)

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

    def test_admin_metadata_endpoint_returns_public_video_details(self):
        self.login()
        expected = {
            "video_id": "abcdefghijk",
            "title": "Bocor Alus Politik: Test Episode",
            "author_name": "Tempo",
            "podcast": "bocor-alus",
        }
        with patch("app.routes.admin.get_youtube_video_metadata", return_value=expected) as lookup:
            response = self.client.get("/admin/youtube-metadata?url=https://youtu.be/abcdefghijk")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), expected)
        lookup.assert_called_once_with("https://youtu.be/abcdefghijk")

    def test_metadata_lookup_matches_podcast_title_without_playlist_requests(self):
        from app.youtube_metadata import get_youtube_video_metadata

        payload = json.dumps({
            "title": "Bocor Alus Politik: Test Episode",
            "author_name": "Tempo",
        }).encode()
        with patch("app.youtube_metadata.urlopen") as open_url:
            open_url.return_value.__enter__.return_value.read.return_value = payload
            metadata = get_youtube_video_metadata("https://youtu.be/abcdefghijk")
        self.assertEqual(metadata["title"], "Bocor Alus Politik: Test Episode")
        self.assertEqual(metadata["podcast"], "bocor-alus")
        open_url.assert_called_once()

    def test_metadata_lookup_falls_back_to_known_playlist(self):
        from app.youtube_metadata import get_youtube_video_metadata

        payload = json.dumps({"title": "Episode terbaru", "author_name": "Tempo"}).encode()
        with patch("app.youtube_metadata.urlopen") as open_url:
            open_url.return_value.__enter__.return_value.read.return_value = payload
            with patch("app.youtube_metadata._match_playlist", return_value="tukang-kupas") as playlist_lookup:
                metadata = get_youtube_video_metadata("https://youtu.be/abcdefghijk")
        self.assertEqual(metadata["podcast"], "tukang-kupas")
        playlist_lookup.assert_called_once_with("abcdefghijk")

    def test_admin_form_has_metadata_autofill_controls(self):
        self.login()
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"youtube-lookup-status", response.data)
        self.assertIn(b"/admin/youtube-metadata?url=", response.data)

    def test_admin_jobs_reports_current_transcription_stage(self):
        from app.admin_store import add_submission, set_submission_status

        self.login()
        video_id = add_submission("https://youtu.be/abcdefghijk", "bocor-alus", "Test episode")
        set_submission_status(video_id, "in_progress")
        log_dir = Path(self.tempdir.name) / "jobs"
        episode_dir = Path("/tmp/podcast-clips") / f"{video_id}-test-progress"
        episode_dir.mkdir(parents=True, exist_ok=True)
        (episode_dir / "source.mp4").touch()
        (log_dir / f"{video_id}.log").parent.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{video_id}.log").write_text(
            f"EPISODE_DIR={episode_dir}\n=== download ===\nDownload finished\n",
            encoding="utf-8",
        )
        try:
            with patch.dict(os.environ, {"ADMIN_JOB_LOG_DIR": str(log_dir)}):
                response = self.client.get("/admin/jobs")
            self.assertEqual(response.status_code, 200)
            job = response.get_json()["jobs"][0]
            self.assertEqual(job["label"], "Berjalan")
            self.assertEqual(job["stage"], "Mentranskripsikan audio")
            self.assertEqual(job["percent"], 25)
        finally:
            (episode_dir / "source.mp4").unlink(missing_ok=True)
            episode_dir.rmdir()

    def test_anonymous_cannot_read_job_progress(self):
        self.assertEqual(self.client.get("/admin/jobs").status_code, 302)

    def test_compilation_build_requires_admin_and_csrf(self):
        payload = {"episode_id": "episode", "items": []}
        self.assertEqual(self.client.post("/admin/compilation/build", json=payload).status_code, 302)
        self.login()
        self.assertEqual(self.client.post("/admin/compilation/build", json=payload).status_code, 400)

    def test_compilation_build_writes_config_and_starts_worker(self):
        import app.routes.admin as admin_routes

        self.login()
        work_root = Path(self.tempdir.name) / "work"
        workdir = work_root / "episode-one"
        workdir.mkdir(parents=True)
        (workdir / "source.mp4").write_bytes(b"source")
        (workdir / "transcript.json").write_text("[]", encoding="utf-8")
        payload = {
            "csrf_token": self.csrf(),
            "episode_id": "episode-one",
            "header_l1": "TOP 5 MOMEN",
            "header_l2": "BOCOR ALUS",
            "subheader": "TOPIK",
            "items": [
                {"label": f"Moment {index}", "start": index * 10, "end": index * 10 + 8}
                for index in range(5)
            ],
        }
        with patch.object(admin_routes, "WORK_ROOT", work_root), patch.object(admin_routes.subprocess, "Popen") as worker:
            response = self.client.post("/admin/compilation/build", json=payload)
        self.assertEqual(response.status_code, 200)
        config_files = list(workdir.glob(".compilation-*.json"))
        self.assertEqual(len(config_files), 1)
        config = json.loads(config_files[0].read_text())
        self.assertEqual(len(config["items"]), 5)
        worker.assert_called_once()

    def test_compilation_preview_serves_built_video(self):
        import app.routes.admin as admin_routes

        self.login()
        work_root = Path(self.tempdir.name) / "work"
        output = work_root / "episode-one" / "clips"
        output.mkdir(parents=True)
        (work_root / "episode-one" / "source.mp4").write_bytes(b"source")
        (work_root / "episode-one" / "transcript.json").write_text("[]", encoding="utf-8")
        (output / "top5_compilation.mp4").write_bytes(b"fake mp4")
        with patch.object(admin_routes, "WORK_ROOT", work_root):
            response = self.client.get("/admin/compilation/preview/episode-one")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "video/mp4")

    def test_compilation_segments_returns_transcript_candidates(self):
        import app.routes.admin as admin_routes

        self.login()
        work_root = Path(self.tempdir.name) / "work"
        workdir = work_root / "episode-one"
        workdir.mkdir(parents=True)
        (workdir / "source.mp4").write_bytes(b"source")
        (workdir / "transcript.json").write_text(json.dumps([
            {"start": 1, "end": 4, "text": "Hook pertama"},
        ]), encoding="utf-8")
        with patch.object(admin_routes, "WORK_ROOT", work_root):
            response = self.client.get("/admin/compilation/segments/episode-one")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["segments"][0]["text"], "Hook pertama")

    def test_admin_page_has_direct_compilation_preview_link(self):
        self.login()
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"compilation-preview-link", response.data)
        self.assertIn(b"Buka di tab baru", response.data)

    def test_compilation_moments_uses_mocked_llm_and_filters_candidates(self):
        import app.routes.admin as admin_routes

        self.login()
        work_root = Path(self.tempdir.name) / "work"
        workdir = work_root / "episode-one"
        workdir.mkdir(parents=True)
        (workdir / "source.mp4").write_bytes(b"source")
        (workdir / "transcript.json").write_text(json.dumps([
            {"start": 0, "end": 40, "text": "Momen pertama"},
        ]), encoding="utf-8")
        llm_payload = json.dumps({"choices": [{"message": {"content": json.dumps([
            {"start": 0, "end": 40, "title": "Valid", "reason": "Payoff"},
            {"start": 1, "end": 10, "title": "Too short", "reason": "Skip"},
        ])}}]}).encode()
        response_mock = Mock()
        response_mock.__enter__ = Mock(return_value=response_mock)
        response_mock.__exit__ = Mock(return_value=False)
        response_mock.read.return_value = llm_payload
        with patch.object(admin_routes, "WORK_ROOT", work_root), \
             patch.dict(os.environ, {"HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY": "test-key"}), \
             patch.object(admin_routes.urllib.request, "urlopen", return_value=response_mock):
            response = self.client.post("/admin/compilation/moments", json={
                "csrf_token": self.csrf(), "episode_id": "episode-one",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["candidates"]), 1)
        self.assertEqual(response.get_json()["candidates"][0]["title"], "Valid")

    def test_compilation_moments_limits_long_transcript_prompt(self):
        import app.routes.admin as admin_routes

        self.login()
        work_root = Path(self.tempdir.name) / "work"
        workdir = work_root / "episode-long"
        workdir.mkdir(parents=True)
        (workdir / "source.mp4").write_bytes(b"source")
        transcript = [{"start": index * 2, "end": index * 2 + 1, "text": "kata " * 30} for index in range(1000)]
        (workdir / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
        llm_payload = json.dumps({"choices": [{"message": {"content": "[]"}}]}).encode()
        response_mock = Mock()
        response_mock.__enter__ = Mock(return_value=response_mock)
        response_mock.__exit__ = Mock(return_value=False)
        response_mock.read.return_value = llm_payload
        with patch.object(admin_routes, "WORK_ROOT", work_root), \
             patch.dict(os.environ, {"HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY": "test-key"}), \
             patch.object(admin_routes.urllib.request, "urlopen", return_value=response_mock) as open_url:
            response = self.client.post("/admin/compilation/moments", json={
                "csrf_token": self.csrf(), "episode_id": "episode-long",
            })
        self.assertEqual(response.status_code, 200)
        prompt = open_url.call_args.args[0].data.decode()
        self.assertLessEqual(len(prompt), 70000)

    def test_compilation_moments_falls_back_when_llm_times_out(self):
        import app.routes.admin as admin_routes

        self.login()
        work_root = Path(self.tempdir.name) / "work"
        workdir = work_root / "episode-timeout"
        workdir.mkdir(parents=True)
        (workdir / "source.mp4").write_bytes(b"source")
        transcript = [{"start": index * 10, "end": index * 10 + 10, "text": f"Momen {index}"} for index in range(8)]
        (workdir / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
        with patch.object(admin_routes, "WORK_ROOT", work_root), \
             patch.dict(os.environ, {"HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY": "test-key"}), \
             patch.object(admin_routes.urllib.request, "urlopen", side_effect=TimeoutError):
            response = self.client.post("/admin/compilation/moments", json={
                "csrf_token": self.csrf(), "episode_id": "episode-timeout",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["source"], "transcript-fallback")
        self.assertGreaterEqual(len(response.get_json()["candidates"]), 1)

    def test_clip_upload_records_mocked_success(self):
        import app.routes.admin as admin_routes

        self.login()
        clip_path = Path(self.tempdir.name) / "clip01.mp4"
        clip_path.write_bytes(b"video")
        caption = {"clip": "clip01.mp4", "title": "Test Clip", "caption": "Test caption"}
        result = {"video_id": "youtube123", "url": "https://youtube.com/watch?v=youtube123"}
        with patch.object(admin_routes, "_deployed_clip", return_value=(clip_path, caption)), \
             patch.object(admin_routes, "get_youtube_upload", return_value=None), \
             patch.object(admin_routes, "record_youtube_upload") as record, \
             patch("scripts.youtube_upload.upload_clip", return_value=result) as upload:
            response = self.client.post("/admin/clips/bocor-alus/episode-one/1/upload", data={"csrf_token": self.csrf()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(upload.call_args.args[1:3], ("Test Clip", "Test caption"))
        self.assertEqual(record.call_args_list[-1].args[:4], ("bocor-alus", "episode-one", "1", "uploaded"))

    def test_clip_upload_marks_quota_without_retry(self):
        import app.routes.admin as admin_routes

        self.login()
        clip_path = Path(self.tempdir.name) / "clip01.mp4"
        clip_path.write_bytes(b"video")
        caption = {"clip": "clip01.mp4", "title": "Test Clip", "caption": "Test caption"}
        with patch.object(admin_routes, "_deployed_clip", return_value=(clip_path, caption)), \
             patch.object(admin_routes, "get_youtube_upload", return_value=None), \
             patch.object(admin_routes, "record_youtube_upload") as record, \
             patch("scripts.youtube_upload.upload_clip", side_effect=RuntimeError("uploadLimitExceeded")) as upload:
            response = self.client.post("/admin/clips/bocor-alus/episode-one/1/upload", data={"csrf_token": self.csrf()})
        self.assertEqual(response.status_code, 302)
        upload.assert_called_once()
        self.assertEqual(record.call_args_list[-1].args[:4], ("bocor-alus", "episode-one", "1", "quota"))

    def test_clip_upload_requires_csrf_and_admin(self):
        response = self.client.post("/admin/clips/bocor-alus/episode-one/1/upload")
        self.assertEqual(response.status_code, 302)
        self.login()
        response = self.client.post("/admin/clips/bocor-alus/episode-one/1/upload")
        self.assertEqual(response.status_code, 400)

    def test_process_action_starts_worker_and_marks_submission_in_progress(self):
        from app.admin_store import list_submissions
        self.login()
        data = {
            "csrf_token": self.csrf(),
            "url": "https://youtu.be/abcdefghijk",
            "podcast": "jelasin-dong",
            "title": "Test episode",
            "action": "process",
        }
        with patch.dict(os.environ, {"ADMIN_JOB_LOG_DIR": str(Path(self.tempdir.name) / "logs")}):
            with patch("app.routes.admin.subprocess.Popen") as worker:
                response = self.client.post("/admin/youtube-links", data=data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list_submissions()[0]["status"], "in_progress")
        worker.assert_called_once()

    def test_episode_worker_marks_submission_completed(self):
        from app.admin_store import add_submission, list_submissions
        from scripts.process_episode import main as process_episode
        video_id = add_submission("https://youtu.be/abcdefghijk", "jelasin-dong", "Test episode")
        with patch("scripts.process_episode.subprocess.run", return_value=Mock(returncode=0)) as runner:
            with patch("sys.argv", ["process_episode.py", video_id]):
                self.assertEqual(process_episode(), 0)
        self.assertEqual(list_submissions()[0]["status"], "completed")
        self.assertEqual(runner.call_args.args[0][1], str(Path(__file__).resolve().parent / "run_one_episode.py"))

    def test_episode_worker_marks_nonzero_run_failed(self):
        from app.admin_store import add_submission, list_submissions
        from scripts.process_episode import main as process_episode
        video_id = add_submission("https://youtu.be/abcdefghijk", "jelasin-dong", "Test episode")
        with patch("scripts.process_episode.subprocess.run", return_value=Mock(returncode=1)):
            with patch("sys.argv", ["process_episode.py", video_id]):
                self.assertEqual(process_episode(), 1)
        self.assertEqual(list_submissions()[0]["status"], "failed")

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
