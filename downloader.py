import os
import json
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

import yt_dlp

from state_manager import StateManager, INGESTION_FILE, WORK_HISTORY_FILE, load_json
from fetch_playlist import fetch_public_playlist, fetch_private_playlists

load_dotenv()

WORK_CONFIG_DIR = Path("workerConfig")
SETTINGS_FILE = WORK_CONFIG_DIR / "settings.json"

# ---------------------------------------------------------
# Downloader & Resumable Logic
# ---------------------------------------------------------
def build_format_string(settings: dict) -> str:
    default_cfg = settings.get("default_format", {})
    fallback_cfg = settings.get("fallback_format", {})

    v_codec = default_cfg.get("video_codec", "mp4")
    v_qual = default_cfg.get("video_quality", "1080")
    a_codec = default_cfg.get("audio_codec", "m4a")

    if v_qual.isdigit():
        primary_fmt = f"bestvideo[height<={v_qual}]+bestaudio/bestvideo[height<={v_qual}][ext={v_codec}]+bestaudio[ext={a_codec}]"
    elif v_qual == "best":
        primary_fmt = "bestvideo+bestaudio"
    else:
        primary_fmt = "bestvideo+bestaudio"

    fallback_v_qual = fallback_cfg.get("video_quality", "best")
    if fallback_v_qual.isdigit():
        fallback_fmt = f"bestvideo[height<={fallback_v_qual}]+bestaudio"
    else:
        fallback_fmt = "bestvideo+bestaudio/best"

    return f"{primary_fmt}/{fallback_fmt}/best"

def create_progress_hook(sm: StateManager, current_url: str):
    def progress_hook(d):
        status_str = d.get('status')
        filename = d.get('filename', '')
        if status_str == 'downloading':
            dl = d.get('downloaded_bytes', 0)
            tot = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            
            speed_val = d.get('speed')
            speed = d.get('_speed_str', '') or (f"{speed_val / 1024 / 1024:.2f} MB/s" if speed_val else '0 B/s')
            
            eta_val = d.get('eta')
            eta = d.get('_eta_str', '') or (f"{eta_val}s" if eta_val is not None else '--:--')

            sm.update_progress(
                url=current_url,
                status="DOWNLOADING",
                downloaded_bytes=dl,
                total_bytes=tot,
                speed_str=speed,
                eta_str=eta,
                filepath=filename
            )
        elif status_str == 'finished':
            sm.update_progress(
                url=current_url,
                status="COMPLETED",
                downloaded_bytes=d.get('total_bytes', 0),
                filepath=filename
            )
        elif status_str == 'error':
            sm.update_progress(
                url=current_url,
                status="FAILED",
                error="Download error reported by yt-dlp",
                filepath=filename
            )
    return progress_hook

def run_pipeline(input_urls: list = None, retry_failed: bool = False, skip_playlists: bool = False, info_only: bool = False, show_status: bool = False):
    settings = load_json(SETTINGS_FILE, default_val={})
    sm = StateManager(WORK_HISTORY_FILE)

    output_dir = Path(settings.get("output_directory", "./downloads"))
    output_dir.mkdir(parents=True, exist_ok=True)

    fetched_urls = []
    if input_urls:
        fetched_urls.extend(input_urls)

    if not skip_playlists:
        playlist_cfg = settings.get("playlists", {})
        private_targets = playlist_cfg.get("private_playlists", [])
        public_targets = playlist_cfg.get("public_playlists", [])

        if private_targets:
            private_urls = fetch_private_playlists(private_targets)
            fetched_urls.extend(private_urls)

        if public_targets:
            for target in public_targets:
                print(f"\nFetching videos from public playlist: '{target}'...")
                public_urls = fetch_public_playlist(target)
                fetched_urls.extend(public_urls)

    if fetched_urls:
        existing_data = load_json(INGESTION_FILE, default_val={"urls": []})
        existing_urls = existing_data.get("urls", []) if isinstance(existing_data, dict) else []
        combined_urls = list(dict.fromkeys(existing_urls + fetched_urls))
        with open(INGESTION_FILE, "w", encoding="utf-8") as f:
            json.dump({"urls": combined_urls}, f, indent=2)
        print(f"\nUpdated '{INGESTION_FILE}' with {len(combined_urls)} total video URLs.")

    # Sync ingestion.json into work_history.json
    sm.sync_with_ingestion(INGESTION_FILE, output_dir)

    if show_status:
        sm.print_status_table()
        return

    all_urls = [v["url"] for v in sm.videos_dict.values()]
    if not all_urls:
        print(f"\nNo URLs configured in settings or found in '{INGESTION_FILE}'.")
        return

    # Filter URLs to process
    urls_to_process = []
    for url in all_urls:
        vid = sm.get_video_id(url)
        entry = sm.videos_dict.get(vid, {})
        st = entry.get("status", "PENDING")

        if retry_failed:
            if st in ("FAILED", "DOWNLOADING"):
                urls_to_process.append(url)
        else:
            if st != "COMPLETED":
                urls_to_process.append(url)

    if not urls_to_process:
        print("\n✨ All videos are already downloaded and verified! Nothing to process.")
        sm.print_status_table()
        return

    # Build yt-dlp format rules
    audio_only = settings.get("download_audio_only", False)
    if audio_only:
        audio_codec = settings.get("default_format", {}).get("audio_codec", "mp3")
        format_spec = "bestaudio/best"
        postprocessors = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': audio_codec,
            'preferredquality': settings.get("default_format", {}).get("audio_quality", "192"),
        }]
    else:
        format_spec = build_format_string(settings)
        postprocessors = []

    outtmpl_pattern = str(output_dir / '%(title)s [%(id)s].%(ext)s')

    print(f"\n🚀 Processing {len(urls_to_process)} video(s)...")
    print(f"Format rule: '{format_spec}'")
    print(f"Output directory: '{output_dir.resolve()}'")
    print(f"Resumable download mode: ACTIVE (part files preserved)\n")

    for i, url in enumerate(urls_to_process, 1):
        vid = sm.get_video_id(url)
        entry = sm.videos_dict.get(vid, {})
        prev_pct = entry.get("progress_percent", 0.0)

        print(f"\n--- [{i}/{len(urls_to_process)}] Processing: {url} ---")
        if prev_pct > 0 and entry.get("status") in ("DOWNLOADING", "FAILED"):
            print(f"🔄 Resuming download from previous checkpoint ({prev_pct:.1f}%)...")

        ydl_opts = {
            'format': format_spec,
            'outtmpl': outtmpl_pattern,
            'merge_output_format': settings.get("default_format", {}).get("video_codec", "mp4"),
            'postprocessors': postprocessors,
            'ignoreerrors': True,
            'continuedl': True,  # Resume partial downloads
            'nopart': False,      # Keep .part files for resumption
            'js_runtimes': {'node': {}},
            'progress_hooks': [create_progress_hook(sm, url)],
        }

        # Step 1: Pre-extract metadata & target filename in output_dir
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'format': format_spec, 'outtmpl': outtmpl_pattern, 'js_runtimes': {'node': {}}}) as ydl_info:
                info = ydl_info.extract_info(url, download=False)
                target_filepath = ydl_info.prepare_filename(info)
                sm.update_metadata(url, info, target_filepath)
                print(f"Title: {info.get('title')}")
                print(f"Specs: {info.get('resolution', 'N/A')} | Video: {info.get('vcodec', 'N/A')} | Audio: {info.get('acodec', 'N/A')}")
                print(f"Target: {target_filepath}")
        except Exception as e:
            print(f"Warning: Failed to fetch metadata for {url}: {e}")
            target_filepath = str(output_dir / f"video_{vid}.mp4")

        if info_only:
            print("Info-only mode: Skipped download.")
            continue

        # Step 2: Download with real-time hook & auto-resumption
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Check if final output file exists
            output_file = Path(target_filepath)
            if not output_file.exists():
                matches = [f for f in output_dir.glob(f"*{vid}*.*") if not f.name.endswith(('.part', '.ytdl', '.temp', '.tmp', '.unpart'))]
                if matches:
                    output_file = matches[0]

            if output_file.exists():
                sm.update_progress(
                    url=url,
                    status="COMPLETED",
                    downloaded_bytes=output_file.stat().st_size,
                    filepath=str(output_file.resolve())
                )
                print(f"✅ Finished: {output_file.name}")
            else:
                # Check for .part file
                part_files = list(dict.fromkeys(
                    list(output_dir.glob(f"*{vid}*.*.part")) + list(output_dir.glob(f"*{vid}*.part"))
                ))
                if part_files:
                    part_size = part_files[0].stat().st_size
                    tot_size = sm.videos_dict.get(vid, {}).get("total_bytes", 0)
                    pct = round((part_size / tot_size * 100), 1) if tot_size > 0 else prev_pct
                    sm.update_progress(
                        url=url,
                        status="FAILED",
                        downloaded_bytes=part_size,
                        filepath=str(part_files[0].resolve()),
                        error="Download interrupted or partial"
                    )
                    print(f"⚠️ Interrupted at {pct:.1f}%. Partial file saved: {part_files[0].name}")
                else:
                    sm.update_progress(url=url, status="FAILED", error="Download did not produce expected output file")

        except KeyboardInterrupt:
            print("\n⚠️ Download process interrupted by user (Ctrl+C). Saving checkpoint state...")
            part_files = list(dict.fromkeys(
                list(output_dir.glob(f"*{vid}*.*.part")) + list(output_dir.glob(f"*{vid}*.part"))
            ))
            if part_files:
                part_size = part_files[0].stat().st_size
                sm.update_progress(
                    url=url,
                    status="FAILED",
                    downloaded_bytes=part_size,
                    filepath=str(part_files[0].resolve()),
                    error="Interrupted by user (Ctrl+C)"
                )
            else:
                sm.update_progress(url=url, status="FAILED", error="Interrupted by user (Ctrl+C)")
            sys.exit(0)
        except Exception as e:
            print(f"❌ Error downloading {url}: {e}")
            sm.update_progress(url=url, status="FAILED", error=str(e))

    print("\n---------------------------------------------------------")
    print("📊 Pipeline Run Completed. Current Video Marker Status:")
    sm.print_status_table()

def main():
    parser = argparse.ArgumentParser(description="YouTube Video Downloader with Markers, Specs & Resumption Support")
    parser.add_argument("urls", nargs="*", help="Optional YouTube URL(s) to ingest and download immediately")
    parser.add_argument("--status", action="store_true", help="Display current video download markers & status table")
    parser.add_argument("--retry-failed", "--resume", action="store_true", help="Only retry failed or interrupted downloads")
    parser.add_argument("--skip-playlists", action="store_true", help="Skip fetching playlist API and use ingestion.json directly")
    parser.add_argument("--info-only", action="store_true", help="Fetch metadata and update markers without downloading video files")

    args = parser.parse_args()

    run_pipeline(
        input_urls=args.urls,
        retry_failed=args.retry_failed,
        skip_playlists=args.skip_playlists,
        info_only=args.info_only,
        show_status=args.status
    )

if __name__ == "__main__":
    main()
