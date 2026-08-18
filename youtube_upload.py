from __future__ import annotations

import random
import time
from pathlib import Path

from config import Settings
from utils import LOG

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]


def get_credentials(cfg: Settings, interactive: bool = True):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if cfg.youtube_token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(cfg.youtube_token_file), SCOPES)
        except Exception as exc:
            LOG.warning("Could not load YouTube token: %s", exc)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        cfg.youtube_token_file.write_text(creds.to_json(), encoding="utf-8")
    if creds and creds.valid:
        return creds
    if not interactive:
        raise RuntimeError("YouTube OAuth token missing/invalid. Run python youtube_auth.py once on a device with a browser.")
    if not cfg.youtube_client_secrets.exists():
        raise RuntimeError(f"YouTube OAuth client file not found: {cfg.youtube_client_secrets}")
    flow = InstalledAppFlow.from_client_secrets_file(str(cfg.youtube_client_secrets), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    cfg.youtube_token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def upload_video(cfg: Settings, video_path: Path, title: str, description: str, tags: list[str]) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    creds = get_credentials(cfg, interactive=False)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    status = {"privacyStatus": cfg.youtube_privacy, "selfDeclaredMadeForKids": cfg.youtube_made_for_kids}
    if cfg.youtube_synthetic_media:
        status["containsSyntheticMedia"] = True
    body = {
        "snippet": {
            "title": title[:100], "description": description[:5000], "tags": tags[:30],
            "categoryId": cfg.youtube_category_id, "defaultLanguage": cfg.language,
        },
        "status": status,
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    retries = 0
    while response is None:
        try:
            progress, response = request.next_chunk()
            if progress:
                LOG.info("YouTube upload: %d%%", int(progress.progress() * 100))
        except HttpError as exc:
            code = getattr(exc.resp, "status", None)
            if code not in {429, 500, 502, 503, 504} or retries >= 5:
                raise
            delay = min(30, (2 ** retries) + random.random())
            LOG.warning("YouTube transient HTTP %s; retrying in %.1fs", code, delay)
            time.sleep(delay)
            retries += 1
    video_id = response["id"]
    if cfg.youtube_paid_promotion:
        try:
            youtube.videos().update(
                part="paidProductPlacementDetails",
                body={"id": video_id, "paidProductPlacementDetails": {"hasPaidProductPlacement": True}},
            ).execute()
            LOG.info("Marked YouTube paid-promotion/commercial relationship disclosure")
        except Exception as exc:
            LOG.warning("Video uploaded but paid-promotion flag could not be set automatically: %s", exc)
    LOG.info("YouTube upload complete: %s", video_id)
    return video_id
