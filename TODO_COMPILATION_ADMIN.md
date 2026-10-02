# TODO: Fitur Kompilasi TOP 5 di Admin UI

> Planning doc — fase build/preview Admin sudah mulai diimplementasikan; upload dan suggestions LLM masih terbuka. Dibuat 2026-09-30. Agen berikutnya yang melanjutkan harus baca file ini + skill `podcast-clipping` (section "Compilation Shorts") dulu.

**Progress (2026-10-01):** `top5_compilation.py` sekarang menerima `--config` tanpa memutus mode manual lama. Admin sudah memiliki discovery workspace, endpoint transcript candidates, background build, status polling, dan preview route. Belum ada upload YouTube, quota queue, atau suggestions otomatis LLM.

## Konteks

Pipeline sudah punya builder kompilasi TOP 5 yang working & user-approved:

- **Script**: `scripts/top5_compilation.py` (committed, `755a423`). Template: persistent header 2-line kuning ("TOP 5 MOMEN / <PODCAST>") + sub-header merah, list 1–5 di kiri dengan item aktif di-highlight kuning 38px, subtitle ASS per segmen, white flash + whoosh SFX tiap transisi, ding di pembuka. Input: edit blok `ITEMS`/`HEADER_L1`/`HEADER_L2`/`SUBHEADER`/`SEGS` di file, lalu `python top5_compilation.py <workdir> <out.mp4>`. Workdir harus punya `transcript.json` + `source.mp4`.
- **Contoh hasil live**: https://youtube.com/watch?v=1l2J04H5Qeo ("TOP 5 Momen Bocor Alus Politik - Prabowo vs Gibran #Shorts")
- **Masalah**: builder hanya bisa dijalankan manual via shell oleh Hermes. User mau bisa pakai dari admin UI (clips.gcp.my.id/admin).

## Yang harus dibangun

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
- Upload yang terputus bisa meninggalkan video duplikat — sebelum upload ulang, cek `search().list(forMine=True, order='date')` dan hapus dup.
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

Fitur kedua yang harus dibangun agen berikutnya, satu paket dengan form kompilasi di atas (sama-sama butuh route upload + handle kuota).

## Konteks

- Upload YouTube selama ini lewat Hermes CLI (`scripts/youtube_upload.py`) — user sekarang sering upload manual dari UI karena kuota channel kecil.
- Kuota channel: `uploadLimitExceeded` muncul pada ~5 video/24 jam (channel muda; naik seiring waktu). HARUS di-handle di UI, bukan cuma error mentah.
- 10 clip episode Manuver UU Pemilu (2026-10-01) belum masuk YouTube karena limit ini — jadi tombol upload + antrian retry itu langsung kepake.

## Yang harus dibangun

### 1. Route: `POST /admin/clips/<episode>/<clip_num>/upload`
- Import `upload_clip()` dari `scripts/youtube_upload.py` langsung (jangan subprocess).
- Source of truth untuk title/caption: `captions.json` di folder episode (deploy dir `app/static/clips/<podcast>/<episode>/` ATAU workdir `/tmp/podcast-clips/<id>/`). Judul dari `captions.json[num]['title']`, caption dari `['caption'].strip()` (strip leading newline — caption.py prefix `\n\n`).
- `#Shorts` di-append otomatis oleh upload_clip — JANGAN ditambahkan di UI.
- Setelah upload sukses: update deskripsi + tags via **raw REST** (googleapiclient `videos().update` diam-diam buang tags — lihat pitfall di bawah). Tags default 15 sesuai skill section "Searchable metadata".
- Catat ke `youtube_submissions` (tabel sudah ada di `app/data/admin.sqlite3`).

### 2. Handle kuota `uploadLimitExceeded`
- Bila ResumableUploadError 400 `uploadLimitExceeded`: JANGAN retry otomatis bareng. Tampilkan status "Kuota harian habis — retry besok" di UI + opsi "Jadwalkan retry" yang menulis ke tabel antrian sederhana (bisa table baru `upload_queue` di sqlite yang sama: episode, clip_num, status queued/failed/done, retry_at).
- Sebuah langkah kecil (bisa cron job user-side atau tombol "Proses antrian" di UI) memproses antrian saat kuota reset.
- Sebelum retry, cek dulu apakah clip sudah terlanjur masuk (upload terputus bisa meninggalkan video live): `search().list(forMine=True, order='date')` dan hapus duplikat sebelum upload ulang.

### 3. Frontend
- Di halaman episode admin, tiap clip card dapat tombol "Upload ke YouTube" + status badge (belum / queued / uploaded <video_id> / failed <reason>).
- Tombol "Upload semua" dengan progress per clip (1 per 5 detik delay untuk hindari rate limit API, bukan kuota).
- Setelah upload sukses, tampilkan link youtube.com/watch?v=<id>.

## Pitfalls tambahan (semua sudah kejadian nyata, Oct 2026)

- `videos().update()` via googleapiclient mengembalikan 200 TAPI tags jadi None. Solusi terverifikasi: refresh token manual → `requests.put('https://www.googleapis.com/youtube/v3/videos?part=snippet', json=body)` dengan title+description+categoryId+tags sekaligus.
- Upload yang terputus (network / restart) bisa meninggalkan video duplikat di channel. Sebelum retry apapun, cek `search().list(forMine=True, order='date', maxResults=10)` dan hapus video dengan judul identik.
- Kuota upload ≠ kuota API. Kuota API (10.000 unit/hari) hampir mustahil habis untuk upload <100 video. Yang habis itu upload limit per-channel (~5/hari untuk channel muda).
- `#Shorts` dobel terjadi 2x — upload_clip sudah auto-append, UI tidak boleh menambah lagi.

## Acceptance criteria tambahan

5. Upload satu clip dari UI → video live dengan judul benar (tanpa #Shorts dobel), description berisi caption + 3 link, tags ≥10 terverifikasi.
6. Saat kena uploadLimitExceeded → status jelas di UI, clip masuk antrian, tidak ada retry spam.
7. Riwayat upload (video_id, timestamp) tercatat di youtube_submissions.
