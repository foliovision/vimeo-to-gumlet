#!/usr/bin/env python3
"""
Upload FV-style video folders to Gumlet.tv.

Folder layout (one per video, directly inside --root):
    {video_id} {video name}/
        {video name}-source.mp4                  (preferred source)
        {video name}-{360|540|720|1080}p.mp4     (renditions, used as fallback)
        subtitles-{lang}-{label}.vtt.vtt         (one or more; lang is ISO 639-1)
        thumbnail-{WxH}.jpg                      (any of the standard sizes)
        video-details.json                       (optional metadata)

If `*-source.mp4` is missing or has size 0, we fall back to the highest-
resolution `*-{N}p.mp4` present.

For each folder we:
    1.  POST /v1/video/assets/upload   → {asset_id, upload_url}
    2.  PUT the chosen source file to upload_url
    3.  POST /v1/video/assets/{asset_id}/thumbnail → {upload_url}
        PUT the largest thumbnail JPEG to that URL
    4.  For each subtitle file:
        POST /v1/video/assets/{asset_id}/subtitle/upload
             body: {"language_codes": ["<lang>"]}
        Response returns an upload URL per language; PUT the VTT file to it.
    5.  (optional) After uploads, move the assets into a target folder via
        POST /v1/video/workspaces/{workspace_id}/folders/{folder_id}
             body: {"asset_ids": [...]}

Authentication:
    Requires GUMLET_API_KEY in the environment.
    Requires GUMLET_COLLECTION_ID (a.k.a. workspace_id) unless
    passed via --collection-id.
    Optionally --parent-id / GUMLET_PARENT_ID to place the uploaded assets
    in a specific folder inside that workspace.

Docs:
    https://docs.gumlet.com/reference/create-asset-direct-upload
    https://docs.gumlet.com/reference/select-from-image-file
    https://docs.gumlet.com/reference/video-asset-upload-subtitle
    https://docs.gumlet.com/reference/update-folder
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

API_BASE = "https://api.gumlet.com/v1"

RENDITION_RE = re.compile(r"-(?P<res>\d{3,4})p\.mp4$", re.IGNORECASE)
SOURCE_RE = re.compile(r"-source\.mp4$", re.IGNORECASE)
# subtitles-en-English Foliovision Promo Video.vtt.vtt
SUBTITLE_RE = re.compile(
    r"^subtitles-(?P<lang>[a-z]{2,3})-(?P<label>.+?)\.vtt\.vtt$",
    re.IGNORECASE,
)
THUMB_RE = re.compile(r"^thumbnail-(?P<w>\d+)x(?P<h>\d+)\.jpe?g$", re.IGNORECASE)
FOLDER_RE = re.compile(r"^(?P<id>\d+)\s+(?P<name>.+)$")

log = logging.getLogger("gumlet-upload")


@dataclass
class VideoFolder:
    path: Path
    video_id: str
    name: str
    source: Path
    thumbnail: Path | None
    subtitles: list[Path] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


# ---------- folder discovery ----------------------------------------------


def _pick_source(folder: Path) -> Path | None:
    """Prefer *-source.mp4 if present and non-empty; else highest -{N}p.mp4."""
    for f in folder.iterdir():
        if SOURCE_RE.search(f.name) and f.is_file() and f.stat().st_size > 0:
            return f

    renditions: list[tuple[int, Path]] = []
    for f in folder.iterdir():
        m = RENDITION_RE.search(f.name)
        if m and f.is_file() and f.stat().st_size > 0:
            renditions.append((int(m.group("res")), f))
    if not renditions:
        return None
    renditions.sort(key=lambda t: t[0], reverse=True)
    return renditions[0][1]


def _pick_thumbnail(folder: Path) -> Path | None:
    """Return the largest thumbnail by pixel area."""
    candidates: list[tuple[int, Path]] = []
    for f in folder.iterdir():
        m = THUMB_RE.match(f.name)
        if m and f.is_file():
            candidates.append((int(m.group("w")) * int(m.group("h")), f))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def _collect_subtitles(folder: Path) -> list[Path]:
    return sorted(
        f for f in folder.iterdir()
        if f.is_file() and SUBTITLE_RE.match(f.name)
    )


def _load_details(folder: Path) -> dict[str, Any]:
    p = folder / "video-details.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        log.warning("%s is not valid JSON: %s", p, exc)
        return {}


def discover_folders(root: Path) -> list[VideoFolder]:
    result: list[VideoFolder] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        m = FOLDER_RE.match(entry.name)
        if not m:
            log.debug("Skipping %s (does not match '{id} {name}')", entry.name)
            continue
        source = _pick_source(entry)
        if source is None:
            log.warning("%s has no usable source video, skipping", entry.name)
            continue
        result.append(VideoFolder(
            path=entry,
            video_id=m.group("id"),
            name=m.group("name"),
            source=source,
            thumbnail=_pick_thumbnail(entry),
            subtitles=_collect_subtitles(entry),
            details=_load_details(entry),
        ))
    return result


# ---------- Gumlet client --------------------------------------------------


class GumletClient:
    def __init__(self, api_key: str, collection_id: str, session: requests.Session | None = None):
        self.api_key = api_key
        self.collection_id = collection_id
        self.s = session or requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        })

    # --- asset creation / video upload ------------------------------------

    def create_asset_direct_upload(
        self,
        *,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        fmt: str = "hls",
        resolutions: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "collection_id": self.collection_id,
            "format": fmt,
            "title": title,
        }
        if description:
            body["description"] = description
        if tags:
            body["tag"] = tags
        if metadata:
            # Gumlet rejects a JSON-encoded string ("body/metadata must be
            # object"), so send the dict directly.
            body["metadata"] = metadata
        if resolutions:
            body["resolution"] = resolutions
        r = self.s.post(
            f"{API_BASE}/video/assets/upload",
            headers={"Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def put_file(self, upload_url: str, path: Path, content_type: str) -> None:
        # Use a bare request (no session) so the Bearer auth header isn't
        # sent to the pre-signed S3 URL, which rejects "Only one auth
        # mechanism allowed".
        with path.open("rb") as fh:
            r = requests.put(
                upload_url,
                data=fh,
                headers={"Content-Type": content_type},
                timeout=None,
            )
        r.raise_for_status()

    # --- asset status -----------------------------------------------------

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        r = self.s.get(f"{API_BASE}/video/assets/{asset_id}", timeout=60)
        r.raise_for_status()
        return r.json()

    def wait_until_ready(
        self,
        asset_id: str,
        *,
        timeout_s: float = 900,
        poll_interval_s: float = 5,
    ) -> dict[str, Any]:
        """Poll GET /video/assets/{id} until status in {ready, errored}."""
        deadline = time.monotonic() + timeout_s
        last_status = None
        while time.monotonic() < deadline:
            asset = self.get_asset(asset_id)
            status = asset.get("status")
            if status != last_status:
                log.info("    status=%s progress=%s",
                         status, asset.get("progress"))
                last_status = status
            if status in ("ready", "errored", "failed"):
                return asset
            time.sleep(poll_interval_s)
        raise TimeoutError(
            f"asset {asset_id} not ready after {timeout_s:.0f}s"
        )

    # --- thumbnail --------------------------------------------------------

    def request_thumbnail_upload(self, asset_id: str) -> dict[str, Any]:
        r = self.s.post(
            f"{API_BASE}/video/assets/{asset_id}/thumbnail",
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    # --- subtitles --------------------------------------------------------

    def request_subtitle_upload(
        self, asset_id: str, language_codes: list[str]
    ) -> dict[str, Any]:
        r = self.s.post(
            f"{API_BASE}/video/assets/{asset_id}/subtitle/upload",
            headers={"Content-Type": "application/json"},
            json={"language_codes": language_codes},
            timeout=60,
        )
        r.raise_for_status()
        # Response shape varies; can be {upload_url: ..., language_code: "en"}
        # or a list of such objects. Caller must handle both.
        return r.json()

    # --- folder placement -------------------------------------------------

    def move_to_folder(
        self, workspace_id: str, folder_id: str, asset_ids: list[str]
    ) -> dict[str, Any]:
        """Move one or more assets into a specific folder.

        Docs: https://docs.gumlet.com/reference/update-folder
        """
        r = self.s.post(
            f"{API_BASE}/video/workspaces/{workspace_id}/folders/{folder_id}",
            headers={"Content-Type": "application/json"},
            json={"asset_ids": asset_ids},
            timeout=60,
        )
        r.raise_for_status()
        return r.json() if r.content else {}


# ---------- per-folder orchestration --------------------------------------


def _subtitle_upload_url_for(
    payload: Any, language_code: str
) -> str | None:
    """Normalize Gumlet's subtitle response to a URL for `language_code`."""
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, list):
        candidates = [p for p in payload if isinstance(p, dict)]
    elif isinstance(payload, dict):
        # Either a single {upload_url, language_code} or
        # {subtitles: [{upload_url, language_code}, ...]} / similar.
        if "upload_url" in payload:
            candidates = [payload]
        else:
            for key in ("signed_urls", "subtitles", "uploads", "results"):
                if key in payload and isinstance(payload[key], list):
                    candidates = [p for p in payload[key] if isinstance(p, dict)]
                    break
    for c in candidates:
        if c.get("language_code", language_code) == language_code and "upload_url" in c:
            return c["upload_url"]
    # Fallback: if there is exactly one candidate, use it
    if len(candidates) == 1 and "upload_url" in candidates[0]:
        return candidates[0]["upload_url"]
    return None


def upload_folder(client: GumletClient, vf: VideoFolder, dry_run: bool = False) -> dict[str, Any]:
    log.info("== %s (%s) ==", vf.name, vf.video_id)
    log.info("  source:    %s (%d bytes)", vf.source.name, vf.source.stat().st_size)
    if vf.thumbnail:
        log.info("  thumbnail: %s", vf.thumbnail.name)
    for s in vf.subtitles:
        log.info("  subtitle:  %s", s.name)

    if dry_run:
        return {"dry_run": True, "video_id": vf.video_id}

    # 1. create asset + upload source
    meta = {
        "fv_video_id": vf.video_id,
        "source_file": vf.source.name,
    }
    asset = client.create_asset_direct_upload(
        title=vf.name,
        description=vf.details.get("description", ""),
        tags=[f"fv-video-id:{vf.video_id}"],
        metadata=meta,
    )
    asset_id = asset.get("asset_id") or asset.get("id")
    upload_url = asset["upload_url"]
    log.info("  asset_id = %s", asset_id)
    client.put_file(upload_url, vf.source, "video/mp4")

    # Thumbnail and subtitle uploads only work once Gumlet has transcoded
    # the video and the asset is in the "ready" state.
    if vf.thumbnail is not None or vf.subtitles:
        log.info("  waiting for asset to become ready...")
        final = client.wait_until_ready(asset_id)
        if final.get("status") != "ready":
            log.error("  asset did not become ready (status=%s); "
                      "skipping thumbnail+subtitles",
                      final.get("status"))
            return {"asset_id": asset_id, "video_id": vf.video_id,
                    "status": final.get("status")}

    # 2. thumbnail
    if vf.thumbnail is not None:
        thumb = client.request_thumbnail_upload(asset_id)
        client.put_file(thumb["upload_url"], vf.thumbnail, "image/jpeg")
        log.info("  thumbnail uploaded")

    # 3. subtitles — one POST per VTT so each file gets its own upload URL
    for sub in vf.subtitles:
        m = SUBTITLE_RE.match(sub.name)
        if m is None:
            continue
        lang = m.group("lang").lower()
        resp = client.request_subtitle_upload(asset_id, [lang])
        put_url = _subtitle_upload_url_for(resp, lang)
        if put_url is None:
            log.warning("  no upload_url for subtitle %s (response: %r)", sub.name, resp)
            continue
        client.put_file(put_url, sub, "text/vtt")
        log.info("  subtitle %s (%s) uploaded", lang, sub.name)

    return {"asset_id": asset_id, "video_id": vf.video_id}


# ---------- cli ------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--root", default="/home/ubuntu/fv-videos",
                   help="Directory containing '{id} {name}' subfolders")
    p.add_argument("--api-key", default=os.environ.get("GUMLET_API_KEY", ""),
                   help="Gumlet API key (env: GUMLET_API_KEY)")
    p.add_argument("--collection-id",
                   default=os.environ.get("GUMLET_COLLECTION_ID", ""),
                   help="Gumlet workspace id (env: GUMLET_COLLECTION_ID)")
    p.add_argument("--parent-id",
                   default=os.environ.get("GUMLET_PARENT_ID", ""),
                   help="Gumlet folder id to place assets into "
                        "(env: GUMLET_PARENT_ID). Optional.")
    p.add_argument("--dry-run", action="store_true",
                   help="List what would be uploaded but make no API calls")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    folders = discover_folders(Path(args.root))
    if not folders:
        log.error("No matching folders found under %s", args.root)
        return 1

    if not args.dry_run:
        if not args.api_key:
            log.error("GUMLET_API_KEY is required (or --api-key)")
            return 2
        if not args.collection_id:
            log.error("GUMLET_COLLECTION_ID is required (or --collection-id)")
            return 2

    client = None if args.dry_run else GumletClient(args.api_key, args.collection_id)
    results = []
    for vf in folders:
        try:
            results.append(upload_folder(client, vf, dry_run=args.dry_run))
        except requests.HTTPError as exc:
            log.error("  HTTP error for %s: %s — %s",
                      vf.name, exc, getattr(exc.response, "text", ""))
        except Exception as exc:
            log.exception("  failed for %s: %s", vf.name, exc)

    # Move uploaded assets into the requested folder in one call.
    if not args.dry_run and args.parent_id:
        asset_ids = [r["asset_id"] for r in results if r.get("asset_id")]
        if asset_ids:
            try:
                client.move_to_folder(
                    args.collection_id, args.parent_id, asset_ids,
                )
                log.info("moved %d asset(s) into folder %s",
                         len(asset_ids), args.parent_id)
            except requests.HTTPError as exc:
                log.error("failed to move assets into folder %s: %s — %s",
                          args.parent_id, exc,
                          getattr(exc.response, "text", ""))

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
