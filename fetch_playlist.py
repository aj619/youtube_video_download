import os
import json
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import yt_dlp

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY")
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
TOKEN_FILE = Path("token.json")
CLIENT_SECRETS_FILE = Path("client_secret.json")
WORK_CONFIG_DIR = Path("workerConfig")
INGESTION_FILE = WORK_CONFIG_DIR / "ingestion.json"

# ---------------------------------------------------------
# Public / Unlisted Playlist Fetcher (Using API Key)
# ---------------------------------------------------------
def fetch_public_playlist(target: str):
    """Fetches video URLs from a public/unlisted playlist using API Key or yt-dlp."""
    playlist_id = target.split("list=")[-1] if "list=" in target else target
    video_urls = []

    if API_KEY:
        try:
            print(f"Fetching public/unlisted playlist using API Key (ID: {playlist_id})...")
            youtube = build("youtube", "v3", developerKey=API_KEY)
            next_page_token = None

            while True:
                request = youtube.playlistItems().list(
                    part="snippet,contentDetails",
                    playlistId=playlist_id,
                    maxResults=50,
                    pageToken=next_page_token
                )
                response = request.execute()

                for item in response.get("items", []):
                    video_id = item.get("contentDetails", {}).get("videoId")
                    title = item.get("snippet", {}).get("title", "")
                    if video_id and title != "Private video":
                        video_urls.append(f"https://www.youtube.com/watch?v={video_id}")

                next_page_token = response.get("nextPageToken")
                if not next_page_token:
                    break

        except HttpError as e:
            print(f"API Key request note/error: {e}")
            print("Falling back to yt-dlp extractor...")

    if not video_urls:
        print(f"Fetching playlist videos via yt-dlp for: {target}...")
        target_url = target if target.startswith("http") else f"https://www.youtube.com/playlist?list={target}"
        ydl_opts = {'extract_flat': 'in_playlist', 'quiet': True, 'skip_download': True}
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=False)

        if 'entries' in info:
            for entry in info['entries']:
                if entry and 'id' in entry:
                    title = entry.get('title', '')
                    if title not in ('[Private video]', '[Deleted video]'):
                        video_urls.append(f"https://www.youtube.com/watch?v={entry['id']}")

    return video_urls

# ---------------------------------------------------------
# Private Playlist Fetcher (Using OAuth 2.0 Credentials)
# ---------------------------------------------------------
def get_client_config():
    """Dynamically builds client configuration dict from .env or client_secret.json."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    auth_uri = os.getenv("GOOGLE_AUTH_URI", "https://accounts.google.com/o/oauth2/auth")
    token_uri = os.getenv("GOOGLE_TOKEN_URI", "https://oauth2.googleapis.com/token")

    if client_id and client_secret:
        return {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": auth_uri,
                "token_uri": token_uri,
                "redirect_uris": ["http://localhost"]
            }
        }
    elif CLIENT_SECRETS_FILE.exists():
        with open(CLIENT_SECRETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        print("\nError: Missing OAuth credentials!", file=sys.stderr)
        print("Please provide GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your .env file,", file=sys.stderr)
        print(f"or place a 'client_secret.json' file in the root directory.\n", file=sys.stderr)
        sys.exit(1)

def get_oauth_service():
    """Authenticates via OAuth 2.0 using .env / client_secret.json and returns YouTube client."""
    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_config = get_client_config()
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)

def fetch_private_playlist(playlist_name_or_id: str):
    """Fetches video URLs from user's private playlists using OAuth 2.0."""
    print("Authenticating with YouTube OAuth 2.0...")
    youtube = get_oauth_service()

    # Step 1: List all user's playlists
    print("\nFetching your account playlists...")
    playlists = []
    next_page_token = None

    while True:
        request = youtube.playlists().list(
            part="snippet,contentDetails",
            mine=True,
            maxResults=50,
            pageToken=next_page_token
        )
        response = request.execute()

        for item in response.get("items", []):
            playlists.append({
                "id": item["id"],
                "title": item["snippet"]["title"],
                "item_count": item["contentDetails"]["itemCount"]
            })

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    if not playlists:
        print("No playlists found in your account.")
        return []

    # Step 2: Match playlist by ID or Title
    matched_playlist = None
    for pl in playlists:
        if pl['id'] == playlist_name_or_id or pl['title'].strip().lower() == playlist_name_or_id.strip().lower():
            matched_playlist = pl
            break

    if not matched_playlist:
        matches = [pl for pl in playlists if playlist_name_or_id.lower() in pl['title'].lower()]
        if matches:
            matched_playlist = matches[0]

    if not matched_playlist:
        print(f"Could not find playlist matching '{playlist_name_or_id}' in your account playlists:")
        for pl in playlists:
            print(f" - [{pl['title']}] (ID: {pl['id']})")
        return []

    print(f"\nSelected Playlist: '{matched_playlist['title']}' (ID: {matched_playlist['id']})")

    # Step 3: Fetch video URLs
    video_urls = []
    next_page_token = None

    while True:
        request = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=matched_playlist['id'],
            maxResults=50,
            pageToken=next_page_token
        )
        response = request.execute()

        for item in response.get("items", []):
            video_id = item.get("contentDetails", {}).get("videoId")
            title = item.get("snippet", {}).get("title", "")
            if video_id and title != "Private video":
                video_urls.append(f"https://www.youtube.com/watch?v={video_id}")

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    return video_urls

def fetch_private_playlists(playlist_targets: list) -> list:
    """Fetches video URLs from multiple private playlists using OAuth 2.0."""
    if not playlist_targets:
        return []

    all_urls = []
    for target in playlist_targets:
        urls = fetch_private_playlist(target)
        all_urls.extend(urls)

    return list(dict.fromkeys(all_urls))

# ---------------------------------------------------------
# Common Utilities
# ---------------------------------------------------------
def update_ingestion_file(urls: list):
    INGESTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing_data = {}
    if INGESTION_FILE.exists():
        try:
            with open(INGESTION_FILE, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception:
            existing_data = {}

    existing_urls = existing_data.get("urls", []) if isinstance(existing_data, dict) else []
    combined = list(dict.fromkeys(existing_urls + urls))

    with open(INGESTION_FILE, "w", encoding="utf-8") as f:
        json.dump({"urls": combined}, f, indent=2)
    print(f"\nSuccessfully updated '{INGESTION_FILE}' with {len(combined)} total video URLs ({len(urls)} new/extracted).")
    
    try:
        from state_manager import StateManager, WORK_HISTORY_FILE
        SETTINGS_FILE = WORK_CONFIG_DIR / "settings.json"
        settings = {}
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
        output_dir = Path(settings.get("output_directory", "./downloads"))

        sm = StateManager(WORK_HISTORY_FILE)
        sm.sync_with_ingestion(INGESTION_FILE, output_dir)
        print(f"Synced video list into '{WORK_HISTORY_FILE.name}'.")
    except Exception as e:
        print(f"Note: Could not sync state manager: {e}")

def main():
    parser = argparse.ArgumentParser(description="Fetch YouTube playlist video URLs into ingestion.json")
    parser.add_argument("target", help="Playlist ID, Playlist URL, or Playlist Name")
    parser.add_argument("--private", action="store_true", help="Use OAuth 2.0 to fetch from private account playlists")

    args = parser.parse_args()

    if args.private:
        urls = fetch_private_playlist(args.target)
    else:
        urls = fetch_public_playlist(args.target)

    if urls:
        update_ingestion_file(urls)
    else:
        print("No video URLs extracted.")

if __name__ == "__main__":
    main()
