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
THUMB_RE = re.compile(r"^/v1/video/assets/(?P<id>[^/]+)/thumbnail/?$")
SUB_RE = re.compile(r"^/v1/video/assets/(?P<id>[^/]+)/subtitle/upload/?$")
PUT_RE = re.compile(r"^/put/(?P<tok>[^/]+)/?$")


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
            langs = body.get("language_codes") or []
            out = []
            for lang in langs:
                tok = uuid.uuid4().hex
                UPLOADS[tok] = {
                    "kind": "subtitle",
                    "asset_id": m.group("id"),
                    "language_code": lang,
                }
                out.append({
                    "language_code": lang,
                    "upload_url": f"{base}/put/{tok}",
                })
            # Match "single object" shape when only one language requested.
            return self._json(200, out[0] if len(out) == 1 else {"subtitles": out})

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
