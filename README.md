# 🌟 Asteri Web Server

**Asteri** adalah web server Python berperforma tinggi, *production-ready*, yang dirancang dengan sistem argumen CLI yang kaya dan intuitif. Asteri mendukung berbagai protokol mulai dari WSGI, ASGI, hingga uWSGI biner.

## ✨ Fitur Utama
- **Multi-Protokol**: HTTP/1.1, HTTP/2 (Full Frame Support), WSGI, ASGI, uWSGI.
- **Berbagai Tipe Worker**:
  - `sync`: Worker sinkron standar.
  - `gthread`: Worker berbasis thread untuk konkurensi lebih baik.
  - `gevent`: Worker asinkron berbasis greenlet (performa tinggi).
  - `asgi`: Dukungan penuh untuk aplikasi modern (FastAPI, Starlette).
- **Auto-Reload Profesional**: Berbasis *event-driven* menggunakan `watchdog`.
- **Manajemen Proses**: Mode Daemon, PID files, dan User/Group switching.
- **Keamanan**: Dukungan SSL/TLS (HTTPS) yang mudah dikonfigurasi.
- **Dashboard Status**: Pantau kesehatan server di endpoint `/asteri-status`.

## 🚀 Instalasi
```bash
git clone https://github.com/IshikawaUta/asteri.git
cd asteri
pip install -e .
```

## 🛠️ Penggunaan Dasar
Menjalankan aplikasi WSGI:
```bash
asteri myapp:app
```

Menjalankan dengan 4 worker dan bind ke port tertentu:
```bash
asteri myapp:app -w 4 -b 0.0.0.0:8080
```

## 📖 Panduan CLI Lengkap

Asteri mendukung berbagai kategori argumen untuk kontrol penuh:

### 🌐 Jaringan (Network)
- `-b, --bind ADDRESS`: Bind ke alamat tertentu (misal: `127.0.0.1:8000`).
- `--backlog INT`: Batas antrean koneksi.
- `--reuse-port`: Menggunakan flag `SO_REUSEPORT` pada soket.

### 👷 Worker
- `-w, --workers INT`: Jumlah proses worker.
- `-k, --worker-class STRING`: Tipe worker (`sync`, `gthread`, `asgi`, `gevent`).
- `-t, --timeout INT`: Batas waktu worker sebelum di-*restart*.
- `--preload`: Memuat aplikasi sebelum *forking* worker.

### 🔒 Keamanan & SSL
- `--certfile FILE`: Jalur ke file sertifikat SSL.
- `--keyfile FILE`: Jalur ke file kunci SSL.
- `-u, --user USER`: Menjalankan worker sebagai user tertentu.
- `-g, --group GROUP`: Menjalankan worker sebagai group tertentu.

### 📝 Logging & Debugging
- `--log-file FILE`: Jalur ke file log error.
- `--log-level LEVEL`: Granularitas log (`debug`, `info`, `warning`, `error`).
- `--access-logfile FILE`: Jalur ke file log akses.
- `--print-config`: Menampilkan konfigurasi yang sedang digunakan dan keluar.

### ⚙️ Proses
- `-D, --daemon`: Menjalankan Asteri di latar belakang (background).
- `-p, --pid FILE`: Membuat file PID.
- `--reload`: Memantau perubahan kode dan memuat ulang worker secara otomatis.
- `--chdir DIR`: Mengubah direktori kerja sebelum menjalankan aplikasi.

## 📄 File Konfigurasi
Anda bisa menggunakan file Python untuk konfigurasi yang lebih kompleks:

```python
# asteri.conf.py
bind = "127.0.0.1:8080"
workers = 4
worker_class = "gthread"
timeout = 60
reload = True
```
Jalankan dengan: `asteri myapp:app -c asteri.conf.py`

## 📊 Dashboard Status
Aktifkan dashboard internal untuk melihat statistik worker:
Kunjungi `http://localhost:8000/asteri-status` pada browser Anda.

## 📜 Lisensi
Proyek ini dilisensikan di bawah **MIT License**.