"""Local web server: the explorer page backed by the real Python pipeline.

    python -m ctxc serve [--port 8000]

Uses only the standard library (``http.server``) and binds to 127.0.0.1, so
it is reachable from this machine only.

API (JSON in, JSON out):

    GET  /                     the explorer page
    GET  /api/samples          bundled sample chats  [{id, title, note}]
    POST /api/compile          {sample: id}  or  {transcript, filename?, summary?}
                               -> compiled chat data + "session" id
    POST /api/query            {session, query} -> symbols the loader resolved
    POST /api/reset            {session}        -> start the loader session over
"""
from __future__ import annotations

import json
import threading
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .dashboard import SAMPLES, TEMPLATE, analyze_chat, sample_files
from .loader import ContextLoader
from .tokens import BACKEND
from .transcript import load_transcript, parse_any

MAX_BODY = 8 * 1024 * 1024        # 8 MB of transcript is plenty
MAX_SESSIONS = 50                 # oldest compiled chats are forgotten first

_sessions: "OrderedDict[str, ContextLoader]" = OrderedDict()
_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _page() -> bytes:
    body = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", "null")
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">"
            "</head><body>\n" + body + "\n</body></html>\n").encode("utf-8")


def _new_session(loader: ContextLoader) -> str:
    sid = uuid.uuid4().hex[:12]
    with _lock:
        _sessions[sid] = loader
        while len(_sessions) > MAX_SESSIONS:
            _sessions.popitem(last=False)
    return sid


def _loader(sid: str) -> ContextLoader:
    with _lock:
        loader = _sessions.get(sid)
    if loader is None:
        raise ApiError(404, "This compiled chat has expired. Compile it again.")
    return loader


def api_compile(req: dict) -> dict:
    if req.get("sample"):
        spec = next((s for s in SAMPLES if s["id"] == req["sample"]), None)
        if spec is None:
            raise ApiError(404, f"No sample called {req['sample']!r}.")
        f = sample_files(spec["id"])
        data, strict = analyze_chat(load_transcript(f["chat"]), str(f["chat"]), f["summary"], f["gold"],
                                    f["queries"], f["probes"], **spec)
    else:
        raw = req.get("transcript") or ""
        if not raw.strip():
            raise ApiError(400, "Paste a transcript or choose a file first.")
        name = req.get("filename") or "pasted chat"
        try:
            turns = parse_any(raw, name)
        except (ValueError, KeyError, StopIteration) as e:
            raise ApiError(400, f"Could not read this transcript: {e}.")
        if not turns:
            raise ApiError(400, "No messages found. Plain text needs lines starting with 'User:' and 'Assistant:'.")
        data, strict = analyze_chat(turns, name, req.get("summary"), None, None, title=name,
                                    note="Your own chat. No expected-fact list exists for it, so fact retention "
                                         "is not scored.")
    data["session"] = _new_session(ContextLoader(strict.bundle))
    data["tokenizer"] = BACKEND
    return data


def api_query(req: dict) -> dict:
    loader = _loader(req.get("session", ""))
    query = (req.get("query") or "").strip()
    if not query:
        raise ApiError(400, "Type a message first.")
    res = loader.on_query(query)
    return {
        "query": query,
        "resolved": [{"symbol": r.symbol, "status": r.status, "matched_by": r.matched_by,
                      "text": r.text, "tokens": r.tokens} for r in res],
        "header_tokens": loader.stats.header_tokens,
        "expanded_tokens": loader.stats.expanded_tokens,
    }


def api_reset(req: dict) -> dict:
    old = _loader(req.get("session", ""))
    loader = ContextLoader(old.b)
    with _lock:
        _sessions[req["session"]] = loader
    return {"header_tokens": loader.stats.header_tokens, "expanded_tokens": 0}


ROUTES = {"/api/compile": api_compile, "/api/query": api_query, "/api/reset": api_reset}


class Handler(BaseHTTPRequestHandler):
    server_version = "ctxc"

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, _page(), "text/html; charset=utf-8")
        elif path == "/api/samples":
            self._json(200, {"samples": SAMPLES, "tokenizer": BACKEND})
        else:
            self._json(404, {"error": "Not found."})

    def do_POST(self):
        fn = ROUTES.get(self.path.split("?")[0])
        if fn is None:
            return self._json(404, {"error": "Not found."})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ApiError(413, "That file is too large (limit 8 MB).")
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise ApiError(400, "The request body was not valid JSON.")
            self._json(200, fn(req))
        except ApiError as e:
            self._json(e.status, {"error": str(e)})
        except Exception as e:  # report pipeline crashes to the page instead of hanging up
            self._json(500, {"error": f"The compiler failed on this input: {type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")


def serve(port: int = 8000, host: str = "127.0.0.1"):
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"ctxc explorer running at http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
