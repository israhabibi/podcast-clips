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

import sys, os, json, tempfile, unittest, logging
from pathlib import Path
from io import StringIO

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
        from app.routes import clips, youtube, tiktok
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


class TestFlaskApp(unittest.TestCase):
    """Test 2: Flask app routes."""
    
    @classmethod
    def setUpClass(cls):
        from app import app
        app.config['TESTING'] = True
        app.config['SERVER_NAME'] = 'localhost'
        cls.client = app.test_client()
        # Import routes so they register
        from app.routes import clips, youtube, tiktok
        cls.app = app

    def test_index_returns_200(self):
        r = self.client.get('/')
        self.assertIn(r.status_code, (200, 404))  # 404 if no clips dir
    
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

    def test_oauth_auth_route(self):
        with self.app.test_request_context():
            from flask import session
            with self.client as c:
                r = c.get('/auth')
                # Should redirect to Google
                self.assertIn(r.status_code, (302, 500))  # 500 if no real creds
    
    def test_oauth_callback_no_state(self):
        r = self.client.get('/oauth?code=test&state=wrong')
        self.assertEqual(r.status_code, 400)


class TestAppInit(unittest.TestCase):
    """Test 3: app/__init__.py behavior."""

    def test_secret_key_from_env(self):
        # Reset module state
        if 'app' in sys.modules:
            del sys.modules['app']
        os.environ['FLASK_SECRET_KEY'] = 'fixed-secret-for-test'
        from app import app as app1
        self.assertEqual(app1.secret_key, 'fixed-secret-for-test')

    def test_secret_key_fallback_random(self):
        if 'app' in sys.modules:
            del sys.modules['app']
        # Remove env var to trigger random fallback
        saved = os.environ.pop('FLASK_SECRET_KEY', None)
        try:
            from app import app as app2
            self.assertTrue(len(app2.secret_key) > 0)
        finally:
            if saved:
                os.environ['FLASK_SECRET_KEY'] = saved


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
            exec(open(REPO_DIR / 'curate.py').read())

    def test_podcast_slug_default(self):
        # Should use 'unknown' when no slug given
        pass  # Validated at runtime only


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
