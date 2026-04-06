"""
Google Drive music integration.

Downloads royalty-free music from a Drive folder (with subfolders)
and caches files locally to avoid re-downloading on every run.

Setup (one-time):
  1. Google Cloud Console → enable Drive API → create OAuth2 Desktop credentials
  2. Download credentials.json → place in config/
  3. Set MUSIC_SOURCE=drive and DRIVE_MUSIC_FOLDER_ID in .env
  4. pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
  5. First run opens browser for consent → token saved to config/google_token.json

Subsequent runs: fully automatic, no browser needed.
"""
import io
import json
import os
from pathlib import Path

from rich.console import Console

console = Console()

# Resolved at runtime to avoid circular imports with config
_BASE_DIR = Path(__file__).parent.parent.parent
_CONFIG_DIR = _BASE_DIR / "config"
_CREDENTIALS_PATH = _CONFIG_DIR / "credentials.json"
_TOKEN_PATH = _CONFIG_DIR / "google_token.json"

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
MUSIC_MIME_TYPES = {
    "audio/mpeg",        # .mp3
    "audio/wav",         # .wav
    "audio/x-wav",
    "audio/ogg",         # .ogg
    "audio/flac",        # .flac
}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def get_credentials():
    """
    Load or refresh OAuth2 credentials.
    Opens browser on first run; uses saved token afterwards.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None

    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            console.print("  [dim]Refreshing Google Drive token...[/dim]")
            creds.refresh(Request())
        else:
            if not _CREDENTIALS_PATH.exists():
                raise FileNotFoundError(
                    f"credentials.json not found in {_CONFIG_DIR}.\n"
                    "Download it from Google Cloud Console → APIs & Services → Credentials\n"
                    "(OAuth 2.0 Client ID, type: Desktop app)"
                )
            console.print("  [yellow]Opening browser for Google Drive authorization...[/yellow]")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_CREDENTIALS_PATH), SCOPES
            )
            creds = flow.run_local_server(port=0)

        _TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        console.print("  [green]Google Drive authorized — token saved[/green]")

    return creds


def build_service():
    """Build and return the Drive API service client."""
    from googleapiclient.discovery import build
    creds = get_credentials()
    return build("drive", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# File listing (recursive)
# ---------------------------------------------------------------------------

def list_all_music(service, folder_id: str, _path: str = "") -> list[dict]:
    """
    Recursively list all music files in a Drive folder and its subfolders.
    Returns list of dicts: {id, name, mime_type, folder_path}
    Sorted by folder_path + name so rotation is consistent across runs.
    """
    results = []

    page_token = None
    while True:
        query = f"'{folder_id}' in parents and trashed = false"
        resp = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageSize=200,
            pageToken=page_token,
        ).execute()

        for f in resp.get("files", []):
            if f["mimeType"] == "application/vnd.google-apps.folder":
                # Recurse into subfolder
                sub_path = f"{_path}/{f['name']}" if _path else f["name"]
                results.extend(list_all_music(service, f["id"], sub_path))
            elif f["mimeType"] in MUSIC_MIME_TYPES or _has_audio_extension(f["name"]):
                results.append({
                    "id":          f["id"],
                    "name":        f["name"],
                    "mime_type":   f["mimeType"],
                    "folder_path": _path,
                })

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    results.sort(key=lambda x: (x["folder_path"], x["name"]))
    return results


def _has_audio_extension(name: str) -> bool:
    return Path(name).suffix.lower() in {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def get_cached_path(file_info: dict, cache_dir: Path) -> Path:
    """Return the expected local cache path for a Drive file."""
    safe_name = file_info["id"] + "_" + _safe_filename(file_info["name"])
    return cache_dir / safe_name


def ensure_cached(service, file_info: dict, cache_dir: Path) -> Path:
    """
    Download the Drive file to cache if not already present.
    Returns the local path.
    """
    from googleapiclient.http import MediaIoBaseDownload

    cache_dir.mkdir(parents=True, exist_ok=True)
    local_path = get_cached_path(file_info, cache_dir)

    if local_path.exists():
        return local_path  # already cached

    console.print(
        f"  Downloading: [cyan]{file_info['folder_path']}/{file_info['name']}[/cyan]"
    )

    request = service.files().get_media(fileId=file_info["id"])
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    local_path.write_bytes(buf.getvalue())
    console.print(f"  [green]Cached → {local_path.name}[/green]")
    return local_path


def _safe_filename(name: str) -> str:
    invalid = r'\/:*?"<>|'
    for ch in invalid:
        name = name.replace(ch, "_")
    return name.strip()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_drive_music(variation: int = 0) -> Path | None:
    """
    Main entry point called by video_builder.get_background_music().
    Authenticates, lists all music in the configured Drive folder,
    selects one file by variation index, downloads if needed, returns local Path.
    """
    from config.settings import DRIVE_MUSIC_FOLDER_ID, MUSIC_CACHE_DIR

    if not DRIVE_MUSIC_FOLDER_ID:
        console.print(
            "  [red]DRIVE_MUSIC_FOLDER_ID not set in .env — "
            "falling back to no music[/red]"
        )
        return None

    try:
        service = build_service()
        console.print("  [dim]Listing music from Google Drive...[/dim]")
        files = list_all_music(service, DRIVE_MUSIC_FOLDER_ID)

        if not files:
            console.print("  [yellow]No music files found in Drive folder[/yellow]")
            return None

        chosen = files[variation % len(files)]
        console.print(
            f"  Music: [cyan]{chosen['folder_path']}/{chosen['name']}[/cyan] "
            f"({variation % len(files) + 1}/{len(files)})"
        )

        local_path = ensure_cached(service, chosen, MUSIC_CACHE_DIR)
        return local_path

    except Exception as e:
        console.print(f"  [red]Drive music error: {e}[/red]")
        console.print("  [yellow]Assembling without music[/yellow]")
        return None
