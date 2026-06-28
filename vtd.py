"""Client for a companion voicetodo-server (github.com/haarisaltaf/voicetodo-server).

Lifeboard proxies the browser's todo actions through this module so the voicetodo
API key stays server-side and there are no cross-origin (CORS) problems talking
to a service that lives on another port. stdlib urllib only — the same approach
the voicetodo CLI uses.

Config (server URL + optional Bearer key) lives in Lifeboard's `settings` table.
"""
from __future__ import annotations

import json
import mimetypes
import socket
import urllib.error
import urllib.request
import uuid
from typing import Optional

import db


class VtdError(RuntimeError):
    """Upstream/transport error. `status` carries an HTTP-ish code for the proxy."""
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ config

def get_config(conn) -> dict:
    return {
        "url": (db.get_setting(conn, "voicetodo_url", "") or ""),
        "api_key": (db.get_setting(conn, "voicetodo_key", "") or ""),
    }


def set_config(conn, url: str, api_key: str) -> None:
    db.set_setting(conn, "voicetodo_url", (url or "").strip().rstrip("/"))
    db.set_setting(conn, "voicetodo_key", (api_key or "").strip())


# ------------------------------------------------------------------ requests

def _headers(api_key: str, auth: bool = True, extra: Optional[dict] = None) -> dict:
    h = {"Accept": "application/json"}
    if auth and api_key:
        h["Authorization"] = f"Bearer {api_key}"
    if extra:
        h.update(extra)
    return h


def _parse(raw: bytes):
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise VtdError(f"Bad JSON from voicetodo server: {e}", 502)


def request(cfg: dict, method: str, path: str, json_body: Optional[dict] = None,
            auth: bool = True, timeout: float = 15.0):
    url = (cfg.get("url") or "").rstrip("/")
    if not url:
        raise VtdError("voicetodo server URL not configured", 400)
    data = None
    headers = _headers(cfg.get("api_key", ""), auth)
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _parse(r.read())
    except urllib.error.HTTPError as e:
        text = (e.read() or b"").decode("utf-8", "replace")
        raise VtdError(f"HTTP {e.code}: {text.strip() or e.reason}", e.code)
    except (urllib.error.URLError, socket.timeout) as e:
        raise VtdError(f"Could not reach voicetodo server: {e}", 502)


def upload_audio(cfg: dict, file_bytes: bytes, filename: str,
                 source: str = "lifeboard", timeout: float = 180.0):
    """Forward a recorded/uploaded audio blob to POST /audio as multipart."""
    url = (cfg.get("url") or "").rstrip("/")
    if not url:
        raise VtdError("voicetodo server URL not configured", 400)
    boundary = uuid.uuid4().hex
    nl = b"\r\n"
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts = [
        f"--{boundary}".encode(),
        b'Content-Disposition: form-data; name="source"', b"", source.encode("utf-8"),
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="audio"; filename="{filename}"'.encode(),
        f"Content-Type: {ctype}".encode(), b"", file_bytes,
        f"--{boundary}--".encode(), b"",
    ]
    body = nl.join(parts)
    headers = _headers(cfg.get("api_key", ""), True, {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    })
    req = urllib.request.Request(url + "/audio", data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _parse(r.read())
    except urllib.error.HTTPError as e:
        text = (e.read() or b"").decode("utf-8", "replace")
        raise VtdError(f"HTTP {e.code}: {text.strip() or e.reason}", e.code)
    except (urllib.error.URLError, socket.timeout) as e:
        raise VtdError(f"Could not reach voicetodo server: {e}", 502)
