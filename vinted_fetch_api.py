from __future__ import annotations

import json
import os
import hmac
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import app


ROOT = Path(__file__).resolve().parent
SERVER_VERSION = "1.0.8"
HOST = os.environ.get("VINTED_FETCH_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("VINTED_FETCH_API_PORT", "8797"))
ACCESS_TOKEN = os.environ.get("VINTED_FETCH_API_TOKEN", "")
LOG_PATH = Path(os.environ.get("VINTED_FETCH_API_LOG_PATH", ROOT / "fetch-api.log"))
MAX_JSON_BODY_BYTES = max(1024, int(os.environ.get("VINTED_FETCH_API_MAX_JSON_BODY_BYTES", "16384")))


def log_event(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except OSError:
        pass


def is_allowed_vinted_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and (hostname == "www.vinted.fr" or hostname.endswith(".vinted.fr"))
        and parsed.path == "/api/v2/catalog/items"
    )


class FetchApiHandler(BaseHTTPRequestHandler):
    server_version = f"VintedFetchApi/{SERVER_VERSION}"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        log_event(f"{self.client_address[0]} GET {parsed.path}")
        if parsed.path == "/health":
            self.send_json(
                {
                    "ok": True,
                    "service": "vinted_fetch_api",
                    "version": SERVER_VERSION,
                    "pid": os.getpid(),
                }
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        started_at = time.monotonic()
        client_ip = self.client_address[0]
        log_event(f"{client_ip} POST {parsed.path}")
        try:
            if parsed.path != "/api/vinted/json":
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            self.require_token()
            payload = self.read_json()
            raw_url = str(payload.get("url", "")).strip()
            api_url = app.search_url_to_api_url(raw_url)
            log_event(f"{client_ip} fetch {api_url}")
            if not is_allowed_vinted_url(api_url):
                raise PermissionError("URL Vinted non autorisee.")

            data = app.fetch_vinted_json_direct(api_url)
            self.send_json({"ok": True, "data": data})
            duration_ms = int((time.monotonic() - started_at) * 1000)
            item_count = len(data.get("items", [])) if isinstance(data, dict) else 0
            log_event(f"{client_ip} 200 ok items={item_count} duration={duration_ms}ms")
        except PermissionError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=401)
            duration_ms = int((time.monotonic() - started_at) * 1000)
            log_event(f"{client_ip} 401 {exc} duration={duration_ms}ms")
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            duration_ms = int((time.monotonic() - started_at) * 1000)
            log_event(f"{client_ip} 400 {exc} duration={duration_ms}ms")

    def require_token(self) -> None:
        if not ACCESS_TOKEN:
            raise PermissionError("Token API non configure.")
        authorization = self.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token, ACCESS_TOKEN):
            raise PermissionError("Token API invalide.")

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RuntimeError("Content-Length invalide.") from exc
        if length < 0 or length > MAX_JSON_BODY_BYTES:
            raise RuntimeError(
                f"Requete trop volumineuse (maximum {MAX_JSON_BODY_BYTES} octets)."
            )
        if length == 0:
            return {}
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Le corps JSON doit etre un objet.")
        return payload

    def send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        log_event(f"{self.client_address[0]} {format % args}")


def main() -> None:
    if not ACCESS_TOKEN:
        raise SystemExit("Definis VINTED_FETCH_API_TOKEN avant de lancer ce service.")

    server = ThreadingHTTPServer((HOST, PORT), FetchApiHandler)
    log_event(f"Vinted Fetch API lance: http://{HOST}:{PORT}")
    log_event(f"Script: {Path(__file__).resolve()}")
    log_event(f"Log: {LOG_PATH.resolve()}")
    log_event("Garde cette fenetre ouverte pour accepter les appels distants.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Arret...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
