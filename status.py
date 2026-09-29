import argparse
import json
from pathlib import Path
from state_manager import StateManager, load_json, INGESTION_FILE, WORK_HISTORY_FILE
WORK_CONFIG_DIR = Path("workerConfig")
SETTINGS_FILE = WORK_CONFIG_DIR / "settings.json"

def main():
    parser = argparse.ArgumentParser(description="View YouTube Video Download Markers & Status List in work_history.json")
    parser.add_argument("--filter", choices=["completed", "downloading", "failed", "pending"],
                        help="Filter displayed list by status")
    parser.add_argument("--json", action="store_true", help="Print work_history.json status list as JSON")
    args = parser.parse_args()

    sm = StateManager(WORK_HISTORY_FILE)
    
    settings = load_json(SETTINGS_FILE, default_val={})
    output_dir = Path(settings.get("output_directory", "./downloads"))

    # Sync ingestion.json with work_history.json and check disk files
    sm.sync_with_ingestion(INGESTION_FILE, output_dir)

    if args.json:
        print(json.dumps(load_json(WORK_HISTORY_FILE), indent=2))
    else:
        sm.print_status_table(filter_status=args.filter)

if __name__ == "__main__":
    main()
