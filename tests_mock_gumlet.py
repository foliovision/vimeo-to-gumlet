"""Tiny stand-in for api.gumlet.com to sanity-check upload_to_gumlet.py.

Routes:
    POST /v1/video/assets/upload                          -> {asset_id, upload_url}
    POST /v1/video/assets/{id}/thumbnail                  -> {upload_url}
    POST /v1/video/assets/{id}/subtitle/upload            -> {upload_url, language_code}
    PUT  /put/<token>                                     -> 200

Run: python3 mock_gumlet.py &  (listens on :8777)
"""
from __future__ import annotations

import json
import re
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

UPLOADS: dict[str, dict] = {}

ASSET_RE = re.compile(r"^/v1/video/assets/upload/?$")
ASSET_GET_RE = re.compile(r"^/v1/video/assets/(?P<id>[^/]+)/?$")
THUMB_RE = re.compile(r"^/v1/video/assets/(?P<id>[^/]+)/thumbnail/?$")
SUB_RE = re.compile(r"^/v1/video/assets/(?P<id>[^/]+)/subtitle/upload/?$")
FOLDER_RE = re.compile(
    r"^/v1/video/workspaces/(?P<ws>[^/]+)/folders/(?P<fid>[^/]+)/?$"
)
PUT_RE = re.compile(r"^/put/(?P<tok>[^/]+)/?$")

FOLDER_MOVES: list[dict] = []
ASSETS: dict[str, dict] = {}

WORKSPACE_ID = "ws_mock"


def _ready_asset(aid: str, subtitle_langs: list[str] | None = None) -> dict:
    base = f"https://video.gumlet.io/{WORKSPACE_ID}/{aid}"
    return {
        "asset_id": aid,
        "status": "ready",
        "workspace_id": WORKSPACE_ID,
        "input": {
            "title": f"Mock {aid}",
            "additional_tracks": [
                {
                    "type": "subtitle",
                    "language_code": lang,
                    "name": lang.upper(),
                    "url": f"{WORKSPACE_ID}/{aid}/origin-{aid}-subtitle-{lang}",
                }
                for lang in (subtitle_langs or [])
            ],
        },
        "output": {
            "format": "hls",
            "playback_url": f"{base}/main.m3u8",
            "thumbnail_url": [f"{base}/thumbnail-1-0.png?v=0"],
            "preview_thumbnails_url": f"{base}/preview_thumbnails.vtt",
        },
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict | list) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body_bytes = self.rfile.read(length) if length else b""
        try:
            body = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            body = {}

        base = f"http://{self.headers.get('Host', 'localhost:8777')}"

        if ASSET_RE.match(self.path):
            aid = f"asset_{uuid.uuid4().hex[:8]}"
            tok = uuid.uuid4().hex
            UPLOADS[tok] = {"kind": "video", "asset_id": aid, "meta": body}
            ASSETS[aid] = _ready_asset(aid)
            return self._json(200, {
                "asset_id": aid,
                "upload_url": f"{base}/put/{tok}",
                "status": "created",
            })

        m = THUMB_RE.match(self.path)
        if m:
            tok = uuid.uuid4().hex
            UPLOADS[tok] = {"kind": "thumbnail", "asset_id": m.group("id")}
            return self._json(200, {
                "upload_url": f"{base}/put/{tok}",
                "asset_id": m.group("id"),
            })

        m = SUB_RE.match(self.path)
        if m:
            aid = m.group("id")
            langs = body.get("language_codes") or []
            out = []
            for lang in langs:
                tok = uuid.uuid4().hex
                UPLOADS[tok] = {
                    "kind": "subtitle",
                    "asset_id": aid,
                    "language_code": lang,
                }
                out.append({
                    "language_code": lang,
                    "upload_url": f"{base}/put/{tok}",
                })
                # Mirror the uploaded subtitle into the asset record
                # so GET /v1/video/assets/{id} reports it.
                asset = ASSETS.setdefault(aid, _ready_asset(aid))
                asset["input"]["additional_tracks"].append({
                    "type": "subtitle",
                    "language_code": lang,
                    "name": lang.upper(),
                    "url": f"{WORKSPACE_ID}/{aid}/origin-{aid}-subtitle-{lang}",
                })
            # Match "single object" shape when only one language requested.
            return self._json(200, out[0] if len(out) == 1 else {"subtitles": out})

        m = FOLDER_RE.match(self.path)
        if m:
            FOLDER_MOVES.append({
                "workspace_id": m.group("ws"),
                "folder_id": m.group("fid"),
                "asset_ids": body.get("asset_ids", []),
            })
            return self._json(200, {
                "folder_id": m.group("fid"),
                "asset_ids": body.get("asset_ids", []),
            })

        return self._json(404, {"error": f"unknown path {self.path}"})

    def do_GET(self) -> None:  # noqa: N802
        m = ASSET_GET_RE.match(self.path)
        if m:
            asset = ASSETS.get(m.group("id"))
            if asset is None:
                return self._json(404, {"error": "unknown asset"})
            return self._json(200, asset)
        return self._json(404, {"error": f"unknown path {self.path}"})

    def do_PUT(self) -> None:  # noqa: N802
        m = PUT_RE.match(self.path)
        if not m:
            return self._json(404, {"error": "unknown put path"})
        tok = m.group("tok")
        record = UPLOADS.get(tok)
        if not record:
            return self._json(403, {"error": "bad token"})
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else b""
        record["bytes"] = len(data)
        record["content_type"] = self.headers.get("Content-Type")
        record["consumed"] = True
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:  # quieter logs
        return


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", 8777), Handler)
    print("mock gumlet listening on :8777")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
