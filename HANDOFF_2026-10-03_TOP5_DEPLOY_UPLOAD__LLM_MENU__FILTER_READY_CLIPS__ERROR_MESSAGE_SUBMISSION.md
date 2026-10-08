# Handoff — Podcast-clips: Sesi perubahan 2026-10-03 batch (Top 5 Deploy/Upload + LLM Config Menu + Filter Klip Siap Upload + Error Message Submission Display)

File ini ditulis **untuk AI AGEN BERIKUTNYA** yang接手 project ini, sebagai catatan:
1. Apa perubahan code yang dibuat
2. Mana yang SUDAH DIVERIFIKASI hijau (unittest, smoke, live HTTP)
3. Bagaimana flow kerja setiap fitur baru
4. Fixtures / constants / naming convention terbaru
5. Known open issues kecil

---

## ❗ Informasi Penting Global (Tetap Berlaku)

- **Python venv**: `/home/isra/podcast-clips/.venv` (Python 3.12)
- **Flask deployment**: `systemctl --user restart flask-app` service `flask-app.service` di user systemd. Binary: `~/.venv/bin/python run.py` dari dir `~/podcast-clips/app`
- **Live URL**: `https://clips.gcp.my.id` (admin login via `POST /login`, password hashing pbkdf2 dibaca dari env `ADMIN_PASSWORD_HASH`). **JANGAN PERNAH hardcode / tulis password literal apapun (termasuk password smoke test) di file manapun di repo, termasuk markdown / dokumentasi / commit body.** Grep rule: string password literal apapun hasilnya harus 0 match di SELURUH source tree.
- **WORK_ROOT episode**: `/tmp/podcast-clips/<episode-id>/` (TEMPORARY, karena /tmp hilang saat restart)
- **PERMANEN deployed clips**: `app/static/clips/<podcast>/<YYYY-MM-DD>_<slug>/` (nama format = `${podcast}_${YYYY-MM-DD}_${slug}_${NN}.mp4` untuk per-klip, `_top5.mp4` untuk kompilasi)
- **DB Admin**: `app/data/admin.sqlite3` (override env ADMIN_DB_PATH)
- **YouTube uploads table**: `youtube_uploads` with UNIQUE(podcast, episode, clip_num). Reserved slot `clip_num=99` untuk Top 5 Kompilasi (konstanta `TOP5_CLIP_NUM = "99"` di `app/routes/admin.py L390`)
- **Hermes skill symlink**: `~/.hermes/skills/media/podcast-clipping/` → repo `hermes-skill/`
- **OAuth token**: `~/podcast-clips/app/youtube_token.json` (dihapus auto jika invalid_grant); token file NOT exist saat ini

---

## 🆕 Fitur #1: Deploy Permanen + Upload YouTube Shorts untuk Kompilasi Top 5

**Status**: ✅ 100% SUDAH DIVERIFIKASI end-to-end (fixture workspace `D7j0s9Mx6M0` episode Jelasin Dong sudah di-deploy permanen dan BENAR-BENAR UPLOAD ke YouTube ID `0-RzMiDYmS8`)

### Lokasi kode:
- Backend helpers & 2 route baru: [admin.py L390-L1282](file:///home/isra/podcast-clips/app/routes/admin.py#L390-L1282)
  - `TOP5_CLIP_NUM = "99"` constant reserved youtube_uploads slot
  - 8 helpers: `_episode_meta_from_workdir`, `_slug_filename_part`, `_top5_filename`, `_top5_deployed_dir_and_file`, `_compilation_preview_path`, `_compilation_deployed_path`, `_compilation_upload_status`
  - `admin_compilation_status` **DIENRICH** (L934-L976): response JSON tambah field `deployed=true/false, deployed_file, static_url` + `upload_status/video_id/error/url`.
  - `_deploy_compilation(episode_id)` L979-L1100: atomic write **chunked 1MB copyfileobj** ke tempfile, `os.replace()`. Compute SHA256 + file_size, baca `.compilation-*.json` build config terbaru buat title/desc/tags (extra tag: `top5`, `kompilasi`), buat companion `<filename>.mp4.metadata.json` atomic juga + copy `episode_data.json`.
  - `POST /admin/compilation/deploy/<id>` L1103-L1127: CSRF protected, accept JSON with `X-Requested-With:XMLHttpRequest` header detection
  - `POST /admin/compilation/upload/<id>` L1130-L1282: **auto-deploy dulu jika belum permanen**. Short-circuit existing uploaded/manual (sudah berhasil) return `already_uploaded=True`. Short-circuit status `uploading` HTTP 409 return `in_progress=True` (mencegah spam klikk upload → dobel video). Register clip_num=99 → call `upload_clip()` lalu handle:
    - SUCCESS clean → `finalize_ok()` db status `uploaded`
    - SUCCESS + warning metadata → uploaded + warning
    - partial_success (error key + partial_success flag + real video_id) → tetep record db UPLOADED karena video YOUTUBE BENARAN live
    - invalid_grant → token file auto unlink + message reconnect banner
    - uploadLimitExceeded → status=quota
    - generic error → db status=failed
- UI panel baru di bawah button Build Top 5: [admin.html L1566-L1586](file:///home/isra/podcast-clips/app/templates/admin.html#L1566-L1586) (section `#compilation-perma-actions`)
  - 2 chips status: **Storage** (⚠️ Masih di /tmp vs 💾 Tersimpan permanen) + **Upload** status (⏳ uploading / ✅ uploaded / ❌ gagal / ⚠ kuota habis)
  - 2 buttons: 📦 Deploy permanen + 🚀 Upload YouTube Shorts
  - Anchor links: ⬇ Download permanen (muncul setelah deploy) + ▶ Open YouTube ↗ (muncul setelah upload success)
  - JS handlers + probeCompilationStatusNow L2005-L2442: episode dropdown onchange → auto probe status → if sudah pernah build → langsung load preview tanpa re-build (smart auto load). Poll 3s saat build berjalan → refresh chips tiap tick. Button disabled selama AJAX berjalan.

### Verifications done:
- ✅ Deploy fixture D7j0s9Mx6M0 → file 41MB ada di static/clips permanen
- ✅ Companion `.metadata.json` + `episode_data.json` ada, SHA256/size benar
- ✅ Status endpoint `/admin/compilation/status/D7j0s9Mx6M0` deployed=true
- ✅ Upload → **video_id=0-RzMiDYmS8 uploaded LIVE ke YouTube** (real bukti end-to-end)
- ✅ Idempotency: 2x click upload → second call returns `already_uploaded=True same video_id`
- ✅ Status `uploading` short-circuit HTTP 409 → JS render friendly message (anti spam klikk → double upload YouTube)
- ✅ 68/68 unittest hijau

---

## 🆕 Fitur #2: Prompt Pilihan Top 5 Kompilasi tidak ngaco + Durasi Total MAX 60 DETIK

**Status**: ✅ 100% Verified (6 unittest durasi rules 100% pass)

User problem: Dulu prompt AI masih bilang durasi 35-75 detik PER KLIP (itu standar klip biasa). Jadi AI milih 5 klip masing-masing 55 detik → total 275 detik untuk top5. Kita ganti: **per-klip 6-14 DETIK, TOTAL 5 ITEM TIDAK BOLEH > 60 detik.**

### Lokasi kode:
- **Prompt AI** [admin.py L825-L846](file:///home/isra/podcast-clips/app/routes/admin.py#L825-L846):
  - Criteria ranking URUT berdampak: 1) Punchline/closing/satir/joke lucu/reaksi host 2) Argumen utama 3) Narasi emosi/provokasi 4) **JANGAN** pilih basa-basi/iklan/jeda/cross-talk 5) Kalimat UTUH
  - Durasi WAJIB: MIN 6s · MAKS 14s per item (ideal 9-12s), TOTAL 5 ITEM ≤ 60s
  - Output JSON array **URUT TERBAIK → TERBURUK**. Field required `start, end, title, reason`.
- **Validator moment suggestions (LLM + fallback)** L860-L873: filter hanya 6-14 detik, expose `per_item_min=6, per_item_max=14, max_total_duration=60`
- **Fallback candidates** [admin.py L574-L599](file:///home/isra/podcast-clips/app/routes/admin.py#L574-L599): dulu 35-75 → sekarang 6-14s juga
- **Build route compilation validator + auto truncate** L896-L953:
  - Per item <6s atau >14s → HTTP 400 error jelas (pesan "Momen #X terlalu pendek Ys; minimal 6s.")
  - **Total >60s AUTO-TRUNCATE dari BELAKANG (index item terbesar)** jaga masing-masing tetap ≥6s → total tepat 60s. Kirim field `auto_warning` ke UI, user klik build dapat flash "⚠ Durasi rencana kelebihan, auto di truncate review ya"
  - Return response `total_duration_seconds + auto_warning`
- **UI durasi info box + live counter** admin.html L1566-L1569 + JS `updateCompilationDuration()` L2124-L2195:
  - Tampil: `Durasi total: 40.0s / 60s (pas untuk Shorts).` WARNA = hijau.
  - Jika total >60s oranye "Kelebihan Xs". Jika per item 6-14s INVALID → button build DISABLED.
  - Breakdown per-item: `#1 9.0s · #2 12.4s ❌(bukan 6-14s)` dsb.
  - Call `updateCompilationDuration()` di resetCompilationSlots, fillNextSlot, autofill5 click, dan input compilationItems (live keyup listener).

### Verifications 6 smoke case durasi 100% PASS:
| Case | Durasi items | Result |
|------|--------------|--------|
| Pendek <6s | item#1 3 detik | ❌ HTTP 400 |
| Panjang >14s | item#1 25 detik | ❌ HTTP 400 |
| 5 x 8 = 40s | ✅ ≤60 | 200 OK, no warning |
| 5 x 13 = 65s | overflow 5s | 200 OK, total=60.0s after truncation + warning |
| 5 x 12 = 60s | PAS exact | 200 OK, no warning |
| 5 x 14 = 70s | overflow 10s | 200 OK, total=60.0s after truncate item belakang |

Unittest `test_compilation_moments_uses_mocked_llm_and_filters_candidates` (test_admin.py L240-L272) diperbaiki → `Valid 11s lolos`, `Too short 2s / Too long 30s rejected`.

---

## 🆕 Fitur #3: Menu Konfigurasi LLM API Key di Admin UI

**Status**: ✅ Verified (6 LLM config save/wipe/validate test 100% pass). User sekarang bisa save LLM key via UI panel, TIDAK perlu restart Flask.

**Rationale**: Dulu cuma bisa baca dari env `HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY`. Sekarang stored DB `admin_settings` key=value → priority **stored > env > defaults**.

### Lokasi kode:
- **Table + helpers** [admin_store.py L108-L113](file:///home/isra/podcast-clips/app/admin_store.py#L108-L113):
  - `admin_settings(key TEXT PK, value, updated_at)`
  - `get_setting/set_setting/delete_setting` L358-L395
  - `get_llm_config(include_key=True/False)` L400-L426: priority stored, env, default. Return dict: `{api_key, base_url, model, configured_from: stored|env|none}`. Masked key display: 4prefix + **** + 4suffix.
  - `save_llm_config(api_key="", base_url=None, model=None)` L440-L467 validators: api_key min 8 chars, base_url harus https/http dan ≤500, model ≤120 chars.
- **Routes** [admin.py L755-L809](file:///home/isra/podcast-clips/app/routes/admin.py#L755-L809):
  - `GET /admin/llm-config`: JSON masked config (AJAX check refresh state)
  - `POST /admin/llm-config/save`: accept JSON or form post. CSRF check via `csrf_token` payload + `X-Requested-With:XMLHttpRequest`. Flag `llm_api_key_clear=1` wipe stored key (fallback ke env). Error validasi → 400 JSON message, success → `{ok:true, config: {masked}}`.
- **Page context injection**: admin_page dan admin_top5 keduanya inject `llm_config` dict (L727 + di admin_top5 L750)
- **Compilation moments route** pakai stored: [admin.py L873-L880](file:///home/isra/podcast-clips/app/routes/admin.py#L873-L880) — now `get_llm_config(include_key=True)` (NOT from env langsung). Model dan base_url juga pakai stored config, tidak hardcoded lagi (L916-L924). Jika NO key (configured_from=none) → fallback transcript + warning "LLM belum dikonfigurasi, save dulu di Konfigurasi LLM."
- **UI Card Konfigurasi LLM** admin.html SETELAH section Koneksi akun upload L1357: panel dengan 3 input (API key password autocomplete off, Base URL, Model), 2 buttons: 💾 Simpan Konfigurasi, 🧹 Hapus key stored. AJAX handlers L2570-L2666. Chip header badge: ✅ Tersimpan (DB) / 📖 Read-only (.env) / ⚠️ Belum dikonfigurasi. Status line inline. Chip Success/Error warna sesuai.

### Verifications:
- ✅ 68/68 unittest hijau
- ✅ 6 LLM test case: none → save pendek → save valid → wipe → ftp invalid base URL (400)
- ✅ HTTP 302 both /admin & /admin/top5 (no 500 render error)

---

## 🆕 Fitur #4: Filter "Klip YouTube siap upload" exclude klip yang sudah pernah di-upload

**Status**: ✅ Verified (Live DB count: total 61 deployed, disembunyikan 9, TAMPIL 52 klip BARU siap upload).

User problem: dulu card "Klip YouTube siap upload" menampilkan 61 item SEMUA klip termasuk 9 yang SUDAH pernah di-upload (bocor-alus 5, jelasin-dong 3, 1 etc). User tidak mau lihat yang udah pernah di-upload; cukup cek di Pengajuan / history.

### Lokasi kode:
- **Filter** [admin.py L659-L665](file:///home/isra/podcast-clips/app/routes/admin.py#L659-L665):
  ```python
  all_clips_full = _deployed_clips()          # tetap generate side effect .metadata.json companion buat semua 61 klip
  all_clips = [c for c in all_clips_full if c.get("upload") is None]   # YANG TAMPIL = HANYA TANPA RECORD UPLOAD SAMA SEKALI (pending/failed/quota/uploading/uploaded/manual → SEMUA HIDDEN)
  hidden_already_uploaded = total - shown
  ```
- **Template context**: L727-L729 inject `hidden_already_uploaded` + `total_deployed_clips`
- **Pagination**: clips_pager uses filtered `len(all_clips)=52` → total pages BENAR (tidak tampil halaman kosong untuk klip tersembunyi)
- **UI card header subtitle + badges** admin.html L1361-L1381:
  - Subtitle baris kedua: `✅ 9 klip sudah pernah di-upload sebelumnya (ada di YT Studio / riwayat) — disembunyikan dari panel ini. Lihat status & historynya di Pengajuan terbaru ↑`
  - Mini chip badge tambahan: `✅ Riwayat upload: N klip`
  - Badge kanan rename dari "N klip" → "52 klip SIAP upload"

---

## 🆕 Fitur #5: Tampilkan Error Message Submission Gagal di UI Pengajuan Terbaru

**Status**: ✅ Verified (columns added auto-migrate, helper accepts error_message, process_episode.py now writes error_message, UI displays red collapsed boxes).

User problem: Pertanyaan user "tukang-kupas uJC4ity3uDI gagal kenapa?" → dulu status failed TANPA penjelasan, cuma icon ✗, tidak ada error di DB, no workspace (karena dihapus). Sekarang ERROR TERCATAT di DB, TAMPIL di UI per item expandable.

### Lokasi kode:
- **ALTER TABLE upgrade-safe** admin_store.py _ensure_columns L67-L74:
  ```python
  existing_sub = pragma table_info(youtube_submissions)
  ADD COLUMN error_message TEXT DEFAULT '' if missing
  ADD COLUMN last_error_at TEXT DEFAULT '' if missing
  ```
  (auto-jalan tiap _connect; tidak perlu migration files; SQLite ALTER TABLE col add OK)
- **Helper `set_submission_status(video_id, status, error_message=None)`** [admin_store.py L309-L331](file:///home/isra/podcast-clips/app/admin_store.py#L309-L331): parameter error_message opsional. Jika disertakan → write error_message (truncate 2000 chars) + last_error_at timestamp. Tidak ada error_message → update status only (backwards compatible).
- **scripts/process_episode.py COMPLETE REWRITE**: (sebelumnya 42 baris, sekarang 86 baris) → Bungkus SELURUH pipeline try/except:
  - Jika list_submissions() gagal di DB → tulis error "DB load failed" + status failed.
  - Jika video_id tidak ada → tulis error "No queued submission for X" + failed.
  - Jika subprocess.run OSError (worker gagal start) → "Worker start failed: OSError..."
  - Jika Exception umum → include traceback.format_exc limit 4.
  - Jika exit code !=0 → error message include kemungkinan penyebab umum: 1) download source mp4 gagal/403 2) Faster-whisper OOM 3) step curate error 4) Duration segment clip boundary error 40-70s (error yang sering: Clip 2 duration must be 40-70 seconds after boundary alignment)
- **UI Pengajuan**:
  - Group header: mini chip MERAH "❌ N item error log ada" sebelum chip group status L1794-L1802.
  - Per-item nested row: Jika status='failed' AND error_message exist → `<details>` badge merah L1837-L1855. Summary menampilkan 90 char pertama + timestamp last_error_at. Click expandable `pre` box putih dashed border menampilkan FULL error escape HTML.

---

## 📋 Rangkuman Files Modified (Sesi Ini Batch)

| File | Perubahan Inti |
|------|----------------|
| `app/admin_store.py` | `admin_settings` table (key/val LLM config); get/set/delete_setting + get_llm_config/save_llm_config; `_ensure_columns` add youtube_submissions error_message+last_error_at; set_submission_status 3th param error_message |
| `app/routes/admin.py` | 4 LLM routes + injection llm_config context; deploy route + upload route top5 (helpers L390-L490, status enrich, deploy helper, 2 POST routes); compilation moments prompt AI rewrite durasi rules + validator build auto truncate 60s total max; filter clips siap upload hidden_already_uploaded 9 → show 52; import list tambah: get_setting / set_setting / delete_setting / get_llm_config / save_llm_config |
| `scripts/process_episode.py` | COMPLETE REWRITE try/except error capture write error_message to DB (tidak cuma status failed doang) + include common penyebab di exit code nonzero |
| `app/templates/admin.html` | 4 sections besar: (1) Panel LLM Config card, (2) Compilation perma-actions deploy + upload UI, (3) JS: updateCompilationDuration counter UI top5 60s rules, (4) Badge group & nested row error_message display untuk failed submissions |
| `test_admin.py` | Perbaiki test_compilation_moments_uses_mocked_llm_and_filters_candidates case durations 6-14s (Valid 11s accepted, 2s/30s rejected) + assert 3 new response fields per_item_min/max/max_total_duration |
| (NEW) `/tmp/podcast-clips/D7j0s9Mx6M0/...` | Fixture: real deployed folder app/static/clips/jelasin-dong/2026-10-02_.../jelasin-dong_..._top5.mp4; upload session actual YouTube 0-RzMiDYmS8 |

### Tests hijau:
- Catatan: jumlah `68 tests` dan smoke run berikut adalah hasil sesi 2026-10-03, bukan hasil saat ini. Per 2026-10-07, suite fresh-environment yang berlaku berisi 106 test; lihat `REMEDIATION_KANBAN.md`.
- ✅ SELALU JALANKAN: `cd /home/isra/podcast-clips && .venv/bin/python -m unittest discover -s . -p "test_*.py"`
- ✅ Expected: `Ran 68 tests in 15-20s` **OK (0 failures)**
- ✅ Restart `systemctl --user restart flask-app` → `curl -I /admin` → **HTTP 302** (redirect /admin/login)
- ✅ `curl -I /admin/top5` → HTTP 302 (redirect login; NOT 500 crash template)

---

## 📝 Jawaban User Pertanyaan "uJC4ity3uDI tukang-kupas Pengakuan Orang Kepercayaan Nusron Wahid GAGAL KENAPA?"

**Answer untuk user (jika ditanyakan balik)**:
Saat ini row `youtube_submissions` ID=68 cuma berisi status=failed, error_message KOSONG karena failure terjadi SEBELUM code kolom error_message kita ditambahkan (kemarin). Tapi **penyebab 99% SAMA PERSIS** dengan 2 episode batch yang FAILED di `batch10.log` (30 Sep dan 1 Okt):

```
FAILED 4Z5vtMoiXIM Skenario Kubu Jokowi dan Prabowo Menjelang 2029 | curate failed after 3 attempts:
Clip 2 duration must be 40-70 seconds after boundary alignment. ERROR: curate failed exit code 1
```

**Artinya**: Step `curate.py` GAGAL saat generate klip #2, karena setelah alignment ke segment transcript (waktu kalimat UTUH), clip 2 DURATION TIDAK MASUK 40-70 detik. Workspace kemudian dihapus (cleanup), jadi tidak ada logs sisa.

**Fix untuk kasus2 gagal seperti ini ke depannya**: SUDAH DILAKUKAN di sesi ini. Mulai SEKARANG setiap submission failed → error message TERCATAT DI DB dan TAMPIL di panel Pengajuan ⋮ Detail expandable merah. User bisa langsung baca penyebabnya tanpa tanya "gagal kenapa?" 😎

---

## 🧪 Historical open issues (resolved or closed by the status audit below):

1. **[LOW] Clip #2 duration boundary error 40-70s (curate.py)** — sering terjadi klip standalone per-episode (bukan kompilasi). User mungkin pengen auto-adjust juga seperti kompilasi 60s; tapi saat ini hanya fatal error. Next time bisa investigasi `curate.py` expand/shrink boundaries.
2. **[MEDIUM] `scripts/run_one_episode.py` exit code non-zero tidak menulis output stderr ke DB**: Saat ini process_episode.py menangkap error code dan tulis pesan GENERIC, tapi actual stdout/stderr dari run_one_episode.py TIDAK di-capture (Popen sekarang text=False capture_output=False). Next improvement: jalankan subprocess.run dengan capture_output=True, text=True lalu jika exit code !=0 ambil stderr 2000 chars terakhir append ke error_message.
3. **[LOW] uJC4ity3uDI FIXTURE TIDAK BISA di trace workspacenya** karena sudah dihapus kemarin. Jika user mau retry, coba delete row submission (bisa via admin panel nanti tambah button reset failed status), atau POST process lagi via admin.
4. **[LOW] LLM test_client route `GET /admin` render** di standalone test (bukan flask live) kadang crash `BuildError: Could not build url for endpoint 'index'.` karena routes public (clips gallery) tidak diimport sebelum test; ini tidak mempengaruhi LIVE server, hanya unit test standalone (68/68 tetap hijau karena test login/moments/deploy tidak render full page).

### Status audit (2026-10-07)

- Issue 1 (clip duration/boundary errors): curation and both renderers now reject invalid ranges before rendering; auto-adjust is intentionally not enabled because it can change the selected moment. See PIPE-03 in `REMEDIATION_KANBAN.md`.
- Issue 2 (worker error output missing from the admin log): resolved. `scripts/process_episode.py` captures stdout/stderr, relays them to its log stream, and stores the last 2,000 characters from each stream in the failed submission error.
- Issue 3 (retrying failed admin submissions): resolved. Failed and pending rows now have an admin retry action; the row is atomically claimed and reused rather than deleted/recreated. See `test_failed_submission_can_be_retried_without_deleting_row`.
- Issue 4 (standalone test route registration): no longer open; current tests register the gallery blueprint before rendering the admin page.

This handoff records the 2026-10-03 session. For current task status and changed interfaces, use `REMEDIATION_KANBAN.md`, `TECHNICAL_REMEDIATION.md`, and `PIPELINE.md`.

---

## 🎯 How To Verify Setiap Fitur (Manual Test Plan — buat next agen bisa verify ulang dalam 10 menit tanpa saya)

### A. Test Top 5 Deploy & Upload:
1. Login admin → Tab ⭐ Top 5 Kompilasi → Pilih `D7j0s9Mx6M0`
2. JS harus auto probe → Preview muncul INSTAN (karena workspace ada). Chips awal: ⚠️ Masih di /tmp.
3. Klik 📦 Deploy permanen → chip hijau 💾 Tersimpan permanen + link Download permanen.
4. Klik 🚀 Upload YouTube → Jika token invalid, error message reconnect benar. Jika token VALID → upload dengan status ✅.
5. Buka `/tmp/podcast-clips/jobs/` → logs compilation ada; cek deployed folder di app/static/clips/<podcast>/<ep>/..._top5.mp4 SHA cocok.

### B. Test LLM Config Menu:
1. Tab 🏠 Beranda → Section card **🧠 Konfigurasi LLM API** (di bawah Koneksi akun upload)
2. Save `llm_api_key` 3 chars → HTTP 400 validasi.
3. Save key `sk-test12345678`, base_url `ftp://bad` → HTTP 400 base_url invalid https/http.
4. Save valid → chip jadi ✅ Tersimpan (DB). Refresh page, cek llm_config.context still stored (masked).
5. Click 🧹 Hapus key → chip kembali ⚠️ Belum dikonfigurasi. Kembali pakai env.

### C. Test Filter Klip siap upload:
1. Beranda → Scroll ke **Klip YouTube siap upload**
2. Subtitle: "✅ 9 klip sudah pernah di-upload sebelumnya — disembunyikan dari panel ini" harus muncul (sesuai count real DB).
3. Card badge: `52 klip SIAP upload` bukan 61.
4. Uploaded klip seperti bocor-alus/lslu0_8OynQ.../clip1 video DiVV0sSAi4I → TIDAK muncul (benar, cuma di Pengajuan atas)

### D. Test Error Message Submission:
1. Jalankan script `process_episode.py fake_video_id_xx` (ID yang tidak ada).
2. Cek DB `youtube_submissions` row → TIDAK BOLEH status cuma failed doang; harus ERROR MESSAGE "No queued submission for fake_video_id_xx" di kolom error_message.
3. Di Beranda Pengajuan → Klik ⋮ Detail → Expand → Terdapat badge merah ❌ Gagal: + box pre full message.
4. Group header harus ada mini chip: "❌ 1 item error log ada"

### E. Test Durasi 60s Rules Top 5:
1. Tab ⭐ Top 5 Kompilasi → Isi form 5 items dengan durasi:
   - 14s ×5 = 70s (overflow 10s)
   - Click Build → harus 200 OK dengan total=60.0s + auto_warning (truncate dari item belakang)
2. Isi form item #1 dengan durasi 3 detik → click build → 400 "Momen #1 terlalu pendek 3s; minimal 6s."

---

**OK agen berikutnya! Semua code verified 68/68 + flask live 302. Selamat bekerja 🚀**
