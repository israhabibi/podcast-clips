# Archived pipeline status snapshot — 2026-10-01

This is a historical operations snapshot, not the current task board. Episode states and YouTube upload counts below were not revalidated during the 2026-10-07 remediation; use `REMEDIATION_KANBAN.md` for current code and infrastructure work.

## A. Proses batch10 (auto-episode pipeline) — batch10_state.json

| Episode | Judul | Status | Catatan |
|---|---|---|---|
| 4Z5vtMoiXIM | Skenario Kubu Jokowi dan Prabowo Menjelang 2029 | ❌ FAILED | curate gagal 3x: "Clip 2 duration must be 40-70 seconds after boundary alignment" |
| uKgDPWY7eog | Cerita Proses Syuting dan Bocoran Film Laut Bercerita | ⏳ STUCK/RUNNING | download berulang gagal: 2 workdir (source.mp4 386MB utuh + source.f398.mp4.part 213MB), batch restart 6x sejak 2026-09-30 22:14, terakhir 2026-10-01 09:26, tidak ada proses aktif sekarang |
| -b8jMywQBl4 s/d unDFg7ZlX2c (8 episode) | Rokok Cukai Ilegal, RUU Perampasan Aset, PBNU, Jokowi wawancara, Raffi Ahmad, Oposan Prabowo, RUU Masyarakat Adat | 📋 QUEUED | belum mulai |

## B. Episode pipeline per episode (jobs/*.log)

| Episode | Hasil | Catatan |
|---|---|---|
| lslu0_8OynQ (Papua/food estate) | ✅ SUCCESS | 8 clip, pipeline complete (2026-09-28) |
| Go_ZovP5Hd0 (Haji Isam) | ⚠️ PARTIAL | download sempat 403 Forbidden, tapi 8 clip jadi di workdir kedua |
| rLfVk3ZN7S4 (Prabowo vs Gibran) | ⚠️ PARTIAL | curate gagal 1x ("Clip 2 hook not verbatim") lalu berhasil manual — 12 clip + top5_compilation jadi |
| compilation-rLfVk3ZN7S4 | ✅ SUCCESS | top5_compilation.mp4 3:39 (2026-10-01 14:58) |

## C. Upload YouTube — channel: 58 video total

### Terdata di DB youtube_submissions (8):
| VideoID | Judul | Tanggal |
|---|---|---|
| lslu0_8OynQ | episode full Papua | 09-28 |
| Go_ZovP5Hd0 | episode full Haji Isam | 09-28 |
| 1MsJEi1ynhA | TOP 5 Jokowi, Prabowo & Gibran | 09-30 |
| 1l2J04H5Qeo | TOP 5 Prabowo vs Gibran | 09-30 |
| rLfVk3ZN7S4 | episode full Prabowo-Gibran | 09-30 |
| KKmvCx94nOE | TOP 5 Manuver Politik (kompilasi) | 10-01 |
| sM08gAftle4 | clip01 Prabowo DPRD ⚠️ DOBEL — **DIHAPUS 2026-10-02** | 10-01 |
| vmVWrTVUkdA | clip02 17 Juta Suara ⚠️ DOBEL — **DIHAPUS 2026-10-02** | 10-01 |
| 5URqwigUwFU | clip03 PDIP Curi Start ⚠️ DOBEL — **DIHAPUS 2026-10-02** | 10-01 |

### Upload ulang 2026-10-02 (via yt_upload_safe.py, anti-dobel):
| VideoID | Judul | Sumber |
|---|---|---|
| 5YExe6LpPEY | Fakta Mengejutkan: 17 Juta Suara Terbuang | jh-kTEMWWvY clip02 |
| 8J52Zn4FWjw | PDIP Curi Start Revisi UU Pemilu | jh-kTEMWWvY clip03 |
| VLHmEha7Q8E | Ternyata 5% untuk Menambah Kursi | jh-kTEMWWvY clip04 |
| Vnc1SkcLZYU | Nasdem Pro 7%, PAN Maunya 0% | jh-kTEMWWvY clip05 |
| JOkjvV8qSC0 | Skema 5% Hanya Loloskan 5 Partai | jh-kTEMWWvY clip06 |
| 2h0WDOoJuHs | Revisi UU Belum Dapat Restu Ketua Kelas | jh-kTEMWWvY clip07 |

Semua 6 tercatat di DB youtube_submissions (status: completed, 2026-10-02 03:25).

Kuota habis setelah 6 upload — clip08-10 jh-kTEMWWvY + D7j0s9Mx6M0 + Go_ZovP5Hd0 clip03-08 + rLfVk3ZN7S4 12 clip menunggu reset kuota (~24 jam). Script yt_upload_safe.py sudah skip otomatis clip yang judulnya sudah ada di channel (terbukti: clip01 jh-kTEMWWvY & clip01-02 Go_ZovP5Hd0 ke-skip).

### Tidak tercatat di DB (50 video) — teridentifikasi via YouTube API:
- 2026-09-26: 7 clip ambang batas parlemus (ohbzPovAEPk, 9D5DMX1IDe8, 6MDWcsxpaMc, IvvMpd900jI, -S9jKyYK9Js, jLZaOWrIdeQ, pOw82EsJ6zs) — termasuk 1 duplikat (Nasdem 7% dua kali: 9D5DMX1IDe8 & ohbzPovAEPk)
- 2026-09-27: 24 clip (Purbaya 8x, saham KCIC 4x, Prabowo-Gibran 6x, Jaksa/KPK 3x, dll) — termasuk duplikat: 6yke0qht6SU muncul 2x, "Purnawirawan Bergerak" 2x (o2aAIzESBt4 + 4uK_fgE2lBs), "Gibran VP" 2x (ekFV3V1P0ZQ + SkOb1zFjP2s), "33 Tahun vs Singkat" 2x (Vh9Bpmdop6I + SNfBNMD87W4), dst.
- 2026-09-28: 1 (LEPET akuarium — bukan podcast)
- 2026-09-29: 8 clip Papua
- 2026-09-30: 6 clip Prabowo-Gibran + TOP 5 x2
- 2026-10-01: KKmvCx94nOE (TOP5 manuver), sM08gAftle4, vmVWrTVUkdA, 1RR1mKshkNs (⚠️ clip01 DOBEL ketiga!), 5URqwigUwFU (⚠️ clip03 PDIP dobel)

### Duplikat terkonfirmasi di channel (hasil upload berkali-kali episode sama):
- clip01 "Prabowo: Lebih Gampang Atur 25 Anggota DPRD" → 3 versi (1RR1mKshkNs, sM08gAftle4, + versi lama)
- clip02 "17 Juta Suara Terbuang" → 2 versi
- clip03 "PDIP Curi Start" → 2 versi (5URqwigUwFU + lama)
- "Purnawirawan Bergerak", "Gibran VP", "33 Tahun", "Saham Kereta Cepat", "Nasdem 7%", "Dual Machine", "Melengser", "Purnawirawan Jendral", "Buka Hutan" → masing-masing 2 versi

## D. Stok clip LOKAL yang BELUM di YouTube (hasil cross-check API):
| Workdir | Episode | Belum upload |
|---|---|---|
| jh-kTEMWWvY / manuver-uu-pemilu-upload | Manuver UU Pemilu | clip04–clip10 (7 clip) |
| D7j0s9Mx6M0 | Purbaya/KCIC | 10 clip (semua) |
| Go_ZovP5Hd0-1790660368 | Haji Isam | clip03–clip08 (6 clip) |
| rLfVk3ZN7S4-1790751609 | Prabowo vs Gibran | 12 clip (semua) |
| **TOTAL** | | **~35 clip** |

## E. Masalah sistemik
1. **DB tidak dipercaya** — 50 dari 58 video tidak tercatat. Upload via script langsung tidak selalu insert ke youtube_submissions.
2. **Tidak ada dedup saat upload** — episode sama di-upload berkali-kali (workdir berbeda untuk episode sama: manuver-uu-pemilu-upload vs jh-kTEMWWvY).
3. **Batch10 macet** — uKgDPWY7eog stuck di download, 8 episode lain nganggur sejak 09-30.
4. **Kuota upload** — limit harian YouTube (~10), reset ~24 jam dari upload terakhir.

## F. Rekomendasi langkah berikutnya
1. Pilih satu workdir kanonik per episode (hapus/arsipkan duplikatnya).
2. Upload 35 clip tersisa via antrian yang CEK JUDUL ke YouTube API dulu sebelum upload (anti-dobel).
3. Perbaiki script upload agar WAJIB insert ke youtube_submissions.
4. Perbaiki/hentikan batch10 (uKgDPWY7eog) biar tidak makan bandwidth.
