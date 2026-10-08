#!/usr/bin/env python3
"""
test_pipeline.py — Test suite untuk Podcast Clips pipeline.
Jalankan sebelum deploy:  python test_pipeline.py

Cakupan:
  1. Import semua modul (syntax check)
  2. Flask app (routes, path traversal, OAuth state)
  3. Pipeline scripts (curate.py, caption.py, cut_smart.py, cut.py)
  4. Upload scripts (youtube_upload.py, tiktok_upload.py)
  5. Monitor script
  6. Template rendering
  7. Edge cases (file tidak ada, path traversal, invalid input)
"""

import sys, os, io, json, runpy, tempfile, unittest, logging, importlib.util
import subprocess
import hashlib
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from io import StringIO
from unittest.mock import patch

# ── Setup ──────────────────────────────────────────────────────────────────
REPO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_DIR))

# Supress Flask startup noise
logging.disable(logging.CRITICAL)
os.environ['FLASK_SECRET_KEY'] = 'test-secret-key-12345'
os.environ['HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY'] = 'test-api-key'
os.environ['WERKZEUG_RUN_MAIN'] = 'true'

# ── Helpers ────────────────────────────────────────────────────────────────

def create_temp_episode():
    """Create a minimal episode workspace in a temp dir with valid clip data."""
    tmp = Path(tempfile.mkdtemp())
    
    # Minimal transcript
    with open(tmp / "transcript.json", "w") as f:
        json.dump([
            {"start": 0.0, "end": 5.0, "text": "Halo selamat datang di podcast"},
            {"start": 5.0, "end": 10.0, "text": "Hari ini kita bahas topik menarik"},
            {"start": 10.0, "end": 15.0, "text": "Menurut saya ini sangat penting"},
            {"start": 15.0, "end": 20.0, "text": "Karena banyak orang belum tahu"},
            {"start": 20.0, "end": 25.0, "text": "Mari kita simak bersama sama"},
        ], f)
    
    # Minimal clips
    with open(tmp / "clips.json", "w") as f:
        json.dump([
            {"start": 0.0, "end": 12.0, "title": "Pembukaan", "hook": "Halo selamat datang"},
            {"start": 10.0, "end": 25.0, "title": "Topik Utama", "hook": "Menurut saya ini penting"},
        ], f)
    
    # Minimal search_results
    with open(tmp / "search_results.json", "w") as f:
        json.dump({
            "1": [{"title": "Berita 1", "url": "https://tempo.co/berita1"}],
            "2": [{"title": "Berita 2", "url": "https://kompas.com/berita2"}],
        }, f)
    
    return tmp

# ── Tests ──────────────────────────────────────────────────────────────────

class TestImports(unittest.TestCase):
    """Test 1: Semua modul bisa di-import tanpa error."""

    def test_flask_app_import(self):
        from app import app
        self.assertTrue(app.secret_key)
        self.assertTrue(app.static_folder)

    def test_routes_import(self):
        from app.routes import clips, youtube, tiktok, admin
        self.assertTrue(hasattr(clips, 'index'))

    def test_scripts_import(self):
        # These are run as __main__, just check they parse
        for script in ['curate.py', 'caption.py', 'cut.py', 'cut_smart.py',
                       'monitor.py', 'scripts/youtube_upload.py', 'scripts/tiktok_upload.py',
                       'scripts/youtube_transcript_to_segments.py']:
            with self.subTest(script=script):
                path = REPO_DIR / script
                self.assertTrue(path.exists(), f"{script} not found")
                # Syntax check via compile
                with open(path) as f:
                    compile(f.read(), str(path), 'exec')


class TestClipQuality(unittest.TestCase):
    def setUp(self):
        from clip_quality import format_timed_transcript, validate_clips, validate_clip_ranges
        self.format_timed_transcript = format_timed_transcript
        self.validate_clips = validate_clips
        self.validate_clip_ranges = validate_clip_ranges
        self.segments = [
            {"start": 0.0, "end": 10.0, "text": "Pembukaan singkat."},
            {"start": 10.0, "end": 25.0, "text": "Kita bahas hasil riset terbaru."},
            {"start": 25.0, "end": 50.0, "text": "Temuan ini mengubah cara pandang kita."},
            {"start": 50.0, "end": 65.0, "text": "Itulah bagian yang paling mengejutkan."},
            {"start": 65.0, "end": 80.0, "text": "Sekian penjelasan untuk hari ini."},
        ]

    def test_prompt_transcript_keeps_each_exact_segment_boundary(self):
        rendered = self.format_timed_transcript(self.segments)
        self.assertIn("[10.00-25.00] Kita bahas hasil riset terbaru.", rendered)
        self.assertIn("[50.00-65.00] Itulah bagian yang paling mengejutkan.", rendered)

    def test_clip_times_snap_and_nonverbatim_hook_uses_transcript_quote(self):
        clips = [{
            "start": 10.8,
            "end": 50.7,
            "title": "Temuan mengejutkan",
            "hook": "Klaim sensasional yang tidak ada di transkrip",
        }]
        validated = self.validate_clips(clips, self.segments)
        self.assertEqual((validated[0]["start"], validated[0]["end"]), (10.0, 50.0))
        self.assertEqual(validated[0]["hook"], self.segments[1]["text"])

    def test_verbatim_hook_is_preserved(self):
        clips = [{
            "start": 10.0,
            "end": 50.0,
            "title": "Temuan mengejutkan",
            "hook": "Temuan ini mengubah cara pandang kita",
        }]
        validated = self.validate_clips(clips, self.segments)
        self.assertEqual(validated[0]["hook"], clips[0]["hook"])

    def test_overlong_title_is_shortened_without_invalidating_clip(self):
        clips = [{
            "start": 10.0,
            "end": 50.0,
            "title": "Judul klip yang terlalu panjang untuk ditampilkan pada video pendek",
            "hook": "Temuan ini mengubah cara pandang kita",
        }]
        validated = self.validate_clips(clips, self.segments)
        self.assertLessEqual(len(validated[0]["title"]), 50)
        self.assertTrue(validated[0]["title"].endswith("..."))

    def test_clip_with_unmatched_timestamp_is_rejected(self):
        clips = [{
            "start": 4.0,
            "end": 50.0,
            "title": "Topik",
            "hook": "Kita bahas hasil riset terbaru",
        }]
        with self.assertRaisesRegex(ValueError, "segment boundaries"):
            self.validate_clips(clips, self.segments)

    def test_renderer_range_validation_preserves_valid_ranges(self):
        clips = [{"start": 10.0, "end": 50.0, "title": "Valid range"}]
        self.assertEqual(self.validate_clip_ranges(clips, self.segments, 100), clips)

    def test_renderer_rejects_invalid_time_ranges_before_rendering(self):
        invalid_cases = [
            ({"start": -1, "end": 40, "title": "Negative"}, "invalid range"),
            ({"start": 50, "end": 40, "title": "Reversed"}, "invalid range"),
            ({"start": 10, "end": float("nan"), "title": "NaN"}, "finite number"),
            ({"start": 10, "end": float("inf"), "title": "Infinite"}, "finite number"),
            ({"start": True, "end": 50, "title": "Bool"}, "finite number"),
            ({"start": 0, "end": 80, "title": "Too long"}, "duration must be"),
            ({"start": 40, "end": 80, "title": "Past end"}, "exceeds source duration"),
        ]
        for clip, message in invalid_cases:
            with self.subTest(clip=clip), self.assertRaisesRegex(ValueError, message):
                self.validate_clip_ranges([clip], self.segments, 70)
        with self.assertRaisesRegex(ValueError, "not covered by transcript"):
            self.validate_clip_ranges(
                [{"start": 10, "end": 50, "title": "Uncovered"}],
                [{"start": 0, "end": 5, "text": "Outside clip"}], 70,
            )


class TestEpisodeManifest(unittest.TestCase):
    def test_manifest_lists_only_complete_media_and_matching_captions(self):
        from episode_manifest import build_review_manifest, processing_manifest, write_manifest
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "clips").mkdir()
            (root / "clips" / "show_episode_01.mp4").write_bytes(b"complete video")
            clips = [{"start": 10, "end": 50, "title": "Moment", "filename": "show_episode_01.mp4"}]
            captions = {"1": {"clip": "show_episode_01.mp4", "title": "Moment", "caption": "Ready for review"}}
            manifest = build_review_manifest(root, "video1234567", "show", "Episode", clips, captions)
            self.assertEqual(manifest["status"], "ready_for_review")
            self.assertEqual(manifest["review_status"], "pending")
            self.assertEqual(manifest["clips"][0]["file_size"], len(b"complete video"))
            self.assertEqual(len(manifest["clips"][0]["sha256"]), 64)
            self.assertEqual(manifest["clips"][0]["platform_upload_ids"], {})
            write_manifest(root, processing_manifest("video1234567", "show", "Episode"))
            self.assertEqual(json.loads((root / "episode_manifest.json").read_text())["status"], "processing")

    def test_manifest_rejects_missing_media_and_incomplete_captions(self):
        from episode_manifest import build_review_manifest
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "clips").mkdir()
            clip = {"start": 10, "end": 50, "title": "Moment", "filename": "missing.mp4"}
            caption = {"1": {"clip": "missing.mp4", "title": "Moment", "caption": "Caption"}}
            with self.assertRaisesRegex(ValueError, "missing or empty"):
                build_review_manifest(root, "video1234567", "show", "Episode", [clip], caption)
            (root / "clips" / "missing.mp4").write_bytes(b"video")
            with self.assertRaisesRegex(ValueError, "do not match"):
                build_review_manifest(root, "video1234567", "show", "Episode", [clip], {})


class TestRendererFailureHandling(unittest.TestCase):
    def test_cut_failure_returns_nonzero_and_discards_partial_mp4(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            (workspace / "clips").mkdir(parents=True)
            source = workspace / "source.mp4"
            source.write_bytes(b"source")
            (workspace / "clips.json").write_text(json.dumps([
                {"start": 10, "end": 50, "title": "Moment", "filename": "moment.mp4"},
            ]))
            (workspace / "transcript.json").write_text(json.dumps([
                {"start": 0, "end": 10, "text": "Intro"},
                {"start": 10, "end": 50, "text": "A complete transcript segment."},
            ]))
            fake_bin = root / "bin"
            fake_bin.mkdir()
            ffprobe = fake_bin / "ffprobe"
            ffprobe.write_text("#!/usr/bin/env python3\nprint('{\\\"format\\\":{\\\"duration\\\":\\\"100\\\"}}')\n")
            ffmpeg = fake_bin / "ffmpeg"
            ffmpeg.write_text("#!/usr/bin/env python3\nimport pathlib, sys\npathlib.Path(sys.argv[-1]).write_bytes(b'partial')\nsys.exit(7)\n")
            ffprobe.chmod(0o755)
            ffmpeg.chmod(0o755)
            env = os.environ.copy()
            env["PODCAST_WORK_DIR"] = str(workspace)
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            result = subprocess.run(
                [sys.executable, str(REPO_DIR / "cut.py"), str(source)],
                cwd=str(REPO_DIR), env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((workspace / "clips" / "moment.mp4").exists())
            self.assertEqual(list((workspace / "clips").glob("*.tmp.mp4")), [])


class TestCaptionFailClosed(unittest.TestCase):
    def test_missing_api_key_exits_before_creating_caption_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"PODCAST_WORK_DIR": temp_dir, "HOME": temp_dir}, clear=True):
                with self.assertRaisesRegex(SystemExit, "API key not found"):
                    runpy.run_path(str(REPO_DIR / "caption.py"), run_name="__main__")
            self.assertFalse((Path(temp_dir) / "captions.json").exists())

    def test_empty_llm_output_fails_after_retries_without_caption_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "clips").mkdir()
            (workspace / "clips" / "moment.mp4").write_bytes(b"video")
            (workspace / "clips.json").write_text(json.dumps([
                {"start": 10, "end": 50, "title": "Moment", "filename": "moment.mp4"},
            ]))
            (workspace / "transcript.json").write_text(json.dumps([
                {"start": 10, "end": 50, "text": "A transcript segment."},
            ]))
            response = b'{"choices":[{"message":{"content":"  "}}]}'
            with patch.dict(os.environ, {"PODCAST_WORK_DIR": temp_dir,
                                        "HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY": "test-api-key"}), \
                    patch("urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(response)) as urlopen, \
                    patch("time.sleep"), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                with self.assertRaisesRegex(RuntimeError, "LLM request failed after 3 attempts"):
                    runpy.run_path(str(REPO_DIR / "caption.py"), run_name="__main__")
            self.assertEqual(urlopen.call_count, 3)
            self.assertFalse((workspace / "captions.json").exists())


class TestFaceTracking(unittest.TestCase):
    def test_no_face_fallback_centers_crop_within_video(self):
        from face_tracking import smooth_track
        width, height = 1920, 1080
        crop_width = int(height * 9 / 16)
        track = smooth_track([], 30, crop_width, width)
        self.assertEqual(track, [(0, width / 2)])
        crop_x = track[0][1] - crop_width / 2
        self.assertGreaterEqual(crop_x, 0)
        self.assertLessEqual(crop_x, width - crop_width)

    def test_tracked_crop_positions_are_clamped_and_dimensions_checked(self):
        from face_tracking import smooth_track, validate_video_dimensions
        crop_width = validate_video_dimensions(1920, 1080)
        track = smooth_track([(0, -1000), (10, 5000)], 10, crop_width, 1920)
        for _, center_x in track:
            crop_x = center_x - crop_width / 2
            self.assertGreaterEqual(crop_x, 0)
            self.assertLessEqual(crop_x, 1920 - crop_width)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            validate_video_dimensions(0, 1080)
        with self.assertRaisesRegex(ValueError, "cannot fit"):
            validate_video_dimensions(100, 2000)


class TestFlaskApp(unittest.TestCase):
    """Test 2: Flask app routes."""
    
    @classmethod
    def setUpClass(cls):
        from app import app
        app.config['TESTING'] = True
        app.config['SERVER_NAME'] = 'localhost'
        cls.client = app.test_client()
        # Import routes so they register
        from app.routes import clips, youtube, tiktok, admin
        cls.app = app

    def test_index_returns_200(self):
        r = self.client.get('/')
        self.assertIn(r.status_code, (200, 404))  # 404 if no clips dir

    def test_pipeline_trigger_is_not_public(self):
        response = self.client.post('/trigger', data={'youtube_url': 'https://youtu.be/abcdefghijk'})
        self.assertEqual(response.status_code, 404)
    
    def test_terms_returns_200(self):
        r = self.client.get('/terms')
        self.assertEqual(r.status_code, 200)
    
    def test_privacy_returns_200(self):
        r = self.client.get('/privacy')
        self.assertEqual(r.status_code, 200)

    def test_clips_view_no_args(self):
        r = self.client.get('/clips')
        self.assertIn(r.status_code, (200, 404))
    
    def test_path_traversal_filename(self):
        """Test path traversal protection di serve_clip."""
        r = self.client.get('/static/clips/jelasin-dong/ep1/../../../etc/passwd')
        # 400 from validation OR 404 because route matches
        self.assertIn(r.status_code, (400, 404))
    
    def test_path_traversal_dotdot(self):
        r = self.client.get('/static/clips/jelasin-dong/../other/../file')
        self.assertIn(r.status_code, (400, 404))
    
    def test_path_traversal_abs(self):
        r = self.client.get('/static/clips/jelasin-dong/ep1/%2Fetc%2Fpasswd')
        self.assertIn(r.status_code, (400, 404))
    
    def test_allowed_podcast_whitelist(self):
        """Test bahwa hanya podcast valid yang bisa diakses."""
        r = self.client.get('/clips/unknown-podcast/ep1')
        self.assertEqual(r.status_code, 404)

    def test_explicit_gallery_episode_never_falls_back(self):
        from app.routes import clips
        with tempfile.TemporaryDirectory() as temp_dir:
            podcast_dir = Path(temp_dir) / 'jelasin-dong'
            episode_dir = podcast_dir / 'episode-valid'
            episode_dir.mkdir(parents=True)
            (episode_dir / 'captions.json').write_text(json.dumps({
                '1': {'clip': 'clip1.mp4', 'title': 'Episode yang benar'}
            }))
            (episode_dir / 'clips.json').write_text(json.dumps([{
                'start': 0, 'end': 30, 'title': 'Klip benar', 'hook': 'Hook benar'
            }]))
            (episode_dir / 'episode_data.json').write_text(json.dumps({'episode_title': 'Episode yang benar'}))
            (episode_dir / 'clip1.mp4').write_bytes(b'video')
            healthy_dir = podcast_dir / 'episode-healthy'
            healthy_dir.mkdir()
            (healthy_dir / 'captions.json').write_text(json.dumps({
                '1': {'clip': 'healthy.mp4', 'title': 'Episode sehat'}
            }))
            (healthy_dir / 'clips.json').write_text(json.dumps([{
                'start': 0, 'end': 30, 'title': 'Klip sehat', 'hook': 'Hook sehat'
            }]))
            (healthy_dir / 'episode_data.json').write_text(json.dumps({'episode_title': 'Episode sehat'}))
            (healthy_dir / 'healthy.mp4').write_bytes(b'video')

            with patch.object(clips, 'CLIPS_DIR', temp_dir):
                valid_response = self.client.get('/clips/jelasin-dong/episode-valid')
                self.assertEqual(valid_response.status_code, 200)
                self.assertIn('Episode yang benar'.encode(), valid_response.data)
                self.assertEqual(self.client.get('/clips/jelasin-dong/episode-missing').status_code, 404)
                (episode_dir / 'captions.json').write_text('{broken json')
                self.assertEqual(self.client.get('/clips/jelasin-dong/episode-valid').status_code, 404)
                index_response = self.client.get('/')
                self.assertEqual(index_response.status_code, 200)
                self.assertIn('Episode sehat'.encode(), index_response.data)
                self.assertNotIn('Episode yang benar'.encode(), index_response.data)
                (episode_dir / 'captions.json').write_text(json.dumps({
                    '1': {'clip': 'clip1.mp4', 'title': 'Episode yang benar'}
                }))
                (episode_dir / 'clip1.mp4').unlink()
                self.assertEqual(self.client.get('/clips/jelasin-dong/episode-valid').status_code, 404)
                # Invalid episodes are omitted from the gallery listing too.
                self.assertNotIn(b'episode-valid', self.client.get('/').data)

    def test_oauth_auth_route(self):
        r = self.client.get('/auth')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/admin/login', r.headers['Location'])
    
    def test_oauth_callback_requires_admin(self):
        r = self.client.get('/oauth?code=test&state=wrong')
        self.assertEqual(r.status_code, 403)


class TestAppInit(unittest.TestCase):
    """Test 3: app/__init__.py behavior."""

    def test_secret_key_from_env(self):
        env = os.environ.copy()
        env['FLASK_SECRET_KEY'] = 'fixed-secret-for-test'
        result = subprocess.run(
            [sys.executable, '-c', 'from app import app; print(app.secret_key)'],
            cwd=REPO_DIR, env=env, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), 'fixed-secret-for-test')

    def test_secret_key_fallback_random(self):
        env = os.environ.copy()
        env.pop('FLASK_SECRET_KEY', None)
        result = subprocess.run(
            [sys.executable, '-c', 'from app import app; print(len(app.secret_key))'],
            cwd=REPO_DIR, env=env, capture_output=True, text=True, check=True,
        )
        self.assertGreater(int(result.stdout.strip()), 0)

    def test_missing_secret_is_rejected_in_production(self):
        env = os.environ.copy()
        env.pop('FLASK_SECRET_KEY', None)
        env['APP_ENV'] = 'production'
        result = subprocess.run(
            [sys.executable, '-c', 'from app import app'],
            cwd=REPO_DIR, env=env, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('FLASK_SECRET_KEY must be configured in production', result.stderr)


class TestCurateScript(unittest.TestCase):
    """Test 4: curate.py — validation & edge cases."""
    
    def setUp(self):
        self.tmp = create_temp_episode()
        self.orig_workdir = os.environ.get('PODCAST_WORK_DIR')
        self.orig_argv = sys.argv
        os.environ['PODCAST_WORK_DIR'] = str(self.tmp)

    def tearDown(self):
        if self.orig_workdir:
            os.environ['PODCAST_WORK_DIR'] = self.orig_workdir
        else:
            os.environ.pop('PODCAST_WORK_DIR', None)
        sys.argv = self.orig_argv
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_workdir_exits(self):
        os.environ.pop('PODCAST_WORK_DIR', None)
        sys.argv = ['curate.py', 'test-model', 'jelasin-dong', 'Test Episode']
        with self.assertRaises(SystemExit):
            import curate
            curate  # just reference

    def test_missing_transcript_exits(self):
        os.remove(self.tmp / "transcript.json")
        sys.argv = ['curate.py', 'test-model', 'jelasin-dong', 'Test Episode']
        with patch("clip_quality.probe_source_duration", return_value=100):
            with self.assertRaises(FileNotFoundError):
                source = (REPO_DIR / 'curate.py').read_text(encoding='utf-8')
                exec(compile(source, str(REPO_DIR / 'curate.py'), 'exec'))

    def test_podcast_slug_default(self):
        # Should use 'unknown' when no slug given
        pass  # Validated at runtime only

    def test_curate_persists_snapped_validated_clips(self):
        segments = [
            {"start": float(start), "end": float(start + 10), "text": f"Segment {start}"}
            for start in range(0, 100, 10)
        ]
        (self.tmp / "transcript.json").write_text(json.dumps(segments), encoding="utf-8")
        generated = {
            "episode_summary": "Ringkasan episode.",
            "x_post": {"text": "Hook episode", "hashtags": ["#podcast"]},
            "clips": [
                {
                    "start": 0.8,
                    "end": 40.7,
                    "title": f"Moment {index}",
                    "hook": "Hook yang tidak ada di transkrip",
                }
                for index in range(1, 7)
            ],
        }
        api_response = {
            "choices": [{"message": {"content": json.dumps(generated)}}]
        }
        with patch("clip_quality.probe_source_duration", return_value=100):
            with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(api_response).encode())):
                with patch.object(sys, "argv", ["curate.py", "ignored", "jelasin-dong", "Test Episode"]):
                    runpy.run_path(str(REPO_DIR / "curate.py"), run_name="__main__")

        saved = json.loads((self.tmp / "clips.json").read_text(encoding="utf-8"))
        self.assertEqual((saved[0]["start"], saved[0]["end"]), (0.0, 40.0))
        self.assertEqual(saved[0]["hook"], "Segment 0")


class TestCaptionScript(unittest.TestCase):
    """Test 5: caption.py — validation & edge cases."""
    
    def setUp(self):
        self.tmp = create_temp_episode()
        self.orig_workdir = os.environ.get('PODCAST_WORK_DIR')
        self.orig_argv = sys.argv
        os.environ['PODCAST_WORK_DIR'] = str(self.tmp)
        sys.argv = ['caption.py']

    def tearDown(self):
        if self.orig_workdir:
            os.environ['PODCAST_WORK_DIR'] = self.orig_workdir
        else:
            os.environ.pop('PODCAST_WORK_DIR', None)
        sys.argv = self.orig_argv
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_workdir_exits(self):
        os.environ.pop('PODCAST_WORK_DIR', None)
        with self.assertRaises(SystemExit):
            exec(compile('import caption; caption', 'caption.py', 'exec'))

    def test_search_results_structure(self):
        """Verify search_results.json uses string keys."""
        with open(self.tmp / "search_results.json") as f:
            data = json.load(f)
        for key in data:
            self.assertIsInstance(key, str)

    def test_missing_api_key_exits_without_writing_captions(self):
        env = os.environ.copy()
        env.pop("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY", None)
        env["PODCAST_WORK_DIR"] = str(self.tmp)
        with tempfile.TemporaryDirectory() as fake_home:
            env["HOME"] = fake_home
            result = subprocess.run(
                [sys.executable, str(REPO_DIR / "caption.py")],
                cwd=REPO_DIR,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("API key not found", result.stderr)
        self.assertFalse((self.tmp / "captions.json").exists())


class TestEpisodeRunner(unittest.TestCase):
    def test_source_candidates_ignore_partial_downloads(self):
        from run_one_episode import source_candidates

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.mp4.part").write_bytes(b"partial")
            (root / "source.ytdl").write_bytes(b"state")
            media = root / "source.webm"
            media.write_bytes(b"complete")
            self.assertEqual(source_candidates(root), [media])


class TestCompilationWorker(unittest.TestCase):
    def test_success_replaces_final_output_atomically_and_releases_lock(self):
        from scripts import build_compilation

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workdir = root / "episode"
            workdir.mkdir()
            job_output = workdir / "clips" / ".job.mp4"
            final_output = workdir / "clips" / "top5_compilation.mp4"
            config = workdir / ".job.json"
            log = root / "job.log"
            lock = workdir / ".compilation-build.lock"
            config.write_text("{}", encoding="utf-8")
            lock.write_text("job", encoding="utf-8")

            def render(*args, **kwargs):
                job_output.parent.mkdir(parents=True, exist_ok=True)
                job_output.write_bytes(b"new compilation")
                return type("Result", (), {"returncode": 0})()

            argv = [
                "build_compilation.py", str(workdir), str(job_output),
                str(final_output), str(config), str(log), str(lock),
            ]
            with patch.object(sys, "argv", argv), patch.object(
                build_compilation.subprocess, "run", side_effect=render
            ):
                self.assertIsNone(build_compilation.main())

            self.assertEqual(final_output.read_bytes(), b"new compilation")
            self.assertFalse(job_output.exists())
            self.assertFalse(lock.exists())
            self.assertIn("EXIT_CODE=0", log.read_text(encoding="utf-8"))


class TestSubtitleTiming(unittest.TestCase):
    def test_short_segment_does_not_overflow(self):
        from subtitle_timing import subtitle_chunks

        chunks = subtitle_chunks([{"start": 0, "end": 10, "text": "satu dua tiga"}], 0, 10)
        self.assertEqual(chunks, [(0, 10, "satu dua tiga")])

    def test_boundary_overlaps_and_last_partial_chunk(self):
        from subtitle_timing import subtitle_chunks

        segments = [
            {"start": 0, "end": 5, "text": "excluded before"},
            {"start": 4, "end": 10, "text": "one two three four five"},
            {"start": 10, "end": 16, "text": "last sentence"},
            {"start": 15, "end": 20, "text": "excluded after"},
        ]
        chunks = subtitle_chunks(segments, 5, 15)
        self.assertEqual(chunks, [
            (0, 4, "one two three four"), (4, 5, "five"), (5, 10, "last sentence"),
        ])

    def test_blank_text_is_omitted(self):
        from subtitle_timing import subtitle_chunks

        self.assertEqual(subtitle_chunks([{"start": 0, "end": 1, "text": "  "}], 0, 1), [])


class TestNewsOverlay(unittest.TestCase):
    def test_source_card_is_timed_at_clip_end_and_uses_readable_sources(self):
        from news_overlay import build_news_overlay_event

        sources = [
            {"title": "Panja dibentuk untuk mengawasi kasus korupsi", "url": "https://www.antaranews.com/news/1"},
            {"title": "KPK dan Kejaksaan Agung berseteru", "url": "https://www.kompas.id/article/2"},
            {"title": "Sumber ketiga", "url": "https://rmol.id/news/3"},
            {"title": "Sumber keempat tidak ditampilkan", "url": "https://tempo.co/news/4"},
        ]
        event = build_news_overlay_event(sources, 62)

        self.assertIn("0:00:56.00,0:01:02.00", event)
        self.assertIn(r"\pos(64,120)", event)
        self.assertIn("ANTARANEWS", event)
        self.assertIn("KOMPAS", event)
        self.assertIn("RMOL", event)
        self.assertNotIn("tempo.co", event)
        self.assertIn("Link lengkap di caption", event)
        self.assertNotIn("https://", event)

    def test_missing_or_invalid_sources_do_not_create_card(self):
        from news_overlay import build_news_overlay_event

        self.assertIsNone(build_news_overlay_event([], 62))
        self.assertIsNone(build_news_overlay_event([{"title": "Bad URL", "url": "javascript:alert(1)"}], 62))
        with self.assertRaises(ValueError):
            build_news_overlay_event([], float("nan"))


class TestUploadScripts(unittest.TestCase):
    """Test 7: Upload scripts — path handling, edge cases."""
    
    def setUp(self):
        self.tmp = create_temp_episode()
        Path(self.tmp / "clips").mkdir(exist_ok=True)
        # Create dummy clip files
        for name in ["clip01.mp4", "clip02.mp4"]:
            Path(self.tmp / "clips" / name).write_text("fake-video-data")
        # Create dummy captions.json (normally created by caption.py)
        with open(self.tmp / "captions.json", "w") as f:
            json.dump({
                "1": {"clip": "clip01.mp4", "title": "Klip 1", "caption": "Caption 1"},
                "2": {"clip": "clip02.mp4", "title": "Klip 2", "caption": "Caption 2"},
            }, f)
        (self.tmp / "episode_manifest.json").write_text(json.dumps({
            "status": "ready_for_review", "review_status": "pending",
            "clips": [
                {"filename": name, "sha256": hashlib.sha256((self.tmp / "clips" / name).read_bytes()).hexdigest(),
                 "platform_upload_ids": {}}
                for name in ("clip01.mp4", "clip02.mp4")
            ],
        }), encoding="utf-8")
        
        self.orig_workdir = os.environ.get('PODCAST_WORK_DIR')
        os.environ['PODCAST_WORK_DIR'] = str(self.tmp)

    def tearDown(self):
        if self.orig_workdir:
            os.environ['PODCAST_WORK_DIR'] = self.orig_workdir
        else:
            os.environ.pop('PODCAST_WORK_DIR', None)
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_captions_json_key_type(self):
        """Upload scripts read caps dict keys as strings."""
        with open(self.tmp / "captions.json") as f:
            caps = json.load(f)
        for key in caps:
            self.assertIsInstance(key, str)

    def load_tiktok_uploader(self, script_path):
        module_name = "tiktok_upload_" + script_path.replace("/", "_").replace(".", "_")
        spec = importlib.util.spec_from_file_location(module_name, REPO_DIR / script_path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"PODCAST_WORK_DIR": str(self.tmp)}):
            spec.loader.exec_module(module)
        return module

    def test_tiktok_uploaders_accept_root_captions(self):
        # Single maintained implementation: scripts/tiktok_upload.py.
        # (hermes-skill copy is a thin shim — no longer importable as a module.)
        for script_path in ("scripts/tiktok_upload.py",):
            with self.subTest(script=script_path):
                module = self.load_tiktok_uploader(script_path)
                with patch.object(module, "upload_clip", return_value={"status": "inbox"}) as upload:
                    with redirect_stdout(StringIO()):
                        status = module.main(["all"])
                self.assertEqual(status, 0)
                self.assertEqual(upload.call_count, 2)

    def test_tiktok_batch_returns_failure_when_any_upload_fails(self):
        module = self.load_tiktok_uploader("scripts/tiktok_upload.py")
        with patch.object(
            module,
            "upload_clip",
            side_effect=[{"status": "inbox"}, {"error": "API rejected upload"}],
        ):
            with redirect_stdout(StringIO()):
                status = module.main(["all"])
        self.assertEqual(status, 1)

    def test_tiktok_records_publish_id_and_skips_existing_inbox_item(self):
        from unittest.mock import Mock
        module = self.load_tiktok_uploader("scripts/tiktok_upload.py")
        init_response = Mock(status_code=200)
        init_response.json.return_value = {"data": {"upload_url": "https://upload.invalid/file", "publish_id": "publish-123"}}
        inbox_response = Mock(status_code=200)
        inbox_response.json.return_value = {"data": {}}
        put_response = Mock(status_code=200)
        with patch.object(module, "get_token", return_value=("fake-token", "fake-open-id")), \
             patch.object(module.requests, "post", side_effect=[init_response, inbox_response]), \
             patch.object(module.requests, "put", return_value=put_response):
            result = module.upload_clip(self.tmp / "clips" / "clip01.mp4", "Caption", "1")
        self.assertEqual(result["status"], "inbox")
        manifest = json.loads((self.tmp / "episode_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["clips"][0]["platform_upload_ids"]["tiktok"],
            {"publish_id": "publish-123", "status": "inbox"},
        )
        with patch.object(module, "upload_clip") as upload, redirect_stdout(StringIO()):
            self.assertEqual(module.main(["1"]), 0)
        upload.assert_not_called()

    def test_youtube_batch_returns_failure_when_any_upload_fails(self):
        module_name = "youtube_upload_test_status"
        spec = importlib.util.spec_from_file_location(
            module_name, REPO_DIR / "scripts/youtube_upload.py"
        )
        module = importlib.util.module_from_spec(spec)
        token_path = self.tmp / "youtube-token.json"
        token_path.write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {
            "PODCAST_WORK_DIR": str(self.tmp),
            "YOUTUBE_TOKEN_FILE": str(token_path),
        }):
            spec.loader.exec_module(module)
        manifest_path = self.tmp / "episode_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["review_status"] = "approved"
        for item in manifest["clips"]:
            item["review_status"] = "approved"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with patch.object(
            module,
            "upload_clip",
            side_effect=[{"url": "https://youtu.be/abcdefghijk"}, {"error": "quota"}],
        ):
            with redirect_stdout(StringIO()):
                status = module.main(["all"])
        self.assertEqual(status, 1)

    def test_youtube_release_strips_news_urls_and_requires_manifest_match(self):
        import hashlib
        module_name = "youtube_upload_test_review_gate"
        spec = importlib.util.spec_from_file_location(module_name, REPO_DIR / "scripts/youtube_upload.py")
        module = importlib.util.module_from_spec(spec)
        token_path = self.tmp / "youtube-token.json"
        token_path.write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {
            "PODCAST_WORK_DIR": str(self.tmp), "YOUTUBE_TOKEN_FILE": str(token_path),
        }):
            spec.loader.exec_module(module)
        self.assertEqual(module._youtube_description("News https://example.com/story\nSecond line"), "News\nSecond line")

        clip_path = self.tmp / "clips" / "clip01.mp4"
        digest = hashlib.sha256(clip_path.read_bytes()).hexdigest()
        manifest = {
            "status": "ready_for_review", "review_status": "approved",
            "clips": [{"filename": "clip01.mp4", "sha256": digest,
                       "review_status": "approved", "platform_upload_ids": {}}],
        }
        (self.tmp / "episode_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        # A caption cannot smuggle a different file into an otherwise approved release.
        with open(self.tmp / "captions.json", "w", encoding="utf-8") as f:
            json.dump({"1": {"clip": "clip02.mp4", "title": "Klip", "caption": "Caption"}}, f)
        with patch.object(module, "upload_clip") as upload, redirect_stdout(StringIO()):
            self.assertEqual(module.main(["all"]), 1)
        upload.assert_not_called()

    def test_youtube_upload_blocks_token_for_unexpected_channel(self):
        from unittest.mock import Mock

        module_name = "youtube_upload_test_channel_identity"
        spec = importlib.util.spec_from_file_location(module_name, REPO_DIR / "scripts/youtube_upload.py")
        module = importlib.util.module_from_spec(spec)
        token_path = self.tmp / "youtube-token.json"
        token_path.write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {
            "PODCAST_WORK_DIR": str(self.tmp),
            "YOUTUBE_TOKEN_FILE": str(token_path),
            "YOUTUBE_EXPECTED_CHANNEL_ID": "expected-channel-id",
        }):
            spec.loader.exec_module(module)

        creds = Mock(valid=True)
        service = Mock()
        service.channels.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "wrong-channel-id"}],
        }
        with patch.dict(os.environ, {"YOUTUBE_EXPECTED_CHANNEL_ID": "expected-channel-id"}), \
             patch.object(module.Credentials, "from_authorized_user_file", return_value=creds), \
             patch.object(module, "build", return_value=service):
            result = module.upload_clip(
                self.tmp / "clips" / "clip01.mp4", "Caption", "Description",
                release_approved=True,
            )

        self.assertIn("does not match YOUTUBE_EXPECTED_CHANNEL_ID", result["error"])
        service.videos.assert_not_called()

    def test_youtube_channel_identity_check_requires_configured_id(self):
        from unittest.mock import Mock

        module_name = "youtube_upload_test_missing_channel_id"
        spec = importlib.util.spec_from_file_location(module_name, REPO_DIR / "scripts/youtube_upload.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"PODCAST_WORK_DIR": str(self.tmp)}, clear=False):
            os.environ.pop("YOUTUBE_EXPECTED_CHANNEL_ID", None)
            spec.loader.exec_module(module)
        with self.assertRaisesRegex(ValueError, "Set YOUTUBE_EXPECTED_CHANNEL_ID"):
            module._verify_youtube_channel(Mock())

    def load_youtube_uploader(self):
        spec = importlib.util.spec_from_file_location("youtube_metadata_tests", REPO_DIR / "scripts/youtube_upload.py")
        module = importlib.util.module_from_spec(spec)
        token_path = self.tmp / "youtube-token.json"
        token_path.write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {"PODCAST_WORK_DIR": str(self.tmp), "YOUTUBE_TOKEN_FILE": str(token_path)}):
            spec.loader.exec_module(module)
        return module

    def test_youtube_metadata_defaults_and_title_limits(self):
        module = self.load_youtube_uploader()
        title, description, tags, category = module._clean_metadata(
            "Long " * 30 + " #SHORTS #Shorts", "Caption https://example.com/article", None, 25,
        )
        self.assertLessEqual(len(title), 100)
        self.assertEqual(title.lower().count("#shorts"), 1)
        self.assertTrue(title.endswith(" #Shorts"))
        self.assertEqual(description, "Caption")
        self.assertEqual(len(tags), 15)
        self.assertEqual(category, "25")
        self.assertEqual(module._clean_metadata("Title", "", ["News", " news ", "custom"], 25)[2], ["News", "custom"])

    def test_youtube_metadata_verifies_all_persisted_fields(self):
        from unittest.mock import Mock
        module = self.load_youtube_uploader()
        snippet = {"title": "Title #Shorts", "description": "Description", "categoryId": "25", "tags": ["first", "second"]}
        good = {"items": [{"id": "video123", "snippet": snippet}]}
        variants = [good, {"items": []}]
        for field in snippet:
            variants.append({"items": [{"id": "video123", "snippet": {key: value for key, value in snippet.items() if key != field}}]})
        variants.append({"items": [{"id": "video123", "snippet": {**snippet, "tags": ["first"]}}]})
        for index, payload in enumerate(variants):
            with self.subTest(index=index), \
                 patch.object(module._requests, "put", return_value=Mock(status_code=200)) as put, \
                 patch.object(module._requests, "get", return_value=Mock(status_code=200, json=lambda: payload)) as get:
                if index == 0:
                    self.assertTrue(module._update_video_metadata_raw(Mock(valid=True, token="test"), "video123", snippet["title"], snippet["description"], snippet["tags"], "25"))
                else:
                    with self.assertRaises(RuntimeError):
                        module._update_video_metadata_raw(Mock(valid=True, token="test"), "video123", snippet["title"], snippet["description"], snippet["tags"], "25")
                self.assertEqual(put.call_args.kwargs["json"]["snippet"], snippet)
                get.assert_called_once()

    def test_youtube_persists_insert_id_then_retries_metadata_in_place(self):
        from unittest.mock import Mock
        module = self.load_youtube_uploader()
        service = Mock()
        service.videos.return_value.insert.return_value.execute.return_value = {"id": "existing123"}
        recorded = []
        def metadata_check(*args):
            self.assertEqual(recorded, ["existing123"])
            raise RuntimeError("tags missing")
        with patch.object(module.Credentials, "from_authorized_user_file", return_value=Mock(valid=True)), \
             patch.object(module, "build", return_value=service), \
             patch.object(module, "_verify_youtube_channel"), \
             patch.object(module, "MediaFileUpload"), \
             patch.object(module, "_update_video_metadata_raw", side_effect=metadata_check):
            first = module.upload_clip(self.tmp / "clips/clip01.mp4", "Title", "Description",
                                       release_approved=True, on_insert=recorded.append)
        self.assertTrue(first["metadata_update_failed"])
        self.assertEqual(first["video_id"], "existing123")
        manifest = json.loads((self.tmp / "episode_manifest.json").read_text())
        self.assertEqual(manifest["clips"][0]["platform_upload_ids"]["youtube"]["metadata_status"], "pending")
        service.reset_mock()
        with patch.object(module.Credentials, "from_authorized_user_file", return_value=Mock(valid=True)), \
             patch.object(module, "build", return_value=service), \
             patch.object(module, "_verify_youtube_channel"), \
             patch.object(module, "_update_video_metadata_raw", return_value=True):
            repaired = module.upload_clip(self.tmp / "clips/clip01.mp4", "Title", "Description",
                                          release_approved=True, existing_video_id="existing123", on_insert=recorded.append)
        service.videos.assert_not_called()
        self.assertNotIn("metadata_update_failed", repaired)
        self.assertEqual(recorded, ["existing123"])
        manifest = json.loads((self.tmp / "episode_manifest.json").read_text())
        self.assertEqual(manifest["clips"][0]["platform_upload_ids"]["youtube"]["metadata_status"], "complete")

    def test_youtube_cli_reports_pending_metadata_as_failure(self):
        module = self.load_youtube_uploader()
        module._approve_manifest_release()
        for args in (["1"], ["all"]):
            with self.subTest(args=args), redirect_stdout(StringIO()), \
                 patch.object(module, "upload_clip", return_value={"video_id": "existing123", "url": "https://youtu.be/existing123", "metadata_update_failed": True, "warning": "tags missing"}):
                self.assertEqual(module.main(args), 1)

    def test_youtube_retry_repairs_metadata_without_duplicate_insert(self):
        import hashlib
        module_name = "youtube_upload_test_retry"
        spec = importlib.util.spec_from_file_location(module_name, REPO_DIR / "scripts/youtube_upload.py")
        module = importlib.util.module_from_spec(spec)
        token_path = self.tmp / "youtube-token.json"
        token_path.write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {
            "PODCAST_WORK_DIR": str(self.tmp), "YOUTUBE_TOKEN_FILE": str(token_path),
        }):
            spec.loader.exec_module(module)
        clip_path = self.tmp / "clips" / "clip01.mp4"
        digest = hashlib.sha256(clip_path.read_bytes()).hexdigest()
        manifest = {
            "status": "ready_for_review", "review_status": "approved",
            "clips": [{"filename": "clip01.mp4", "sha256": digest, "review_status": "approved",
                       "platform_upload_ids": {"youtube": {"video_id": "existing123", "metadata_status": "pending"}}}],
        }
        (self.tmp / "episode_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with patch.object(module, "_retry_existing_video_metadata") as repair, \
             patch.object(module, "upload_clip") as upload, redirect_stdout(StringIO()):
            self.assertEqual(module.main(["1"]), 0)
        repair.assert_called_once()
        upload.assert_not_called()

    def test_scheduled_youtube_retry_uploads_only_digest_approved_media(self):
        import hashlib
        from datetime import datetime, timedelta, timezone
        from unittest.mock import Mock
        from app.admin_store import (
            approve_youtube_release, enqueue_youtube_retry, get_youtube_retry,
            get_youtube_upload,
        )
        from scripts import process_youtube_queue

        with patch.dict(os.environ, {"ADMIN_DB_PATH": str(self.tmp / "queue.sqlite3")}):
            clips_root = self.tmp / "deployed"
            episode = clips_root / "bocor-alus" / "episode-one"
            episode.mkdir(parents=True)
            (episode / "captions.json").write_text(json.dumps({
                "1": {"clip": "clip01.mp4", "title": "Reviewed clip", "caption": "Caption"},
            }), encoding="utf-8")
            media = episode / "clip01.mp4"
            media.write_bytes(b"approved media")
            digest = hashlib.sha256(media.read_bytes()).hexdigest()
            approve_youtube_release("bocor-alus", "episode-one", "1", digest)
            due = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
            enqueue_youtube_retry("bocor-alus", "episode-one", "1", retry_at=due, error="quota")
            with patch.object(process_youtube_queue, "YOUTUBE_CLIPS_ROOT", clips_root):
                result = process_youtube_queue.process_due(
                    uploader=Mock(return_value={"video_id": "scheduled123", "url": "https://youtu.be/scheduled123"})
                )
            self.assertEqual(result, 0)
            self.assertEqual(get_youtube_retry("bocor-alus", "episode-one", "1")["status"], "completed")
            self.assertEqual(get_youtube_upload("bocor-alus", "episode-one", "1")["video_id"], "scheduled123")

    def test_scheduled_youtube_retry_rejects_changed_media_without_upload(self):
        import hashlib
        from datetime import datetime, timedelta, timezone
        from unittest.mock import Mock
        from app.admin_store import approve_youtube_release, enqueue_youtube_retry, get_youtube_retry
        from scripts import process_youtube_queue

        with patch.dict(os.environ, {"ADMIN_DB_PATH": str(self.tmp / "queue-changed.sqlite3")}):
            clips_root = self.tmp / "deployed-changed"
            episode = clips_root / "bocor-alus" / "episode-one"
            episode.mkdir(parents=True)
            (episode / "captions.json").write_text(json.dumps({
                "1": {"clip": "clip01.mp4", "title": "Reviewed clip", "caption": "Caption"},
            }), encoding="utf-8")
            media = episode / "clip01.mp4"
            media.write_bytes(b"first version")
            approved_digest = hashlib.sha256(media.read_bytes()).hexdigest()
            approve_youtube_release("bocor-alus", "episode-one", "1", approved_digest)
            media.write_bytes(b"changed version")
            due = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
            enqueue_youtube_retry("bocor-alus", "episode-one", "1", retry_at=due, error="quota")
            upload = Mock()
            with patch.object(process_youtube_queue, "YOUTUBE_CLIPS_ROOT", clips_root), \
                 redirect_stderr(StringIO()):
                result = process_youtube_queue.process_due(uploader=upload)
            self.assertEqual(result, 1)
            upload.assert_not_called()
            self.assertEqual(get_youtube_retry("bocor-alus", "episode-one", "1")["status"], "failed")

    def test_scheduled_youtube_retry_preserves_pending_video_id(self):
        from datetime import datetime, timedelta, timezone
        from unittest.mock import Mock
        from app.admin_store import (
            approve_youtube_release, enqueue_youtube_retry, get_youtube_retry,
            get_youtube_upload, record_youtube_upload,
        )
        from scripts import process_youtube_queue
        with patch.dict(os.environ, {"ADMIN_DB_PATH": str(self.tmp / "metadata-queue.sqlite3")}):
            root = self.tmp / "metadata-deployed"
            episode = root / "bocor-alus/episode-one"
            episode.mkdir(parents=True)
            media = episode / "clip01.mp4"
            media.write_bytes(b"approved media")
            (episode / "captions.json").write_text(json.dumps({"1": {"clip": media.name, "title": "Title", "caption": "Description"}}))
            key = ("bocor-alus", "episode-one", "1")
            digest = hashlib.sha256(media.read_bytes()).hexdigest()
            approve_youtube_release(*key, digest)
            record_youtube_upload(*key, "metadata_pending", video_id="existing123", sha256=digest)
            due = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
            enqueue_youtube_retry(*key, retry_at=due)
            pending = Mock(return_value={"video_id": "existing123", "metadata_update_failed": True, "warning": "tags missing"})
            with patch.object(process_youtube_queue, "YOUTUBE_CLIPS_ROOT", root):
                self.assertEqual(process_youtube_queue.process_due(uploader=pending), 1)
            self.assertEqual(pending.call_args.kwargs["existing_video_id"], "existing123")
            self.assertEqual(get_youtube_upload(*key)["video_id"], "existing123")
            self.assertEqual(get_youtube_upload(*key)["status"], "metadata_pending")
            self.assertEqual(get_youtube_retry(*key)["status"], "queued")
            enqueue_youtube_retry(*key, retry_at=due)
            repaired = Mock(return_value={"video_id": "existing123", "url": "https://youtu.be/existing123"})
            with patch.object(process_youtube_queue, "YOUTUBE_CLIPS_ROOT", root):
                self.assertEqual(process_youtube_queue.process_due(uploader=repaired), 0)
            self.assertEqual(repaired.call_args.kwargs["existing_video_id"], "existing123")
            self.assertEqual(get_youtube_upload(*key)["status"], "uploaded")
            self.assertEqual(get_youtube_retry(*key)["status"], "completed")

    def test_scheduled_youtube_retry_requeues_quota_for_tomorrow(self):
        import hashlib
        from datetime import datetime, timedelta, timezone
        from unittest.mock import Mock
        from app.admin_store import approve_youtube_release, enqueue_youtube_retry, get_youtube_retry
        from scripts import process_youtube_queue

        with patch.dict(os.environ, {"ADMIN_DB_PATH": str(self.tmp / "queue-quota.sqlite3")}):
            clips_root = self.tmp / "deployed-quota"
            episode = clips_root / "bocor-alus" / "episode-one"
            episode.mkdir(parents=True)
            (episode / "captions.json").write_text(json.dumps({
                "1": {"clip": "clip01.mp4", "title": "Reviewed clip", "caption": "Caption"},
            }), encoding="utf-8")
            media = episode / "clip01.mp4"
            media.write_bytes(b"approved media")
            digest = hashlib.sha256(media.read_bytes()).hexdigest()
            approve_youtube_release("bocor-alus", "episode-one", "1", digest)
            due = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
            enqueue_youtube_retry("bocor-alus", "episode-one", "1", retry_at=due, error="quota")
            upload = Mock(return_value={"error": "uploadLimitExceeded"})
            with patch.object(process_youtube_queue, "YOUTUBE_CLIPS_ROOT", clips_root), \
                 patch.object(process_youtube_queue, "_retry_time", return_value="2099-01-01T00:00:00+00:00"), \
                 redirect_stderr(StringIO()):
                result = process_youtube_queue.process_due(uploader=upload)
            self.assertEqual(result, 0)
            self.assertEqual(get_youtube_retry("bocor-alus", "episode-one", "1")["status"], "queued")
            self.assertEqual(get_youtube_retry("bocor-alus", "episode-one", "1")["retry_at"], "2099-01-01T00:00:00+00:00")
            upload.assert_called_once()

    def test_tiktok_upload_shim_forwards(self):
        """Shim in hermes-skill/scripts/tiktok_upload.py forwards to the real script
        via subprocess. With no OAuth token, the real script must emit its expected
        authentication error without making a network request."""
        import subprocess as _sp
        shim = str(REPO_DIR / "hermes-skill/scripts/tiktok_upload.py")
        env = os.environ.copy()
        env["PODCAST_WORK_DIR"] = str(self.tmp)
        env.pop("TIKTOK_TOKEN_FILE", None)
        proc = _sp.run(
            [sys.executable, shim, "all"],
            cwd=str(self.tmp),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        combined = proc.stdout + proc.stderr
        # MUST NOT contain shim-level errors. If it does, forwarding failed.
        self.assertNotIn("target script missing", combined)
        self.assertNotIn("hermes-skill shim could not find", combined)
        # MUST contain something identifiable as the REAL tiktok_upload.py output
        # (its emoji ❌ error prefix, the OAuth URL, or captions file text).
        self.assertTrue(
            any(marker in combined for marker in (
                "❌",
                "Captions file not found",
                "Belum OAuth",
                "tiktok-auth",
                "access_token_invalid",
                "access_token is invalid",
            )),
            msg=f"Shim output looks wrong — no real tiktok_upload markers found. stdout/stderr: {combined}"
        )
        self.assertEqual(proc.returncode, 1)

    def test_tiktok_uploaders_fail_on_missing_inputs_before_upload(self):
        # Single maintained implementation: scripts/tiktok_upload.py.
        for script_path in ("scripts/tiktok_upload.py",):
            with self.subTest(script=script_path):
                module = self.load_tiktok_uploader(script_path)
                (self.tmp / "captions.json").unlink()
                with patch.object(module, "upload_clip") as upload:
                    output = StringIO()
                    with redirect_stdout(output):
                        status = module.main(["all"])
                self.assertEqual(status, 1)
                self.assertIn("Captions file not found", output.getvalue())
                upload.assert_not_called()
                with open(self.tmp / "captions.json", "w") as captions_file:
                    json.dump({"1": {"clip": "missing.mp4", "title": "Klip", "caption": "Caption"}}, captions_file)
                with patch.object(module, "upload_clip") as upload:
                    output = StringIO()
                    with redirect_stdout(output):
                        status = module.main(["all"])
                self.assertEqual(status, 1)
                self.assertIn("Clip file for 1 not found", output.getvalue())
                upload.assert_not_called()
                with open(self.tmp / "captions.json", "w") as captions_file:
                    json.dump({
                        "1": {"clip": "clip01.mp4", "title": "Klip 1", "caption": "Caption 1"},
                        "2": {"clip": "clip02.mp4", "title": "Klip 2", "caption": "Caption 2"},
                    }, captions_file)


class TestMonitorScript(unittest.TestCase):
    """Test 8: monitor.py — parsing logic."""
    
    def test_playlist_ids(self):
        """Verify playlist IDs are present in script."""
        with open(REPO_DIR / "monitor.py") as f:
            content = f.read()
        self.assertIn("PLkiSnq8pdz9TC0rHIiguTsgelilKeR3pw", content)
        self.assertIn("PLkiSnq8pdz9T3QQVbczz4XVgsHjjJK3vd", content)
        self.assertIn("PLkiSnq8pdz9SBWNzd65VKlI4uzLv4_p41", content)

    def test_discovery_records_without_completing_and_emits_compatible_line(self):
        import monitor
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            with patch.object(monitor, "STATE_DIR", state_dir), \
                    patch.object(monitor, "STATE_FILE", state_dir / "episodes.json"), \
                    patch.object(monitor, "LOCK_FILE", state_dir / "lock"), \
                    patch.object(monitor, "LEGACY_STATE_FILE", Path(temp_dir) / "legacy.txt"), \
                    patch.object(monitor, "PLAYLISTS", {"jelasin-dong": "playlist"}), \
                    patch.object(monitor, "_fetch_playlist", return_value=[{
                        "video_id": "video1234567", "title": "Episode baru",
                        "published": "2026-10-07T00:00:00Z", "podcast": "jelasin-dong",
                    }]):
                output = StringIO()
                with redirect_stdout(output):
                    monitor.discover()
                state = json.loads((state_dir / "episodes.json").read_text())
                self.assertEqual(state["video1234567"]["status"], "discovered")
                self.assertIn("NEW:jelasin-dong:video1234567:Episode baru", output.getvalue())

                output = StringIO()
                with redirect_stdout(output):
                    monitor.discover()
                self.assertIn("NEW:jelasin-dong:video1234567:Episode baru", output.getvalue())

                monitor._set_status("video1234567", "in_progress")
                monitor._set_status("video1234567", "failed", "worker failed")
                output = StringIO()
                with redirect_stdout(output):
                    monitor.discover()
                self.assertIn("NEW:jelasin-dong:video1234567:Episode baru", output.getvalue())
                monitor._set_status("video1234567", "in_progress")
                monitor._set_status("video1234567", "completed")
                output = StringIO()
                with redirect_stdout(output):
                    monitor.discover()
                self.assertEqual(output.getvalue().strip(), "NO_NEW")

    def test_lifecycle_retries_failures_and_rejects_completed_reprocessing(self):
        import monitor
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            state_dir.mkdir()
            paths = {
                "STATE_DIR": state_dir,
                "STATE_FILE": state_dir / "episodes.json",
                "LOCK_FILE": state_dir / "lock",
                "LEGACY_STATE_FILE": Path(temp_dir) / "legacy.txt",
            }
            with patch.multiple(monitor, **paths):
                monitor._write_state_raw({"video1234567": {
                    "video_id": "video1234567", "status": "discovered", "attempts": 0,
                }})
                with redirect_stdout(StringIO()):
                    monitor._set_status("video1234567", "in_progress")
                    monitor._set_status("video1234567", "failed", "render failed")
                    monitor._retry_failed()
                    monitor._set_status("video1234567", "in_progress")
                    monitor._set_status("video1234567", "completed")
                state = json.loads((state_dir / "episodes.json").read_text())
                self.assertEqual(state["video1234567"]["status"], "completed")
                self.assertEqual(state["video1234567"]["attempts"], 2)
                with self.assertRaises(SystemExit):
                    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                        monitor._set_status("video1234567", "failed")
                self.assertEqual(json.loads((state_dir / "episodes.json").read_text())[
                    "video1234567"]["status"], "completed")

    def test_atomic_state_write_preserves_previous_file_on_replace_failure(self):
        import monitor
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            with patch.object(monitor, "STATE_DIR", state_dir), \
                    patch.object(monitor, "STATE_FILE", state_dir / "episodes.json"):
                monitor._write_state_raw({"old": {"status": "completed"}})
                with patch.object(monitor.os, "replace", side_effect=OSError("disk error")):
                    with self.assertRaises(OSError):
                        monitor._write_state_raw({"new": {"status": "discovered"}})
                self.assertEqual(json.loads((state_dir / "episodes.json").read_text()),
                                 {"old": {"status": "completed"}})
                self.assertEqual(list(state_dir.glob(".state-*")), [])


class TestGitignore(unittest.TestCase):
    """Test 9: .gitignore mencakup file sensitif."""
    
    def test_secrets_excluded(self):
        with open(REPO_DIR / ".gitignore") as f:
            content = f.read()
        self.assertIn(".env", content)
        self.assertIn("*_token.json", content)
        self.assertIn("credentials/", content)
        self.assertIn("*.mp4", content)


class TestTemplateRendering(unittest.TestCase):
    """Test 10: Template ada dan bisa di-render."""
    
    @classmethod
    def setUpClass(cls):
        cls.templates_dir = REPO_DIR / "app" / "templates"

    def test_clips_html_exists(self):
        self.assertTrue((self.templates_dir / "clips.html").exists())
    
    def test_index_html_exists(self):
        self.assertTrue((self.templates_dir / "index.html").exists())

    def test_clips_html_has_required_vars(self):
        """Template menerima variabel yang dibutuhkan."""
        with open(self.templates_dir / "clips.html") as f:
            content = f.read()
        self.assertIn("{{ podcast }}", content)
        self.assertIn("{{ episode }}", content)
        self.assertIn("captions.get(", content)
        self.assertIn("clips_meta", content)


# ── CLI Check ──────────────────────────────────────────────────────────────

def check_ffmpeg():
    """Check ffmpeg dan ffprobe tersedia."""
    import shutil
    missing = []
    for cmd in ['ffmpeg', 'ffprobe']:
        if not shutil.which(cmd):
            missing.append(cmd)
    return missing


def check_yunet_model():
    """Check YuNet ONNX model exists."""
    return os.path.exists("/tmp/face_yunet.onnx")


# ── Runner ─────────────────────────────────────────────────────────────────

def print_header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

if __name__ == '__main__':
    print_header("PODCAST CLIPS — TEST SUITE")
    print(f"  Repo: {REPO_DIR}")
    print(f"  Python: {sys.version}")
    print()
    
    # 1. Environment checks
    print_header("🔧 Environment Checks")
    
    missing = check_ffmpeg()
    if missing:
        print(f"  ⚠️  Missing system deps: {', '.join(missing)}")
    else:
        print(f"  ✅ ffmpeg & ffprobe tersedia")
    
    if check_yunet_model():
        print(f"  ✅ YuNet face model ada di /tmp/face_yunet.onnx")
    else:
        print(f"  ⚠️  YuNet model tidak ada (cut_smart.py akan fail)")
    
    # 2. Python unit tests
    print_header("🧪 Unit Tests")
    
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2, stream=StringIO())
    result = runner.run(suite)
    
    # Print test results
    result_str = result.stream.getvalue()
    print(result_str)
    
    # 3. Summary
    print_header("📊 Summary")
    print(f"  Tests run: {result.testsRun}")
    print(f"  Passed:    {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  Failures:  {len(result.failures)}")
    print(f"  Errors:    {len(result.errors)}")
    
    if result.wasSuccessful():
        print(f"\n  ✅ SEMUA TEST PASS — Siap deploy!")
    else:
        print(f"\n  ❌ ADA TEST FAIL — Perbaiki sebelum deploy.")
        for test, trace in result.failures:
            print(f"\n  FAIL: {test}")
            for line in trace.split('\n')[-5:]:
                print(f"    {line}")
        for test, trace in result.errors:
            print(f"\n  ERROR: {test}")
            for line in trace.split('\n')[-5:]:
                print(f"    {line}")
    
    sys.exit(0 if result.wasSuccessful() else 1)
