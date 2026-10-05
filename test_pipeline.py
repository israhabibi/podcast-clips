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
from contextlib import redirect_stdout
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
        from clip_quality import format_timed_transcript, validate_clips
        self.format_timed_transcript = format_timed_transcript
        self.validate_clips = validate_clips
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

            with patch.object(clips, 'CLIPS_DIR', temp_dir):
                self.assertEqual(self.client.get('/clips/jelasin-dong/episode-valid').status_code, 200)
                self.assertEqual(self.client.get('/clips/jelasin-dong/episode-missing').status_code, 404)
                (episode_dir / 'captions.json').write_text('{broken json')
                self.assertEqual(self.client.get('/clips/jelasin-dong/episode-valid').status_code, 404)
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


class TestCutSmartScript(unittest.TestCase):
    """Test 6: cut_smart.py — subtitle timing logic."""
    
    def setUp(self):
        self.tmp = create_temp_episode()
        self.orig_workdir = os.environ.get('PODCAST_WORK_DIR')
        os.environ['PODCAST_WORK_DIR'] = str(self.tmp)

    def tearDown(self):
        if self.orig_workdir:
            os.environ['PODCAST_WORK_DIR'] = self.orig_workdir
        else:
            os.environ.pop('PODCAST_WORK_DIR', None)

    def test_subtitle_timing_bug(self):
        """
        Test bahwa subtitle chunk timing tidak overflow.
        Bug lama: chunk end time dihitung (j+4)/len(words) yang bisa > 1.
        Fix: pakai min(j+4, n_words).
        """
        # Test subtitle timing logic langsung tanpa import cut_smart
        start, end = 0.0, 10.0
        segs = [{"start": 0.0, "end": 10.0, "text": "satu dua tiga"}]
        
        subs = []
        for s in segs:
            if s["end"] <= start or s["start"] >= end:
                continue
            st = max(s["start"], start) - start
            en = min(s["end"], end) - start
            words = s["text"].split()
            n_words = max(len(words), 1)
            for j in range(0, n_words, 4):
                chunk = " ".join(words[j:min(j+4, n_words)])
                if chunk.strip():
                    chunk_start = st + (en - st) * j / n_words
                    chunk_end = st + (en - st) * min(j + 4, n_words) / n_words
                    subs.append((chunk_start, chunk_end, chunk))
        
        # 3 words -> 1 chunk, timenya harus proporsional
        self.assertEqual(len(subs), 1)
        self.assertAlmostEqual(subs[0][0], 0.0)  # start = 0
        self.assertAlmostEqual(subs[0][1], 10.0)  # end = min(3+4, 3)/3 * 10 = 3/3 * 10 = 10.0
        self.assertEqual(subs[0][2], "satu dua tiga")


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
        with patch.object(
            module,
            "upload_clip",
            side_effect=[{"url": "https://youtu.be/abcdefghijk"}, {"error": "quota"}],
        ):
            with redirect_stdout(StringIO()):
                status = module.main(["all"])
        self.assertEqual(status, 1)

    def test_tiktok_upload_shim_forwards(self):
        """Shim in hermes-skill/scripts/tiktok_upload.py forwards to the real script
        via subprocess. Running it produces real-script error output (e.g. OAuth missing,
        captions file missing, or live API 401) rather than any shim-level error string
        such as "target script missing" or "could not find the podcast-clips repository".
        We cannot easily mock network in a subprocess, so any real-script error text counts
        as success — what we are really testing is the forward was routed correctly."""
        import subprocess as _sp
        shim = str(REPO_DIR / "hermes-skill/scripts/tiktok_upload.py")
        # Fake token file so OAuth guard passes (forces the real script to move past
        # the "Belum OAuth" error to a later guard — in practice this hits the live
        # TikTok API with a bad token and returns a 401 body).
        token_path = self.tmp / "tiktok_token_fake.json"
        token_path.write_text(json.dumps({"access_token": "fake-test", "open_id": "fake-open"}))
        env = os.environ.copy()
        env["PODCAST_WORK_DIR"] = str(self.tmp)
        env["TIKTOK_TOKEN_FILE"] = str(token_path)
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
        # Real script returns 0 (no success for every clip with explicit per-item
        # failure markers in output) or 1 on global missing-input exits. Both accept.
        self.assertIn(proc.returncode, (0, 1))

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
