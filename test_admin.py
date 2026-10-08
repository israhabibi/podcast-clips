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

    def test_session_survives_app_restart_when_secret_is_stable(self):
        import subprocess
        import sys
        import textwrap

        # Independent interpreters import the deployed WSGI app and use its
        # actual login/start/callback routes. All provider requests are mocked.
        script = textwrap.dedent("""
            import json, sys
            from unittest.mock import Mock, patch
            from app.wsgi import app
            import app.routes.youtube as youtube
            app.config.update(TESTING=True)
            client = app.test_client()
            flow = Mock(code_verifier='test-verifier')
            flow.authorization_url.return_value = ('https://example.invalid/auth', 'restart-state')
            flow.credentials.token = 'test-token'
            flow.credentials.to_json.return_value = json.dumps({'token': 'test-token'})
            identity = Mock()
            identity.json.return_value = {'items': [{'id': 'test-channel'}]}
            with patch.object(youtube.google_auth_oauthlib.flow.Flow, 'from_client_config', return_value=flow), patch.object(youtube._req, 'get', return_value=identity):
                if sys.argv[1] == 'start':
                    client.get('/admin/login')
                    with client.session_transaction() as session:
                        csrf = session['admin_csrf']
                    assert client.post('/admin/login', data={'csrf_token': csrf, 'password': 'correct horse battery staple'}).status_code == 302
                    assert client.get('/auth').status_code == 302
                    print(json.dumps({'cookie': client.get_cookie(app.config['SESSION_COOKIE_NAME']).value}))
                else:
                    cookie = json.loads(sys.stdin.read())['cookie']
                    client.set_cookie(app.config['SESSION_COOKIE_NAME'], cookie)
                    response = client.get('/oauth?state=restart-state&code=test-code')
                    print(json.dumps({'status': response.status_code, 'exchanges': flow.fetch_token.call_count}))
        """)
        env = os.environ.copy()
        env.update({
            "APP_ENV": "production", "FLASK_SECRET_KEY": "test-restart-secret",
            "SESSION_COOKIE_SECURE": "false", "YOUTUBE_CLIENT_ID": "test-client",
            "YOUTUBE_CLIENT_SECRET": "test-client-secret", "YOUTUBE_EXPECTED_CHANNEL_ID": "test-channel",
            "YOUTUBE_TOKEN_FILE": str(Path(self.tempdir.name) / "restart-token.json"),
        })
        repo = Path(__file__).resolve().parent
        before = subprocess.run([sys.executable, "-c", script, "start"], env=env, cwd=repo, capture_output=True, text=True, check=True)
        cookie_data = before.stdout.strip()
        after = subprocess.run([sys.executable, "-c", script, "callback"], input=cookie_data, env=env, cwd=repo, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(after.stdout), {"status": 302, "exchanges": 1})
        self.assertEqual(json.loads(Path(env["YOUTUBE_TOKEN_FILE"]).read_text()), {"token": "test-token"})
        env["FLASK_SECRET_KEY"] = "different-test-secret"
        changed = subprocess.run([sys.executable, "-c", script, "callback"], input=cookie_data, env=env, cwd=repo, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(changed.stdout), {"status": 403, "exchanges": 0})

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
        (workdir / "clips").mkdir()
        (workdir / "clips" / "top5_compilation.mp4").write_bytes(b"old preview")
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
            duplicate = self.client.post("/admin/compilation/build", json=payload)
            status = self.client.get("/admin/compilation/status/episode-one")
            preview = self.client.get("/admin/compilation/preview/episode-one")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(status.get_json()["status"], "running")
        self.assertEqual(preview.status_code, 409)
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
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "video/mp4")
        finally:
            response.close()

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
        response = self.client.get("/admin/top5")
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
            {"start": 0, "end": 14, "text": "Momen pertama"},
        ]), encoding="utf-8")
        llm_payload = json.dumps({"choices": [{"message": {"content": json.dumps([
            {"start": 0, "end": 11, "title": "Valid", "reason": "Punchline kencang."},
            {"start": 3, "end": 5, "title": "Terlalu pendek", "reason": "Skip <6s"},
            {"start": 0, "end": 30, "title": "Terlalu panjang", "reason": "Skip >14s"},
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
        data = response.get_json()
        self.assertEqual(len(data["candidates"]), 1, f"Expected 1 valid candidate, got titles: {[c['title'] for c in data['candidates']]}")
        self.assertEqual(data["candidates"][0]["title"], "Valid")
        self.assertEqual(data.get("per_item_min"), 6)
        self.assertEqual(data.get("per_item_max"), 14)
        self.assertEqual(data.get("max_total_duration"), 60)

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
             patch.object(admin_routes, "deployed_clip_metadata", return_value={"sha256": "a" * 64, "file_size": 5}), \
             patch.object(admin_routes, "get_youtube_upload", return_value=None), \
             patch.object(admin_routes, "record_youtube_upload") as record, \
             patch("scripts.youtube_upload.upload_clip", return_value=result) as upload:
            response = self.client.post("/admin/clips/bocor-alus/episode-one/1/upload", data={
                "csrf_token": self.csrf(), "release_approved": "1",
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(upload.call_args.args[1:3], ("Test Clip", "Test caption"))
        self.assertTrue(upload.call_args.kwargs["release_approved"])
        self.assertEqual(record.call_args_list[-1].args[:4], ("bocor-alus", "episode-one", "1", "uploaded"))

    def test_clip_metadata_failure_retains_id_for_retry(self):
        import app.routes.admin as admin_routes
        from app.admin_store import get_youtube_upload, get_youtube_retry
        self.login()
        clip_path = Path(self.tempdir.name) / "clip01.mp4"
        clip_path.write_bytes(b"video")
        caption = {"clip": "clip01.mp4", "title": "Test Clip", "caption": "Caption"}
        def pending_upload(*args, **kwargs):
            kwargs["on_insert"]("existing123")
            self.assertEqual(get_youtube_upload("bocor-alus", "episode-one", "1")["video_id"], "existing123")
            return {"video_id": "existing123", "url": "https://youtu.be/existing123", "warning": "tags missing", "metadata_update_failed": True}
        with patch.object(admin_routes, "_deployed_clip", return_value=(clip_path, caption)), \
             patch.object(admin_routes, "deployed_clip_metadata", return_value={"sha256": "a" * 64, "file_size": 5}):
            with patch("scripts.youtube_upload.upload_clip", side_effect=pending_upload):
                self.client.post("/admin/clips/bocor-alus/episode-one/1/upload", data={"csrf_token": self.csrf(), "release_approved": "1"})
            self.assertEqual(get_youtube_upload("bocor-alus", "episode-one", "1")["status"], "metadata_pending")
            self.assertEqual(get_youtube_retry("bocor-alus", "episode-one", "1")["status"], "queued")
            with patch("scripts.youtube_upload.upload_clip", return_value={"video_id": "existing123", "url": "https://youtu.be/existing123"}) as repair:
                self.client.post("/admin/clips/bocor-alus/episode-one/1/upload", data={"csrf_token": self.csrf()})
            self.assertEqual(repair.call_args.kwargs["existing_video_id"], "existing123")
            self.assertEqual(get_youtube_upload("bocor-alus", "episode-one", "1")["status"], "uploaded")

    def test_compilation_metadata_failure_returns_pending_and_repairs_same_id(self):
        import app.routes.admin as admin_routes
        from app.admin_store import get_youtube_upload
        self.login()
        deployed = Path(self.tempdir.name) / "bocor-alus/episode-one/top5_compilation.mp4"
        deployed.parent.mkdir(parents=True)
        deployed.write_bytes(b"video")
        with patch.object(admin_routes, "_compilation_workdir", return_value=deployed.parent), \
             patch.object(admin_routes, "_compilation_deployed_path", return_value=deployed), \
             patch.object(admin_routes, "_episode_meta_from_workdir", return_value={"podcast": "bocor-alus", "title": "Episode"}), \
             patch.object(admin_routes, "deployed_clip_metadata", return_value={"sha256": "a" * 64, "file_size": 5}):
            with patch("scripts.youtube_upload.upload_clip", return_value={"video_id": "existing123", "url": "https://youtu.be/existing123", "error": "metadata rejected", "partial_success": True}):
                response = self.client.post("/admin/compilation/upload/episode-one", json={"csrf_token": self.csrf(), "release_confirmed": True})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.get_json()["status"], "metadata_pending")
            self.assertFalse(response.get_json()["ok"])
            self.assertEqual(get_youtube_upload("bocor-alus", "episode-one", "99")["video_id"], "existing123")
            with patch("scripts.youtube_upload.upload_clip", return_value={"video_id": "existing123", "url": "https://youtu.be/existing123"}) as repair:
                response = self.client.post("/admin/compilation/upload/episode-one", json={"csrf_token": self.csrf()})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(repair.call_args.kwargs["existing_video_id"], "existing123")

    def test_clip_upload_marks_quota_and_schedules_retry(self):
        import app.routes.admin as admin_routes
        from app.admin_store import get_youtube_retry

        self.login()
        clip_path = Path(self.tempdir.name) / "clip01.mp4"
        clip_path.write_bytes(b"video")
        caption = {"clip": "clip01.mp4", "title": "Test Clip", "caption": "Test caption"}
        with patch.object(admin_routes, "_deployed_clip", return_value=(clip_path, caption)), \
             patch.object(admin_routes, "deployed_clip_metadata", return_value={"sha256": "b" * 64, "file_size": 5}), \
             patch.object(admin_routes, "get_youtube_upload", return_value=None), \
             patch.object(admin_routes, "record_youtube_upload") as record, \
             patch("scripts.youtube_upload.upload_clip", side_effect=RuntimeError("uploadLimitExceeded")) as upload:
            response = self.client.post("/admin/clips/bocor-alus/episode-one/1/upload", data={
                "csrf_token": self.csrf(), "release_approved": "1",
            })
        self.assertEqual(response.status_code, 302)
        upload.assert_called_once()
        self.assertEqual(record.call_args_list[-1].args[:4], ("bocor-alus", "episode-one", "1", "quota"))
        self.assertEqual(get_youtube_retry("bocor-alus", "episode-one", "1")["status"], "queued")

    def test_clip_upload_requires_csrf_and_admin(self):
        response = self.client.post("/admin/clips/bocor-alus/episode-one/1/upload")
        self.assertEqual(response.status_code, 302)
        self.login()
        response = self.client.post("/admin/clips/bocor-alus/episode-one/1/upload")
        self.assertEqual(response.status_code, 400)

    def test_clip_upload_is_blocked_without_explicit_release_review(self):
        import app.routes.admin as admin_routes
        self.login()
        clip_path = Path(self.tempdir.name) / "clip01.mp4"
        clip_path.write_bytes(b"video")
        caption = {"clip": "clip01.mp4", "title": "Test Clip", "caption": "Test caption"}
        with patch.object(admin_routes, "_deployed_clip", return_value=(clip_path, caption)), \
             patch.object(admin_routes, "deployed_clip_metadata", return_value={"sha256": "c" * 64, "file_size": 5}), \
             patch("scripts.youtube_upload.upload_clip") as upload:
            response = self.client.post("/admin/clips/bocor-alus/episode-one/1/upload", data={
                "csrf_token": self.csrf(),
            })
        self.assertEqual(response.status_code, 302)
        upload.assert_not_called()

    def test_manual_studio_metadata_requires_prior_digest_bound_approval(self):
        import app.routes.admin as admin_routes
        self.login()
        clip_path = Path(self.tempdir.name) / "clip01.mp4"
        clip_path.write_bytes(b"manual review media")
        caption = {"clip": "clip01.mp4", "title": "Test Clip", "caption": "Caption"}
        digest = "d" * 64
        with patch.object(admin_routes, "_deployed_clip", return_value=(clip_path, caption)), \
             patch.object(admin_routes, "deployed_clip_metadata", return_value={"sha256": digest}), \
             patch.object(admin_routes, "record_manual_upload") as record:
            response = self.client.post(
                "/admin/clips/bocor-alus/episode-one/1/manual-upload",
                data={"csrf_token": self.csrf(), "video_id": "abcdefghijk"},
            )
        self.assertEqual(response.status_code, 302)
        record.assert_not_called()

        with patch.object(admin_routes, "_deployed_clip", return_value=(clip_path, caption)), \
             patch.object(admin_routes, "deployed_clip_metadata", return_value={"sha256": digest}), \
             patch.object(admin_routes, "approve_youtube_release") as approve:
            response = self.client.post(
                "/admin/clips/bocor-alus/episode-one/1/approve-manual",
                data={"csrf_token": self.csrf(), "release_approved": "1"},
            )
        self.assertEqual(response.status_code, 302)
        approve.assert_called_once_with("bocor-alus", "episode-one", "1", digest, reviewed_by="admin")

    def test_clip_upload_does_not_duplicate_an_upload_in_progress(self):
        import app.routes.admin as admin_routes

        self.login()
        clip_path = Path(self.tempdir.name) / "clip01.mp4"
        clip_path.write_bytes(b"video")
        caption = {"clip": "clip01.mp4", "title": "Test Clip", "caption": "Caption"}
        with patch.object(admin_routes, "_deployed_clip", return_value=(clip_path, caption)), \
             patch.object(admin_routes, "get_youtube_upload", return_value={"status": "uploading"}), \
             patch("scripts.youtube_upload.upload_clip") as upload:
            response = self.client.post(
                "/admin/clips/bocor-alus/episode-one/1/upload",
                data={"csrf_token": self.csrf()},
            )
        self.assertEqual(response.status_code, 302)
        upload.assert_not_called()

    def test_upload_claim_is_atomic_and_retryable_after_failure(self):
        from app.admin_store import claim_youtube_upload, record_youtube_upload

        claimed, previous = claim_youtube_upload("bocor-alus", "episode-one", "1")
        self.assertTrue(claimed)
        self.assertIsNone(previous)
        claimed_again, current = claim_youtube_upload("bocor-alus", "episode-one", "1")
        self.assertFalse(claimed_again)
        self.assertEqual(current["status"], "uploading")

        record_youtube_upload(
            "bocor-alus", "episode-one", "1", "failed", error="temporary failure"
        )
        retry_claimed, previous = claim_youtube_upload("bocor-alus", "episode-one", "1")
        self.assertTrue(retry_claimed)
        self.assertEqual(previous["status"], "failed")

    def test_metadata_claim_retains_id_and_rejects_replaced_media(self):
        from app.admin_store import claim_youtube_upload, get_youtube_upload, record_youtube_upload
        key = ("bocor-alus", "episode-one", "1")
        record_youtube_upload(*key, "metadata_pending", video_id="existing123", sha256="a" * 64)
        self.assertFalse(claim_youtube_upload(*key, sha256="b" * 64)[0])
        self.assertEqual(get_youtube_upload(*key)["status"], "metadata_pending")
        claimed, previous = claim_youtube_upload(*key, sha256="a" * 64)
        self.assertTrue(claimed)
        self.assertEqual(previous["video_id"], "existing123")
        self.assertEqual(get_youtube_upload(*key)["video_id"], "existing123")

    def test_quota_retry_queue_claims_due_items_once_and_reschedules(self):
        from datetime import datetime, timedelta, timezone
        from app.admin_store import (
            enqueue_youtube_retry, claim_due_youtube_retry,
            finish_youtube_retry, get_youtube_retry,
        )
        due = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        later = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        enqueue_youtube_retry("bocor-alus", "episode-one", "99", retry_at=due, error="quota")
        first = claim_due_youtube_retry()
        self.assertEqual(first["status"], "processing")
        self.assertIsNone(claim_due_youtube_retry())
        finish_youtube_retry("bocor-alus", "episode-one", "99", "queued", retry_at=later,
                             error="quota still active")
        self.assertEqual(get_youtube_retry("bocor-alus", "episode-one", "99")["retry_at"], later)
        self.assertIsNone(claim_due_youtube_retry())

    def test_failed_upload_remains_visible_for_retry(self):
        import app.routes.admin as admin_routes

        self.login()
        clip = {
            "podcast": "bocor-alus", "episode": "episode-one", "clip_num": "1",
            "title": "Retry this clip", "manual": None,
            "upload": {"status": "failed", "error": "network error", "video_id": None},
        }
        with patch.object(admin_routes, "_deployed_clips", return_value=[clip]):
            response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Retry this clip", response.data)
        self.assertIn(b"network error", response.data)

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

    def test_failed_submission_can_be_retried_without_deleting_row(self):
        from app.admin_store import add_submission, list_submissions, set_submission_status
        video_id = add_submission("https://youtu.be/abcdefghijk", "jelasin-dong", "Test episode")
        set_submission_status(video_id, "failed", error_message="temporary worker failure")
        self.login()
        log_dir = Path(self.tempdir.name) / "retry-logs"
        with patch.dict(os.environ, {"ADMIN_JOB_LOG_DIR": str(log_dir)}):
            with patch("app.routes.admin.subprocess.Popen") as worker:
                response = self.client.post(
                    f"/admin/youtube-links/{video_id}/retry",
                    data={"csrf_token": self.csrf()},
                )
        self.assertEqual(response.status_code, 302)
        rows = list_submissions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "in_progress")
        self.assertEqual(rows[0]["error_message"], "")
        worker.assert_called_once()

    def test_submission_retry_rejects_missing_csrf_and_duplicate_claim(self):
        from app.admin_store import add_submission, claim_submission_retry
        video_id = add_submission("https://youtu.be/abcdefghijk", "jelasin-dong", "Test episode")
        self.login()
        response = self.client.post(f"/admin/youtube-links/{video_id}/retry")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(claim_submission_retry(video_id))
        self.assertFalse(claim_submission_retry(video_id))

    def test_episode_worker_marks_submission_ready_for_review(self):
        from app.admin_store import add_submission, list_submissions
        from scripts.process_episode import main as process_episode
        video_id = add_submission("https://youtu.be/abcdefghijk", "jelasin-dong", "Test episode")
        with patch("scripts.process_episode.subprocess.run", return_value=Mock(returncode=0)) as runner:
            with patch("sys.argv", ["process_episode.py", video_id]):
                self.assertEqual(process_episode(), 0)
        self.assertEqual(list_submissions()[0]["status"], "ready_for_review")
        self.assertEqual(runner.call_args.args[0][1], str(Path(__file__).resolve().parent / "run_one_episode.py"))

    def test_episode_worker_marks_nonzero_run_failed(self):
        from app.admin_store import add_submission, list_submissions
        from scripts.process_episode import main as process_episode
        video_id = add_submission("https://youtu.be/abcdefghijk", "jelasin-dong", "Test episode")
        with patch("scripts.process_episode.subprocess.run", return_value=Mock(
            returncode=1, stdout="", stderr="curate failed: clip boundary invalid",
        )):
            with patch("sys.argv", ["process_episode.py", video_id]):
                self.assertEqual(process_episode(), 1)
        self.assertEqual(list_submissions()[0]["status"], "failed")
        self.assertIn("curate failed: clip boundary invalid", list_submissions()[0]["error_message"])

    def test_submission_error_details_are_returned_and_rendered(self):
        from app.admin_store import add_submission, list_submissions, set_submission_status

        video_id = add_submission(
            "https://youtu.be/abcdefghijk", "jelasin-dong", "Broken episode"
        )
        set_submission_status(video_id, "failed", "ffmpeg failed on clip 3")
        row = list_submissions()[0]
        self.assertEqual(row["error_message"], "ffmpeg failed on clip 3")
        self.assertTrue(row["last_error_at"])

        self.login()
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ffmpeg failed on clip 3", response.data)

    def test_failed_submission_retry_is_visible_with_details_collapsed(self):
        from app.admin_store import add_submission, set_submission_status
        import app.routes.admin as admin_routes
        video_id = add_submission("https://youtu.be/abcdefghijk", "tukang-kupas", "Failed episode")
        set_submission_status(video_id, "failed", "Original worker error")
        self.login()
        with patch.object(admin_routes, "_deployed_clips", return_value=[]), \
             patch.object(admin_routes, "_try_check_youtube_token_valid", return_value=(False, "no_token")):
            response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        card = response.get_data(as_text=True).split('<article class="sub-group"', 1)[1].split('</article>', 1)[0]
        header = card.split('</header>', 1)[0]
        self.assertIn(f'/admin/youtube-links/{video_id}/retry', header)
        self.assertIn('Coba proses lagi', header)
        self.assertIn('Progress tidak tersedia', header)
        self.assertNotIn('100%', header)
        self.assertIn('Original worker error', card)
        self.assertRegex(card, r'class="sub-group-items"[\s\S]*?aria-hidden="true"\s+hidden')

    def test_failed_submission_progress_is_unknown_in_page_and_polling(self):
        from app.admin_store import add_submission, set_submission_status
        import app.routes.admin as admin_routes
        progress = admin_routes._job_progress({"status": "failed", "video_id": "abcdefghijk"})
        self.assertFalse(progress["percent_known"])
        self.assertEqual(progress["percent"], 0)
        video_id = add_submission("https://youtu.be/abcdefghijk", "tukang-kupas", "Failed episode")
        set_submission_status(video_id, "failed", "Original worker error")
        self.login()
        response = self.client.get("/admin/jobs")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["jobs"][0]["percent_known"])
        self.assertFalse(payload["groups"][0]["percent_known"])
        self.assertEqual(payload["groups"][0]["percent"], 0)

    def test_group_detail_toggle_removes_and_restores_hidden_attribute(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required to exercise the actual JavaScript click handler")
        template = (Path(__file__).resolve().parent / "app/templates/admin.html").read_text()
        handler = '// --- Source video toggle' + template.split('// --- Source video toggle', 1)[1].split('// --- Job progress auto refresh', 1)[0]
        harness = r'''
            const vm = require('node:vm');
            const assert = require('node:assert/strict');
            const source = require('node:fs').readFileSync(0, 'utf8');
            const group = {attributes: {}, setAttribute(k, v) { this.attributes[k] = v; }};
            const target = {
                hidden: true, attributes: {},
                setAttribute(k, v) { this.attributes[k] = v; },
                toggleAttribute(k, present) { assert.equal(k, 'hidden'); this.hidden = present; }
            };
            const button = {
                attributes: {'aria-expanded': 'false', 'aria-controls': 'items'}, textContent: '',
                getAttribute(k) { return this.attributes[k]; },
                setAttribute(k, v) { this.attributes[k] = v; },
                closest(selector) { return selector === '.sub-group' ? group : null; }
            };
            const listeners = [];
            vm.runInNewContext(source, {document: {
                addEventListener(type, fn) { if (type === 'click') listeners.push(fn); },
                getElementById(id) { return id === 'items' ? target : null; }
            }});
            const event = {target: {closest(selector) { return selector === '[data-group-toggle-items]' ? button : null; }}};
            for (const listener of listeners) listener(event);
            assert.equal(target.hidden, false);
            assert.equal(target.attributes['aria-hidden'], 'false');
            assert.equal(group.attributes['aria-expanded'], 'true');
            assert.equal(button.textContent, '▲ Sembunyikan');
            for (const listener of listeners) listener(event);
            assert.equal(target.hidden, true);
            assert.equal(target.attributes['aria-hidden'], 'true');
            assert.equal(group.attributes['aria-expanded'], 'false');
            assert.equal(button.textContent, '⋮ Detail');
        '''
        result = subprocess.run([node, '-e', harness], input=handler, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_oauth_callbacks_reject_mismatched_state_before_exchange(self):
        self.login()
        with self.client.session_transaction() as session:
            session["tiktok_state"] = "expected-tiktok"
            session["tiktok_verifier"] = "verifier"
            session["oauth_state"] = "expected-youtube"
            session["code_verifier"] = "verifier"
        with patch("app.routes.tiktok._req.post") as tiktok_request, \
             patch("app.routes.youtube.google_auth_oauthlib.flow.Flow.from_client_config") as youtube_flow:
            tiktok = self.client.get("/tiktok-oauth?code=code&state=wrong")
            youtube = self.client.get("/oauth?code=code&state=wrong")
        self.assertEqual(tiktok.status_code, 400)
        self.assertEqual(youtube.status_code, 400)
        tiktok_request.assert_not_called()
        youtube_flow.assert_not_called()

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
             patch("app.routes.tiktok.TIKTOK_EXPECTED_OPEN_ID", "test-account"), \
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
        identity_response = Mock(status_code=200)
        identity_response.json.return_value = {"items": [{"id": "expected-channel"}]}
        with patch("app.routes.youtube.YOUTUBE_TOKEN_FILE", str(token_path)), \
             patch("app.routes.youtube.YOUTUBE_EXPECTED_CHANNEL_ID", "expected-channel"), \
             patch("app.routes.youtube._req.get", return_value=identity_response), \
             patch("app.routes.youtube.google_auth_oauthlib.flow.Flow.from_client_config", return_value=flow):
            first = self.client.get("/oauth?code=test-code&state=expected-state")
            second = self.client.get("/oauth?code=test-code&state=expected-state")
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(json.loads(token_path.read_text())["token"], "test-token")
        flow.fetch_token.assert_called_once()

    def test_unexpected_oauth_accounts_leave_previous_tokens_unchanged(self):
        self.login()
        tik_tok_path = Path(self.tempdir.name) / "tiktok-token.json"
        yt_tok_path = Path(self.tempdir.name) / "youtube-token.json"
        tik_tok_path.write_text('{"access_token":"old-tiktok"}')
        yt_tok_path.write_text('{"token":"old-youtube"}')
        with self.client.session_transaction() as session:
            session["tiktok_state"] = "tiktok-state"
            session["tiktok_verifier"] = "tiktok-verifier"
        token_response = Mock(status_code=200)
        token_response.json.return_value = {"access_token": "new-tiktok", "open_id": "wrong-account"}
        with patch("app.routes.tiktok.TIKTOK_TOKEN_FILE", str(tik_tok_path)), \
             patch("app.routes.tiktok.TIKTOK_EXPECTED_OPEN_ID", "expected-account"), \
             patch("app.routes.tiktok._req.post", return_value=token_response):
            response = self.client.get("/tiktok-oauth?code=code&state=tiktok-state")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(tik_tok_path.read_text())["access_token"], "old-tiktok")

        with self.client.session_transaction() as session:
            session["oauth_state"] = "youtube-state"
            session["code_verifier"] = "youtube-verifier"
        flow = Mock()
        flow.credentials.token = "new-youtube-access"
        flow.credentials.to_json.return_value = '{"token":"new-youtube"}'
        identity_response = Mock(status_code=200)
        identity_response.json.return_value = {"items": [{"id": "wrong-channel"}]}
        with patch("app.routes.youtube.YOUTUBE_TOKEN_FILE", str(yt_tok_path)), \
             patch("app.routes.youtube.YOUTUBE_EXPECTED_CHANNEL_ID", "expected-channel"), \
             patch("app.routes.youtube._req.get", return_value=identity_response), \
             patch("app.routes.youtube.google_auth_oauthlib.flow.Flow.from_client_config", return_value=flow):
            response = self.client.get("/oauth?code=code&state=youtube-state")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(yt_tok_path.read_text())["token"], "old-youtube")

    def test_failed_token_replace_preserves_previous_file(self):
        from app.admin_security import write_private_json
        token_path = Path(self.tempdir.name) / "token.json"
        token_path.write_text('{"token":"old"}')
        with patch("app.admin_security.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                write_private_json(token_path, {"token": "new"})
        self.assertEqual(json.loads(token_path.read_text()), {"token": "old"})
        self.assertEqual(list(Path(self.tempdir.name).glob(".token-*")), [])

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
