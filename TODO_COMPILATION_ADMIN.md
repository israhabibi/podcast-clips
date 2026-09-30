# TODO: Fitur Kompilasi TOP 5 di Admin UI

> Planning doc — BELUM dieksekusi. Dibuat 2026-09-30. Agen berikutnya yang mengeksekusi harus baca file ini + skill `podcast-clipping` (section "Compilation Shorts") dulu.

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
