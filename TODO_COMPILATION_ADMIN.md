# Historical implementation brief: TOP 5 compilation admin UI

> **Historical design checklist (2026-09-30; not a list of unimplemented work).** Current status and the only open operational checks are recorded below. Treat the numbered proposals and old acceptance criteria below as design history where they conflict with current behavior.

**Status audit (2026-10-07):** The selection/build/preview/deploy/upload flow, digest-bound release approval, and SQLite quota queue are implemented. The worker and 30-minute systemd timer templates are present. Code and mocked tests cover the behavior. Remaining: install and exercise the timer; inspect final metadata on a real upload. No video was published during verification.

## Konteks

Pipeline sudah punya builder kompilasi TOP 5 yang working & user-approved:

- **Script**: `scripts/top5_compilation.py` (committed, `755a423`). Template: persistent header 2-line kuning ("TOP 5 MOMEN / <PODCAST>") + sub-header merah, list 1–5 di kiri dengan item aktif di-highlight kuning 38px, subtitle ASS per segmen, white flash + whoosh SFX tiap transisi, ding di pembuka. Input: edit blok `ITEMS`/`HEADER_L1`/`HEADER_L2`/`SUBHEADER`/`SEGS` di file, lalu `python top5_compilation.py <workdir> <out.mp4>`. Workdir harus punya `transcript.json` + `source.mp4`.
- **Contoh hasil live**: https://youtube.com/watch?v=1l2J04H5Qeo ("TOP 5 Momen Bocor Alus Politik - Prabowo vs Gibran #Shorts")
- **Masalah pada saat brief ditulis (sudah ditangani)**: builder hanya bisa dijalankan manual via shell oleh Hermes; admin UI now provides the compilation workflow described in the status audit above.

## Original backend proposal (implemented; retained as design history)

### 1. Backend routes (app/routes/admin.py)

- `GET /admin/compilation` — halaman form kompilasi:
  - Dropdown episode dari workspace `/tmp/podcast-clips/*` yang punya `transcript.json` + `source.mp4` (validasi keduanya; tampilkan tanggal episode + judul).
  - Render transkrip episode terpilih per segmen (group baris transcript.json jadi paragraf ~10 detik) dengan checkbox per paragraf + field timestamp implisit.
- `POST /admin/compilation/moments` — (opsional, LLM-assisted) kirim transkrip ke SumoPod (`HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY` dari `.env`, JANGAN pass eksplisit — lihat skill) → return ~10 kandidat punchline `{start, end, judul_pendek, alasan}` untuk dicentang user. Model yang dipakai pipeline: baca dari env yang sama seperti curate.py.
- `POST /admin/compilation/build` — body: episode_id, header_l1, header_l2, subheader, items[] `{start, end, item_index}`. Jalankan `scripts/top5_compilation.py` sebagai **background job** (pola sama dengan worker yang sudah ada — lihat `_job_progress`/`_submissions_with_progress`). Script butuh blok ITEMS/SEGS di-inject: paling gampang generate config JSON terpisah dan refactor `top5_compilation.py` agar bisa baca `--config config.json` (jangan edit file script per-request). Output ke `<workdir>/clips/top5_compilation.mp4`.
- `GET /admin/compilation/preview/<episode>` — serve video hasil via route Flask (pattern sama dengan static clips route).
- `POST /admin/compilation/upload` — upload ke YouTube via `scripts/youtube_upload.py` → `upload_clip()` (import langsung). Auto-generate judul `TOP 5 Momen <Podcast> - <Topic> #Shorts` (editable di form) + hashtag set sesuai skill section "v2 template" (campuran nama podcast + topik + generic discoverability, ~15 tags).

### 2. Frontend (app/templates/admin.html atau partial baru)

- Tab/section "Kompilasi TOP 5" di admin page.
- Alur 3 langkah: (1) pilih episode → (2) centang 5 momen dari transkrip (bisa "Usul otomatis" via LLM atau manual), edit judul item; (3) Build → preview player → tombol Upload (judul+hashtag editable).
- Job progress polling pakai `/admin/jobs` pattern yang sudah ada.

### 3. Refactor kecil yang diperlukan

- `scripts/top5_compilation.py`: terima config via argumen (`--config <json>`) selain hardcode. Struktur config: `{"header_l1","header_l2","subheader","items":[{"label"}],"segs":[[start,end,item_index]]}`. JANGAN break mode lama yang hardcode (masih dipakai manual).

## Pitfalls yang wajib dipegang (dari sesi 2026-09-30)

- `youtube_upload.py` auto-append `#Shorts` — jangan sertakan di judul form, nanti dobel `#Shorts #Shorts`.
- **Current behavior:** an ambiguous interrupted upload must be reconciled against the channel and persisted upload records before any new insert. Do not delete a video or blindly retry an ambiguous upload.
- `videos().update()` via googleapiclient **diam-diam buang tags** (200 OK tapi tags None). Pakai raw REST: refresh token manual, `requests.put('https://www.googleapis.com/youtube/v3/videos?part=snippet', json=body)` — tags baru nempel. Satu update harus sekalian title+description+categoryId+tags.
- Font header 52px di lebar 720 crop karakter ujung — pakai ukuran yang sudah di script.
- Timestamp momen: pad start ~0.5–1 detik lebih awal biar setup punchline nggak kepotong.
- Background build jangan foreground + background barengan (pola concurrency upload yang sama).
- CSRF check untuk semua POST (`check_csrf()`), `@admin_required()` di semua route baru.

## Acceptance criteria

1. Dari /admin bisa: pilih episode → centang 5 momen → build → lihat preview → upload ke YouTube, tanpa buka shell.
2. Video hasil identik kualitasnya dengan builder manual (header persistent, highlight aktif, subtitle, SFX).
3. Upload menghasilkan metadata lengkap (judul benar tanpa #Shorts dobel, description + 15 tags terverifikasi via `videos().list` setelah update).
4. Job gagal → status error tampil di UI, bukan silent no-op.

---

# TAMBAHAN (1 Okt 2026): Tombol Upload YouTube per-clip di Admin UI

Fitur kedua requested in the original plan, one package with the compilation form (same upload route and quota-handling concerns).

## Konteks

- Upload YouTube selama ini lewat Hermes CLI (`scripts/youtube_upload.py`) — user sekarang sering upload manual dari UI karena kuota channel kecil.
- Kuota channel: `uploadLimitExceeded` muncul pada ~5 video/24 jam (channel muda; naik seiring waktu). HARUS di-handle di UI, bukan cuma error mentah.
- 10 clip episode Manuver UU Pemilu (2026-10-01) belum masuk YouTube karena limit ini — jadi tombol upload + antrian retry itu langsung kepake.

## Original per-clip upload proposal (implemented; retained as design history)

### 1. Route: `POST /admin/clips/<episode>/<clip_num>/upload`
- Import `upload_clip()` dari `scripts/youtube_upload.py` langsung (jangan subprocess).
- Source of truth untuk title/caption: `captions.json` di folder episode (deploy dir `app/static/clips/<podcast>/<episode>/` ATAU workdir `/tmp/podcast-clips/<id>/`). Judul dari `captions.json[num]['title']`, caption dari `['caption'].strip()` (strip leading newline — caption.py prefix `\n\n`).
- `#Shorts` di-append otomatis oleh upload_clip — JANGAN ditambahkan di UI.
- Setelah upload sukses: update deskripsi + tags via **raw REST** (googleapiclient `videos().update` diam-diam buang tags — lihat pitfall di bawah). Tags default 15 sesuai skill section "Searchable metadata".
- **Historical proposal superseded:** the implementation records per-clip history in `youtube_uploads`; admin submission state is stored separately.

### 2. Handle kuota `uploadLimitExceeded`
- Bila ResumableUploadError 400 `uploadLimitExceeded`: JANGAN retry otomatis bareng. Tampilkan status "Kuota harian habis — retry besok" di UI + opsi "Jadwalkan retry" yang menulis ke tabel antrian sederhana (bisa table baru `upload_queue` di sqlite yang sama: episode, clip_num, status queued/failed/done, retry_at).
- **Current behavior:** `scripts/process_youtube_queue.py` processes due rows; deploy `deploy/youtube-retry.service` and `deploy/youtube-retry.timer` to schedule it.
- **Current behavior:** retries require a persisted video ID or a reconciled non-ambiguous state; never delete a potentially valid upload to make retry proceed.

### 3. Frontend
- Di halaman episode admin, tiap clip card dapat tombol "Upload ke YouTube" + status badge (belum / queued / uploaded <video_id> / failed <reason>).
- Tombol "Upload semua" dengan progress per clip (1 per 5 detik delay untuk hindari rate limit API, bukan kuota).
- Setelah upload sukses, tampilkan link youtube.com/watch?v=<id>.

## Pitfalls tambahan (semua sudah kejadian nyata, Oct 2026)

- `videos().update()` via googleapiclient mengembalikan 200 TAPI tags jadi None. Solusi terverifikasi: refresh token manual → `requests.put('https://www.googleapis.com/youtube/v3/videos?part=snippet', json=body)` dengan title+description+categoryId+tags sekaligus.
- **Current behavior:** reconcile interrupted uploads using persisted IDs and the manifest; ambiguous `uploading` states need operator investigation before retry. Never delete videos based only on matching titles.
- Kuota upload ≠ kuota API. Kuota API (10.000 unit/hari) hampir mustahil habis untuk upload <100 video. Yang habis itu upload limit per-channel (~5/hari untuk channel muda).
- `#Shorts` dobel terjadi 2x — upload_clip sudah auto-append, UI tidak boleh menambah lagi.

## Acceptance criteria tambahan

5. Upload satu clip dari UI → video live dengan judul benar (tanpa #Shorts dobel), description berisi caption + 3 link, tags ≥10 terverifikasi.
6. Saat kena uploadLimitExceeded → status jelas di UI, clip masuk antrian, tidak ada retry spam.
7. Riwayat upload (video_id, timestamp) tercatat di `youtube_uploads`, the current per-clip upload table.
