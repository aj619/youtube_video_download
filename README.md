# 📹 YouTube Video Downloader

A robust, configurable YouTube video downloader managed with [`uv`](https://github.com/astral-sh/uv) and standard Python virtual environments. Features real-time visual status markers, stream details tracking (codecs, resolution, bitrate, FPS), download progress tracking, and checkpoint-based resumption for interrupted downloads.

---

## 📁 Project & Configuration Structure

All configuration and tracking state files are stored inside the `workerConfig/` directory:

```
workerConfig/
├── ingestion.json     # Clean list of target YouTube URLs to ingest and download
├── settings.json      # Codec, resolution, quality targets, playlist targets, and output directory settings
└── work_history.json  # Download tracking state, visual status markers, stream specs, and progress %
```

### 1. `workerConfig/settings.json`
Configures output directory, video/audio formats, quality targets, fallback rules, and automatic playlist targets:

```json
{
  "output_directory": "./downloads",
  "playlists": {
    "private_playlists": [
      "Education",
      "Useful content"
    ],
    "public_playlists": [
      "https://www.youtube.com/playlist?list=PLBCF2DAC6FFB574DE"
    ]
  },
  "default_format": {
    "video_codec": "mp4",
    "video_quality": "1080",
    "audio_codec": "m4a",
    "audio_quality": "192"
  },
  "fallback_format": {
    "video_codec": "best",
    "video_quality": "best",
    "audio_codec": "best",
    "audio_quality": "best"
  },
  "download_audio_only": false
}
```

### 2. `workerConfig/ingestion.json`
Maintains the list of YouTube video URLs to download:

```json
{
  "urls": [
    "https://www.youtube.com/watch?v=0zFfcEr1e9U",
    "https://www.youtube.com/watch?v=YuOSyRj3sXg"
  ]
}
```

### 3. `workerConfig/work_history.json`
Automatically updated during pipeline execution to track metadata and download state per video:
- **Status Markers**: `[✓ COMPLETED]`, `[⏳ XX.X% IN PROG]`, `[❌ FAILED]`, `[⏸ PENDING]`
- **Progress % & Bytes**: Real-time progress, downloaded bytes, total expected size, download speed, and ETA.
- **Stream Specs**: Resolution (e.g. `1920x1080`), FPS, Video Codec (e.g. `av01`, `vp09`), Audio Codec (e.g. `opus`, `m4a`), Audio Bitrate.
- **Output Location**: Absolute file path of saved videos or `.part` checkpoint files.
- **Error Tracking**: Interruption or failure logs.

---

## 🛠️ CLI Usage & Commands

### 1. Download Configured Videos & Playlists
Fetches configured public/private playlists, ingests URLs, checks disk checkpoints, and downloads pending videos:

```bash
uv run python downloader.py
```

### 2. Ingest & Download Direct URL(s)
Pass one or more video URLs directly via command line to add them to `ingestion.json` and process them immediately:

```bash
uv run python downloader.py "https://www.youtube.com/watch?v=0zFfcEr1e9U"
```

### 3. Retry Failed or Interrupted Downloads Only
Resumes only incomplete or failed downloads from where they left off using `.part` checkpoint files:

```bash
uv run python downloader.py --retry-failed
# or
uv run python downloader.py --resume
```

### 4. Skip Playlist Fetching
Uses `workerConfig/ingestion.json` directly without querying YouTube Playlist APIs:

```bash
uv run python downloader.py --skip-playlists
```

### 5. Info-Only Mode (Pre-fetch Metadata)
Fetches video titles, video/audio specs, and target file paths into `work_history.json` without downloading actual media files:

```bash
uv run python downloader.py --info-only
```

### 6. Display Visual Status Table
Displays a colorized terminal table of video statuses, stream specs, download progress, and file paths:

```bash
uv run python status.py
```

Filter by status:
```bash
uv run python status.py --filter completed
uv run python status.py --filter downloading
uv run python status.py --filter failed
uv run python status.py --filter pending
```

Output as JSON:
```bash
uv run python status.py --json
```

### 7. Fetch Public or Private YouTube Playlists
Extracts video URLs from a playlist and syncs them into `ingestion.json` and `work_history.json`:

```bash
# Public or Unlisted playlist URL/ID
uv run python fetch_playlist.py "https://www.youtube.com/playlist?list=PLBCF2DAC6FFB574DE"

# Private account playlist using OAuth 2.0 (by playlist name or ID)
uv run python fetch_playlist.py "My Private Playlist" --private
```

---

## ⚙️ Setup & Credentials

### Prerequisites
- Python 3.8+
- Node.js (installed on system PATH for `yt-dlp` JavaScript challenge execution)
- [`uv`](https://github.com/astral-sh/uv) package manager

### Environment Initialization
```bash
# Create virtual environment and install dependencies
uv venv
uv sync
```

### Environment Variables (`.env`)
Create a `.env` file in the project root for YouTube Data API key and OAuth 2.0 credentials (required only for playlist fetching):

```env
GOOGLE_API_KEY=your_api_key_here

# For Private Playlist Access (OAuth 2.0)
GOOGLE_CLIENT_ID=your_client_id_here
GOOGLE_CLIENT_SECRET=your_client_secret_here
```
Or place a standard `client_secret.json` in the root directory for OAuth authentication.
