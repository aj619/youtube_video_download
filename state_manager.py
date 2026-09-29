import os
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

WORK_CONFIG_DIR = Path("workerConfig")
INGESTION_FILE = WORK_CONFIG_DIR / "ingestion.json"
WORK_HISTORY_FILE = WORK_CONFIG_DIR / "work_history.json"

def load_json(file_path: Path, default_val: Any = None) -> Any:
    if not file_path.exists():
        return default_val if default_val is not None else {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_val if default_val is not None else {}

def save_json(data: Any, file_path: Path) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = file_path.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp_file.replace(file_path)

class StateManager:
    def __init__(self, history_file: Path = WORK_HISTORY_FILE):
        self.history_file = history_file
        self.load()

    def get_video_id(self, url: str) -> str:
        if "v=" in url:
            return url.split("v=")[-1].split("&")[0]
        elif "youtu.be/" in url:
            return url.split("youtu.be/")[ -1].split("?")[0]
        elif "shorts/" in url:
            return url.split("shorts/")[ -1].split("?")[0]
        elif "embed/" in url:
            return url.split("embed/")[ -1].split("?")[0]
        return url

    def load(self) -> None:
        """Loads work_history.json and populates internal videos_dict."""
        data = load_json(self.history_file, default_val={})
        self.videos_dict: Dict[str, dict] = {}

        if "videos" in data and isinstance(data["videos"], list):
            for item in data["videos"]:
                if isinstance(item, dict) and "url" in item:
                    vid = self.get_video_id(item["url"])
                    item["video_id"] = vid
                    self.videos_dict[vid] = item

    def _create_default_entry(self, url: str, vid: str) -> dict:
        return {
            "url": url,
            "video_id": vid,
            "title": "Unknown Title",
            "status": "PENDING",  # PENDING, DOWNLOADING, COMPLETED, FAILED
            "progress_percent": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed": "0 B/s",
            "eta": "--:--",
            "output_path": "",
            "video_details": {
                "resolution": "N/A",
                "fps": None,
                "vcodec": "N/A",
                "format": "N/A"
            },
            "audio_details": {
                "acodec": "N/A",
                "abr": "N/A",
                "quality": "N/A"
            },
            "error": None,
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ")
        }

    def sync_with_ingestion(self, ingestion_file: Path, output_dir: Path) -> List[str]:
        """Syncs ingestion.json (list of URLs) into work_history.json and checks disk state."""
        data = load_json(ingestion_file, default_val={"urls": []})
        raw_urls = data.get("urls", []) if isinstance(data, dict) else []

        valid_urls = []
        for item in raw_urls:
            url = item if isinstance(item, str) else item.get("url")
            if not url:
                continue
            valid_urls.append(url)
            vid = self.get_video_id(url)
            if vid not in self.videos_dict:
                self.videos_dict[vid] = self._create_default_entry(url, vid)

            entry = self.videos_dict[vid]
            self._check_disk_status(entry, output_dir)

        self.save()
        return valid_urls

    def _check_disk_status(self, entry: dict, output_dir: Path) -> None:
        vid = entry.get("video_id")
        if not vid or not output_dir.exists():
            return

        # Check recorded output path first
        rec_path = entry.get("output_path")
        if rec_path and Path(rec_path).exists() and not rec_path.endswith(('.part', '.ytdl', '.temp', '.tmp')):
            p = Path(rec_path)
            file_size = p.stat().st_size
            entry["status"] = "COMPLETED"
            entry["progress_percent"] = 100.0
            entry["downloaded_bytes"] = file_size
            if entry.get("total_bytes", 0) < file_size:
                entry["total_bytes"] = file_size
            entry["output_path"] = str(p.resolve())
            entry["error"] = None
            return

        # Glob output directory for completed video
        completed_files = [
            f for f in output_dir.glob(f"*{vid}*.*")
            if not f.name.endswith(('.part', '.ytdl', '.temp', '.tmp', '.unpart'))
        ]

        if completed_files:
            final_file = completed_files[0]
            file_size = final_file.stat().st_size
            entry["status"] = "COMPLETED"
            entry["progress_percent"] = 100.0
            entry["downloaded_bytes"] = file_size
            if entry.get("total_bytes", 0) < file_size:
                entry["total_bytes"] = file_size
            entry["output_path"] = str(final_file.resolve())
            entry["error"] = None
            return

        # Look for .part files to calculate partial download percentage for resumption
        part_files = list(dict.fromkeys(
            list(output_dir.glob(f"*{vid}*.*.part")) + list(output_dir.glob(f"*{vid}*.part"))
        ))
        if part_files:
            part_file = part_files[0]
            part_size = part_file.stat().st_size
            entry["downloaded_bytes"] = part_size
            entry["output_path"] = str(part_file.resolve())

            tot = entry.get("total_bytes", 0)
            if part_size > tot:
                entry["total_bytes"] = part_size
                tot = part_size

            if tot > 0:
                entry["progress_percent"] = round(min((part_size / tot) * 100.0, 99.9), 1)

            if entry.get("status") not in ("DOWNLOADING", "FAILED"):
                entry["status"] = "DOWNLOADING"
            return

        # Reset status if previously marked COMPLETED but file no longer exists on disk
        if entry.get("status") in ("COMPLETED", "DOWNLOADING"):
            entry["status"] = "PENDING"
            entry["progress_percent"] = 0.0
            entry["downloaded_bytes"] = 0
            entry["output_path"] = ""

    def update_metadata(self, url: str, info: dict, target_filepath: str) -> None:
        vid = self.get_video_id(url)
        entry = self.videos_dict.get(vid, self._create_default_entry(url, vid))

        entry["title"] = info.get("title", entry.get("title", "Unknown Title"))
        entry["output_path"] = str(Path(target_filepath).resolve())

        req_formats = info.get("requested_formats")
        if req_formats and len(req_formats) >= 2:
            v_fmt = req_formats[0]
            a_fmt = req_formats[1]

            res = v_fmt.get("resolution") or (f"{v_fmt.get('width')}x{v_fmt.get('height')}" if v_fmt.get("width") else "N/A")
            vcodec = v_fmt.get("vcodec", "N/A")
            fps = v_fmt.get("fps")
            format_note = v_fmt.get("format_note") or v_fmt.get("ext", "N/A")

            acodec = a_fmt.get("acodec", "N/A")
            abr_raw = a_fmt.get("abr") or a_fmt.get("tbr")
            abr = f"{abr_raw} kbps" if isinstance(abr_raw, (int, float)) else str(abr_raw or "N/A")
            a_ext = a_fmt.get("ext", "N/A")
        else:
            res = info.get("resolution") or (f"{info.get('width')}x{info.get('height')}" if info.get("width") else "N/A")
            vcodec = info.get("vcodec", "N/A")
            fps = info.get("fps")
            format_note = info.get("format_note") or info.get("format", "N/A")
            acodec = info.get("acodec", "N/A")
            abr_raw = info.get("abr")
            abr = f"{abr_raw} kbps" if isinstance(abr_raw, (int, float)) else "N/A"
            a_ext = info.get("audio_ext", "N/A")

        entry["video_details"] = {
            "resolution": res or "N/A",
            "fps": fps,
            "vcodec": vcodec if vcodec != "none" else "N/A (Audio only)",
            "format": format_note
        }

        entry["audio_details"] = {
            "acodec": acodec if acodec != "none" else "N/A (Video only)",
            "abr": abr,
            "quality": a_ext
        }

        # Estimate filesize or total bytes
        filesize = info.get("filesize") or info.get("filesize_approx")
        if not filesize and req_formats:
            filesize = sum(f.get("filesize") or f.get("filesize_approx") or 0 for f in req_formats)

        if filesize and filesize > 0:
            entry["total_bytes"] = max(entry.get("total_bytes", 0), filesize)

        entry["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.videos_dict[vid] = entry
        self.save()

    def update_progress(self, url: str, status: str, downloaded_bytes: int = 0, total_bytes: int = 0,
                        speed_str: str = "", eta_str: str = "", filepath: str = "", error: Optional[str] = None) -> None:
        vid = self.get_video_id(url)
        entry = self.videos_dict.get(vid, self._create_default_entry(url, vid))

        entry["status"] = status
        if filepath:
            entry["output_path"] = str(Path(filepath).resolve())

        if downloaded_bytes > 0:
            entry["downloaded_bytes"] = downloaded_bytes
        if total_bytes > 0:
            entry["total_bytes"] = max(entry.get("total_bytes", 0), total_bytes)

        if entry["downloaded_bytes"] > entry.get("total_bytes", 0):
            entry["total_bytes"] = entry["downloaded_bytes"]

        tot = entry.get("total_bytes", 0)
        dl = entry.get("downloaded_bytes", 0)

        if status == "COMPLETED":
            entry["progress_percent"] = 100.0
            if dl > 0 and tot == 0:
                entry["total_bytes"] = dl
            entry["error"] = None
        elif tot > 0 and dl >= 0:
            pct = (dl / tot) * 100.0
            entry["progress_percent"] = round(min(max(pct, 0.0), 99.9), 1)

        if speed_str:
            entry["speed"] = speed_str
        if eta_str:
            entry["eta"] = eta_str
        if error is not None:
            entry["error"] = error

        entry["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.videos_dict[vid] = entry
        self.save()

    def save(self) -> None:
        """Saves current video details array into work_history.json."""
        video_list = list(self.videos_dict.values())
        data = {
            "videos": video_list
        }
        save_json(data, self.history_file)

    def print_status_table(self, filter_status: Optional[str] = None) -> None:
        console = Console()
        if not self.videos_dict:
            console.print(f"[yellow]No video tracking records found in '{self.history_file}'.[/yellow]")
            return

        table = Table(title=f"📹 YouTube Video Tracker ({self.history_file.name})", show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Marker / Status", style="bold", width=18)
        table.add_column("Video Title & URL", style="cyan", ratio=3)
        table.add_column("Progress %", justify="right", width=12)
        table.add_column("Downloaded / Total", justify="right", width=18)
        table.add_column("Video & Audio Specs", style="dim", ratio=2)
        table.add_column("Download Location", style="green", ratio=3)

        completed_cnt = 0
        downloading_cnt = 0
        failed_cnt = 0
        pending_cnt = 0
        total_dl_bytes = 0

        for vid, item in self.videos_dict.items():
            st = item.get("status", "PENDING")
            if filter_status and st.upper() != filter_status.upper():
                continue

            pct = item.get("progress_percent", 0.0)
            dl_bytes = item.get("downloaded_bytes", 0)
            tot_bytes = item.get("total_bytes", 0)
            total_dl_bytes += dl_bytes

            if st == "COMPLETED":
                completed_cnt += 1
                marker = "[bold green][✓ COMPLETED][/bold green]"
                pct_str = f"[bold green]{pct:.1f}%[/bold green]"
            elif st == "DOWNLOADING":
                downloading_cnt += 1
                marker = f"[bold yellow][⏳ {pct:.1f}% IN PROG][/bold yellow]"
                pct_str = f"[yellow]{pct:.1f}%[/yellow]"
            elif st == "FAILED":
                failed_cnt += 1
                marker = "[bold red][❌ FAILED][/bold red]"
                pct_str = f"[red]{pct:.1f}%[/red]"
            else:
                pending_cnt += 1
                marker = "[dim blue][⏸ PENDING][/dim blue]"
                pct_str = "[dim]0.0%[/dim]"

            title = item.get("title", "Unknown")
            if len(title) > 40:
                title = title[:37] + "..."
            title_cell = f"[bold]{title}[/bold]\n[dim]{item.get('url', '')}[/dim]"

            dl_str = self._format_size(dl_bytes)
            tot_str = self._format_size(tot_bytes) if tot_bytes > 0 else "Unknown"
            size_cell = f"{dl_str} /\n{tot_str}"

            v_spec = item.get("video_details", {})
            a_spec = item.get("audio_details", {})
            v_res = v_spec.get("resolution", "N/A")
            v_fps = f"{v_spec.get('fps')}fps" if v_spec.get('fps') else ""
            v_codec = v_spec.get("vcodec", "N/A")
            a_codec = a_spec.get("acodec", "N/A")
            a_bitrate = a_spec.get("abr", "N/A")

            specs_cell = f"🎬 {v_res} {v_fps} ({v_codec})\n🎵 {a_codec} @ {a_bitrate}"

            loc = item.get("output_path", "Not downloaded yet")
            if item.get("error"):
                loc += f"\n[bold red]Error: {item['error']}[/bold red]"

            table.add_row(marker, title_cell, pct_str, size_cell, specs_cell, loc)

        console.print(table)

        summary_text = (
            f"[bold]Total Videos in {self.history_file.name}:[/bold] {len(self.videos_dict)} | "
            f"[bold green]Completed:[/bold green] {completed_cnt} | "
            f"[bold yellow]In Progress:[/bold yellow] {downloading_cnt} | "
            f"[bold red]Failed:[/bold red] {failed_cnt} | "
            f"[bold blue]Pending:[/bold blue] {pending_cnt} | "
            f"[bold cyan]Total Size Downloaded:[/bold cyan] {self._format_size(total_dl_bytes)}"
        )
        console.print(Panel(summary_text, title="📊 Download History Summary", border_style="bold magenta"))

    def _format_size(self, size_bytes: int) -> str:
        if not size_bytes or size_bytes <= 0:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"
