from __future__ import annotations

import json
import os
import random
import sqlite3
import secrets
import hashlib
import hmac
import re
import threading
import time
import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("VINTED_ALERTS_DB_PATH", ROOT / "vinted_alerts.db"))
HOST = os.environ.get("VINTED_ALERTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("VINTED_ALERTS_PORT", "8790"))
DEFAULT_INTERVAL_SECONDS = 180
DEFAULT_RANDOM_INTERVAL_PERCENT = 5
MAX_RANDOM_INTERVAL_PERCENT = 90
RANDOM_INTERVAL_PERCENT_STEP = 5
ADMIN_USERNAME = os.environ.get("VINTED_ALERTS_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_ENV = os.environ.get("VINTED_ALERTS_ADMIN_PASSWORD")
ADMIN_PASSWORD = ADMIN_PASSWORD_ENV or "admin123"
SESSION_COOKIE = "vinted_session"
SESSION_TTL_SECONDS = max(300, int(os.environ.get("VINTED_ALERTS_SESSION_TTL_SECONDS", "604800")))
SECURE_COOKIE = os.environ.get("VINTED_ALERTS_SECURE_COOKIE", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MAX_JSON_BODY_BYTES = max(1024, int(os.environ.get("VINTED_ALERTS_MAX_JSON_BODY_BYTES", "65536")))
LOGIN_ATTEMPT_LIMIT = max(1, int(os.environ.get("VINTED_ALERTS_LOGIN_ATTEMPT_LIMIT", "5")))
LOGIN_ATTEMPT_WINDOW_SECONDS = max(
    30,
    int(os.environ.get("VINTED_ALERTS_LOGIN_ATTEMPT_WINDOW_SECONDS", "300")),
)
FETCH_API_ENABLED = os.environ.get("VINTED_ALERTS_FETCH_API_ENABLED", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
FETCH_API_URL = os.environ.get("VINTED_ALERTS_FETCH_API_URL", "").rstrip("/")
FETCH_API_TOKEN = (
    os.environ.get("VINTED_ALERTS_FETCH_API_TOKEN", "")
    or os.environ.get("VINTED_FETCH_API_TOKEN", "")
)


VINTED_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "DNT": "1",
    "Referer": "https://www.vinted.fr/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
}


state_lock = threading.Lock()
check_lock = threading.Lock()
worker_stop = threading.Event()
worker_wakeup = threading.Event()
worker_thread: threading.Thread | None = None
last_check_started_at: str | None = None
last_check_finished_at: str | None = None
last_error: str | None = None
initialized_search_ids: set[int] = set()
next_check_at: dict[int, float] = {}
login_attempts: dict[str, list[float]] = {}
login_attempts_lock = threading.Lock()
vinted_lock = threading.Lock()
vinted_cookie_jar = http.cookiejar.CookieJar()


def build_vinted_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(vinted_cookie_jar),
    )


vinted_opener = build_vinted_opener()


def now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma busy_timeout = 30000")
    return conn


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, salt, digest = stored_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    ).hex()
    return hmac.compare_digest(candidate, digest)


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute(
            "insert into sessions(token, user_id, created_at) values(?, ?, ?)",
            (token, user_id, now_iso()),
        )
    return token


def get_session_user(token: str) -> dict | None:
    if not token:
        return None
    with db() as conn:
        row = conn.execute(
            """
            select u.id, u.username, u.is_admin, s.created_at as session_created_at
            from sessions s
            join users u on u.id = s.user_id
            where s.token = ?
            """,
            (token,),
        ).fetchone()
        if row and session_is_expired(row["session_created_at"]):
            conn.execute("delete from sessions where token = ?", (token,))
            return None
    if not row:
        return None
    user = dict(row)
    user.pop("session_created_at", None)
    return user


def session_is_expired(created_at: str) -> bool:
    try:
        created_timestamp = time.mktime(time.strptime(created_at, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError, OverflowError):
        return True
    return time.time() - created_timestamp >= SESSION_TTL_SECONDS


def delete_session(token: str) -> None:
    if not token:
        return
    with db() as conn:
        conn.execute("delete from sessions where token = ?", (token,))


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            create table if not exists users (
                id integer primary key autoincrement,
                username text not null unique,
                password_hash text not null,
                is_admin integer not null default 0,
                created_at text not null
            );

            create table if not exists sessions (
                token text primary key,
                user_id integer not null,
                created_at text not null,
                foreign key(user_id) references users(id) on delete cascade
            );

            create table if not exists settings (
                user_id integer not null,
                key text not null,
                value text not null,
                primary key(user_id, key),
                foreign key(user_id) references users(id) on delete cascade
            );

            create table if not exists searches (
                id integer primary key autoincrement,
                user_id integer not null,
                name text not null,
                url text not null,
                enabled integer not null default 1,
                interval_seconds integer not null default 180,
                created_at text not null,
                last_checked_at text,
                last_error text
            );

            create table if not exists seen_items (
                id integer primary key autoincrement,
                item_id text not null,
                search_id integer not null,
                title text not null,
                price text,
                price_amount real,
                currency text,
                url text not null,
                photo_url text,
                created_at text not null,
                notified_at text,
                unique(search_id, item_id),
                foreign key(search_id) references searches(id) on delete cascade
            );
            """
        )
        ensure_admin_user(conn)
        migrate_multi_user_schema(conn)


def ensure_admin_user(conn: sqlite3.Connection) -> int:
    row = conn.execute("select id from users where username = ?", (ADMIN_USERNAME,)).fetchone()
    if row:
        if ADMIN_PASSWORD_ENV:
            conn.execute(
                "update users set password_hash = ?, is_admin = 1 where id = ?",
                (hash_password(ADMIN_PASSWORD_ENV), int(row["id"])),
            )
        return int(row["id"])
    cursor = conn.execute(
        """
        insert into users(username, password_hash, is_admin, created_at)
        values(?, ?, 1, ?)
        """,
        (ADMIN_USERNAME, hash_password(ADMIN_PASSWORD), now_iso()),
    )
    return int(cursor.lastrowid)


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()]


def migrate_multi_user_schema(conn: sqlite3.Connection) -> None:
    admin_id = ensure_admin_user(conn)

    settings_columns = table_columns(conn, "settings")
    if "user_id" not in settings_columns:
        conn.executescript(
            """
            alter table settings rename to settings_old;
            create table settings (
                user_id integer not null,
                key text not null,
                value text not null,
                primary key(user_id, key),
                foreign key(user_id) references users(id) on delete cascade
            );
            """
        )
        conn.execute(
            "insert into settings(user_id, key, value) select ?, key, value from settings_old",
            (admin_id,),
        )
        conn.execute("drop table settings_old")

    searches_columns = table_columns(conn, "searches")
    if "user_id" not in searches_columns:
        conn.execute("alter table searches add column user_id integer")
    conn.execute("update searches set user_id = ? where user_id is null", (admin_id,))

    seen_columns = table_columns(conn, "seen_items")
    if "id" not in seen_columns:
        conn.executescript(
            """
            alter table seen_items rename to seen_items_old;
            create table seen_items (
                id integer primary key autoincrement,
                item_id text not null,
                search_id integer not null,
                title text not null,
                price text,
                price_amount real,
                currency text,
                url text not null,
                photo_url text,
                created_at text not null,
                notified_at text,
                unique(search_id, item_id),
                foreign key(search_id) references searches(id) on delete cascade
            );
            insert or ignore into seen_items(
                item_id, search_id, title, price, url, photo_url, created_at, notified_at
            )
            select item_id, search_id, title, price, url, photo_url, created_at, notified_at
            from seen_items_old;
            drop table seen_items_old;
            """
        )
        seen_columns = table_columns(conn, "seen_items")
    if "price_amount" not in seen_columns:
        conn.execute("alter table seen_items add column price_amount real")
    if "currency" not in seen_columns:
        conn.execute("alter table seen_items add column currency text")
    backfill_seen_item_prices(conn)


def parse_price_value(value: object) -> tuple[float | None, str]:
    if value is None:
        return None, ""
    text = str(value).strip()
    if not text:
        return None, ""
    currency = "EUR" if "€" in text or "eur" in text.lower() else ""
    match = re.search(r"(\d+(?:[\s\u00a0]?\d{3})*(?:[,.]\d+)?)", text)
    if not match:
        return None, currency
    amount_text = match.group(1).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return round(float(amount_text), 2), currency
    except ValueError:
        return None, currency


def backfill_seen_item_prices(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "select id, price from seen_items where price_amount is null and coalesce(price, '') <> ''"
    ).fetchall()
    for row in rows:
        amount, currency = parse_price_value(row["price"])
        if amount is None:
            continue
        conn.execute(
            "update seen_items set price_amount = ?, currency = coalesce(nullif(currency, ''), ?) where id = ?",
            (amount, currency or "EUR", row["id"]),
        )


def get_setting(user_id: int, key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute(
            "select value from settings where user_id = ? and key = ?",
            (user_id, key),
        ).fetchone()
    return row["value"] if row else default


def set_setting(user_id: int, key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "insert into settings(user_id, key, value) values(?, ?, ?) "
            "on conflict(user_id, key) do update set value = excluded.value",
            (user_id, key, value),
        )


def delete_settings(user_id: int, keys: tuple[str, ...]) -> None:
    if not keys:
        return
    placeholders = ",".join("?" for _ in keys)
    with db() as conn:
        conn.execute(
            f"delete from settings where user_id = ? and key in ({placeholders})",
            (user_id, *keys),
        )


def normalize_random_interval_percent(value: object) -> int:
    try:
        percent = int(value)
    except (TypeError, ValueError):
        percent = DEFAULT_RANDOM_INTERVAL_PERCENT
    percent = min(max(percent, 0), MAX_RANDOM_INTERVAL_PERCENT)
    return round(percent / RANDOM_INTERVAL_PERCENT_STEP) * RANDOM_INTERVAL_PERCENT_STEP


def get_random_interval_percent(user_id: int) -> int:
    return normalize_random_interval_percent(
        get_setting(user_id, "random_interval_percent", str(DEFAULT_RANDOM_INTERVAL_PERCENT))
    )


def list_searches(user_id: int | None = None) -> list[dict]:
    with db() as conn:
        params: tuple = ()
        where = ""
        if user_id is not None:
            where = "where user_id = ?"
            params = (user_id,)
        rows = conn.execute(
            f"""
            select id, user_id, name, url, enabled, interval_seconds, created_at,
                   last_checked_at, last_error
            from searches
            {where}
            order by id desc
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def search_url_to_api_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    query = urllib.parse.parse_qs(parsed.query)

    # If the user pastes an API URL, keep it and ensure pagination/sorting defaults.
    if "/api/v2/catalog/items" in parsed.path:
        api_params = {k: v[-1] for k, v in query.items()}
    else:
        api_params = {}
        mapping = {
            "search_text": "search_text",
            "catalog[]": "catalog_ids",
            "catalog_ids": "catalog_ids",
            "brand_ids[]": "brand_ids",
            "brand_ids": "brand_ids",
            "size_ids[]": "size_ids",
            "size_ids": "size_ids",
            "status_ids[]": "status_ids",
            "status_ids": "status_ids",
            "color_ids[]": "color_ids",
            "color_ids": "color_ids",
            "price_to": "price_to",
            "price_from": "price_from",
            "currency": "currency",
            "order": "order",
        }
        for source, target in mapping.items():
            if source in query:
                api_params[target] = ",".join(query[source])

    api_params.setdefault("per_page", "24")
    api_params.setdefault("page", "1")
    api_params.setdefault("order", "newest_first")
    encoded = urllib.parse.urlencode(api_params)
    return f"https://www.vinted.fr/api/v2/catalog/items?{encoded}"


def is_allowed_vinted_search_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url.strip())
    hostname = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and (hostname == "vinted.fr" or hostname.endswith(".vinted.fr"))
        and parsed.path.startswith(("/catalog", "/api/v2/catalog/items"))
    )


def reset_vinted_session() -> None:
    global vinted_cookie_jar, vinted_opener

    vinted_cookie_jar = http.cookiejar.CookieJar()
    vinted_opener = build_vinted_opener()


def warm_vinted_session() -> None:
    request = urllib.request.Request(
        "https://www.vinted.fr/",
        headers={**VINTED_HEADERS, "Accept": "text/html,application/xhtml+xml"},
    )
    try:
        with vinted_opener.open(request, timeout=20) as response:
            response.read(1024)
    except OSError as exc:
        raise RuntimeError(network_error_message(exc)) from exc


def http_json(url: str, headers: dict[str, str], opener=None) -> dict:
    request = urllib.request.Request(url, headers=headers)
    active_opener = opener or urllib.request
    try:
        response_context = active_opener.open(request, timeout=20)
    except urllib.error.HTTPError as exc:
        detail = read_http_error(exc)
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connexion impossible: {exc.reason}") from exc
    except OSError as exc:
        raise RuntimeError(network_error_message(exc)) from exc

    with response_context as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def fetch_vinted_items(search_url: str) -> list[dict]:
    api_url = search_url_to_api_url(search_url)
    data = fetch_vinted_json(api_url)
    return normalize_vinted_items(data)


def normalize_vinted_items(data: dict) -> list[dict]:
    items = data.get("items", [])
    normalized = []

    for item in items:
        item_id = str(item.get("id") or "")
        title = item.get("title") or item.get("description") or "Article Vinted"
        price = item.get("price")
        if isinstance(price, dict):
            price_text = price.get("amount") or price.get("value") or ""
            currency = price.get("currency_code") or price.get("currency") or "EUR"
            price = f"{price_text} {currency}".strip()
        elif price is None:
            price = ""
        else:
            price = str(price)
        price_amount, currency = parse_price_value(price)
        if not currency and price_amount is not None:
            currency = "EUR"

        photo_url = ""
        photo = item.get("photo")
        if isinstance(photo, dict):
            photo_url = photo.get("url") or photo.get("full_size_url") or ""

        url = item.get("url") or f"https://www.vinted.fr/items/{item_id}"
        if item_id:
            normalized.append(
                {
                    "id": item_id,
                    "title": title,
                    "price": price,
                    "price_amount": price_amount,
                    "currency": currency,
                    "url": url,
                    "photo_url": photo_url,
                }
            )

    return normalized


def fetch_vinted_json(api_url: str) -> dict:
    if FETCH_API_ENABLED:
        return fetch_vinted_json_from_api(api_url)
    return fetch_vinted_json_direct(api_url)


def fetch_vinted_json_from_api(api_url: str) -> dict:
    if not FETCH_API_URL:
        raise RuntimeError("VINTED_ALERTS_FETCH_API_URL est obligatoire quand l'API de fetch est active.")
    if not FETCH_API_TOKEN:
        raise RuntimeError(
            "VINTED_ALERTS_FETCH_API_TOKEN est obligatoire quand l'API de fetch est active "
            "(ou VINTED_FETCH_API_TOKEN comme alias)."
        )

    payload = json.dumps({"url": api_url}).encode("utf-8")
    request = urllib.request.Request(
        f"{FETCH_API_URL}/api/vinted/json",
        data=payload,
        headers={
            "Authorization": f"Bearer {FETCH_API_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                data = json.loads(response.read().decode(charset))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "Reponse invalide du proxy de fetch. "
                    "Verifie que l'URL proxy pointe bien vers l'API de fetch."
                ) from exc
    except urllib.error.HTTPError as exc:
        detail = read_http_error(exc)
        raise RuntimeError(f"Proxy de fetch HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(fetch_api_unavailable_message(exc.reason)) from exc
    except OSError as exc:
        raise RuntimeError(fetch_api_unavailable_message(exc)) from exc

    if not data.get("ok"):
        raise RuntimeError(
            f"Proxy de fetch en erreur: {data.get('error') or 'Erreur inconnue.'}"
        )
    result = data.get("data")
    if not isinstance(result, dict):
        raise RuntimeError("Reponse invalide du proxy de fetch.")
    return result


def fetch_api_unavailable_message(reason: object) -> str:
    detail = str(reason).strip()
    message = (
        "Proxy de fetch inaccessible. "
        "Verifie que l'URL proxy est bien demarree et repond."
    )
    return f"{message} Detail: {detail}" if detail else message


def fetch_vinted_json_direct(api_url: str) -> dict:
    with vinted_lock:
        if not any(True for _ in vinted_cookie_jar):
            warm_vinted_session()

        try:
            return http_json(api_url, VINTED_HEADERS, opener=vinted_opener)
        except RuntimeError as exc:
            if "HTTP 401" not in str(exc):
                raise

            reset_vinted_session()
            warm_vinted_session()
            try:
                return http_json(api_url, VINTED_HEADERS, opener=vinted_opener)
            except RuntimeError as retry_exc:
                if "HTTP 401" in str(retry_exc):
                    raise RuntimeError(
                        "Vinted refuse la session locale (401). "
                        "Supprime puis recrée la recherche avec une URL de page Vinted normale, "
                        "ou réessaie après avoir ouvert vinted.fr dans ton navigateur."
                    ) from retry_exc
                raise


def read_http_error(exc: urllib.error.HTTPError) -> str:
    charset = exc.headers.get_content_charset() or "utf-8"
    raw = exc.read().decode(charset, errors="replace").strip()
    content_type = exc.headers.get("Content-Type", "").lower()
    if not raw:
        return http_status_message(exc.code, exc.reason)
    if "html" in content_type or raw.lower().startswith(("<!doctype html", "<html")):
        return http_status_message(exc.code, exc.reason)
    try:
        data = json.loads(raw)
        detail = data.get("message") or data.get("error")
        return str(detail) if detail else http_status_message(exc.code, exc.reason)
    except json.JSONDecodeError:
        if "<html" in raw.lower() or "</html>" in raw.lower():
            return http_status_message(exc.code, exc.reason)
        return raw[:500]


def http_status_message(status: int, reason: str | None = None) -> str:
    if status == 403:
        return "Acces refuse par Vinted (403). Vinted bloque probablement la requete depuis cette IP ou cette session."
    if status == 429:
        return "Trop de requetes vers Vinted (429). Patiente quelques minutes puis reessaie."
    if status == 401:
        return "Session Vinted refusee (401). Reessaie plus tard ou renouvelle la session locale."
    label = reason or "Erreur HTTP"
    return f"{label} ({status})"


def network_error_message(exc: OSError) -> str:
    detail = str(exc)
    if "aswMonFltProxy" in detail:
        return (
            "Acces reseau bloque par le filtre Web Avast/AVG. "
            "Ajoute python.exe aux applications autorisees, desactive l'analyse HTTPS/Web Shield "
            "pour ce test, ou active l'API de fetch distante."
        )
    return f"Erreur reseau locale: {detail}"


def telegram_request(user_id: int, method: str, payload: dict) -> dict:
    token = get_setting(user_id, "telegram_bot_token")
    if not token:
        raise RuntimeError("Token Telegram manquant.")

    url = f"https://api.telegram.org/bot{token}/{method}"
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset))
    except urllib.error.HTTPError as exc:
        charset = exc.headers.get_content_charset() or "utf-8"
        raw = exc.read().decode(charset, errors="replace")
        try:
            data = json.loads(raw)
            description = data.get("description") or raw
        except json.JSONDecodeError:
            description = raw
        raise RuntimeError(f"Erreur Telegram ({exc.code}): {description}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Impossible de contacter Telegram: {exc.reason}") from exc


def send_telegram_message(user_id: int, text: str) -> None:
    chat_id = get_setting(user_id, "telegram_chat_id")
    if not chat_id:
        raise RuntimeError("Chat ID Telegram manquant.")
    result = telegram_request(
        user_id,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        },
    )
    if not result.get("ok"):
        raise RuntimeError(f"Erreur Telegram: {result}")


def notify_item(user_id: int, search_name: str, item: dict) -> None:
    price = f"\nPrix: {item['price']}" if item.get("price") else ""
    text = (
        "Nouvel article Vinted\n\n"
        f"<b>{html_escape(item['title'])}</b>"
        f"{html_escape(price)}\n"
        f"Recherche: {html_escape(search_name)}\n"
        f"{html_escape(item['url'])}"
    )
    send_telegram_message(user_id, text)


def html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def check_search(search: sqlite3.Row, notify: bool = True) -> int:
    items = fetch_vinted_items(search["url"])
    new_count = 0
    search_id = int(search["id"])
    user_id = int(search["user_id"])

    with state_lock:
        is_initial_scan = search_id not in initialized_search_ids

    with db() as conn:
        seen_count = conn.execute(
            "select count(*) as count from seen_items where search_id = ?",
            (search_id,),
        ).fetchone()["count"]
        if is_initial_scan or seen_count == 0:
            for item in items:
                conn.execute(
                    """
                    insert or ignore into seen_items(
                        item_id, search_id, title, price, price_amount, currency, url, photo_url,
                        created_at, notified_at
                    ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, null)
                    """,
                    (
                        item["id"],
                        search_id,
                        item["title"],
                        item["price"],
                        item.get("price_amount"),
                        item.get("currency"),
                        item["url"],
                        item["photo_url"],
                        now_iso(),
                    ),
                )
            conn.execute(
                """
                update searches
                set last_checked_at = ?, last_error = null
                where id = ?
                """,
                (now_iso(), search_id),
            )
            with state_lock:
                initialized_search_ids.add(search_id)
            return 0

        for item in reversed(items):
            exists = conn.execute(
                "select 1 from seen_items where search_id = ? and item_id = ?",
                (search_id, item["id"]),
            ).fetchone()
            if exists:
                continue

            notified_at = None
            if notify:
                notify_item(user_id, search["name"], item)
                notified_at = now_iso()

            conn.execute(
                """
                insert into seen_items(
                    item_id, search_id, title, price, price_amount, currency, url, photo_url,
                    created_at, notified_at
                ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    search_id,
                    item["title"],
                    item["price"],
                    item.get("price_amount"),
                    item.get("currency"),
                    item["url"],
                    item["photo_url"],
                    now_iso(),
                    notified_at,
                ),
            )
            new_count += 1

        conn.execute(
            """
            update searches
            set last_checked_at = ?, last_error = null
            where id = ?
            """,
            (now_iso(), search_id),
        )

    with state_lock:
        initialized_search_ids.add(search_id)
    return new_count


def run_checks_once(
    notify: bool = True,
    user_id: int | None = None,
    raise_on_error: bool = False,
    search_ids: set[int] | None = None,
) -> int:
    global last_check_started_at, last_check_finished_at, last_error

    with check_lock:
        with state_lock:
            last_check_started_at = now_iso()
            last_error = None

        total = 0
        errors: list[Exception] = []
        try:
            with db() as conn:
                params: tuple = ()
                user_filter = ""
                if user_id is not None:
                    user_filter = "and user_id = ?"
                    params = (user_id,)
                rows = conn.execute(
                    f"select * from searches where enabled = 1 {user_filter} order by id asc",
                    params,
                ).fetchall()

            for search in rows:
                search_id = int(search["id"])
                if search_ids is not None and search_id not in search_ids:
                    continue
                try:
                    total += check_search(search, notify=notify)
                except Exception as exc:
                    errors.append(exc)
                    with db() as conn:
                        conn.execute(
                            "update searches set last_error = ? where id = ?",
                            (str(exc), search_id),
                        )
                    print(f"[{now_iso()}] Recherche {search_id} en erreur: {exc}")
                finally:
                    schedule_next_check(search)
        except Exception as exc:
            errors.append(exc)
            print(f"[{now_iso()}] Verification impossible: {exc}")

        with state_lock:
            last_error = " | ".join(str(error) for error in errors) or None
            last_check_finished_at = now_iso()

        if errors and raise_on_error:
            raise errors[0]
        return total


def schedule_next_check(search: sqlite3.Row | dict, from_time: float | None = None) -> float:
    interval = max(int(search["interval_seconds"]), 60)
    jitter_percent = get_random_interval_percent(int(search["user_id"]))
    jitter = random.randint(0, max(0, round(interval * jitter_percent / 100)))
    due_at = (from_time if from_time is not None else time.time()) + interval + jitter
    with state_lock:
        next_check_at[int(search["id"])] = due_at
    return due_at


def schedule_search_by_id(search_id: int) -> None:
    with db() as conn:
        search = conn.execute("select * from searches where id = ?", (search_id,)).fetchone()
    if search and search["enabled"]:
        schedule_next_check(search)
    worker_wakeup.set()


def worker_loop() -> None:
    while not worker_stop.is_set():
        searches = list_searches()
        enabled_searches = [search for search in searches if search["enabled"]]
        enabled_ids = {int(search["id"]) for search in enabled_searches}
        now = time.time()
        with state_lock:
            for search_id in list(next_check_at):
                if search_id not in enabled_ids:
                    next_check_at.pop(search_id, None)
            due_ids = {
                search_id
                for search_id in enabled_ids
                if next_check_at.get(search_id, 0) <= now
            }

        if due_ids:
            run_checks_once(notify=True, search_ids=due_ids)
            continue

        with state_lock:
            upcoming = [next_check_at[search_id] for search_id in enabled_ids if search_id in next_check_at]
        wait_seconds = min(max(1.0, min(upcoming) - now), 60.0) if upcoming else 60.0
        worker_wakeup.wait(wait_seconds)
        worker_wakeup.clear()


def start_worker() -> None:
    global worker_thread
    if worker_thread and worker_thread.is_alive():
        return
    worker_thread = threading.Thread(target=worker_loop, daemon=True)
    worker_thread.start()


class RequestTooLargeError(RuntimeError):
    pass


class AuthenticationError(RuntimeError):
    pass


class AuthorizationError(RuntimeError):
    pass


def login_is_rate_limited(client_ip: str) -> bool:
    cutoff = time.monotonic() - LOGIN_ATTEMPT_WINDOW_SECONDS
    with login_attempts_lock:
        recent = [attempt for attempt in login_attempts.get(client_ip, []) if attempt >= cutoff]
        if recent:
            login_attempts[client_ip] = recent
        else:
            login_attempts.pop(client_ip, None)
        return len(recent) >= LOGIN_ATTEMPT_LIMIT


def record_login_failure(client_ip: str) -> None:
    cutoff = time.monotonic() - LOGIN_ATTEMPT_WINDOW_SECONDS
    with login_attempts_lock:
        recent = [attempt for attempt in login_attempts.get(client_ip, []) if attempt >= cutoff]
        recent.append(time.monotonic())
        login_attempts[client_ip] = recent


def clear_login_failures(client_ip: str) -> None:
    with login_attempts_lock:
        login_attempts.pop(client_ip, None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[{now_iso()}] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/":
                self.send_file(ROOT / "web" / "index.html", "text/html; charset=utf-8")
            elif parsed.path == "/app.js":
                self.send_file(ROOT / "web" / "app.js", "application/javascript; charset=utf-8")
            elif parsed.path == "/styles.css":
                self.send_file(ROOT / "web" / "styles.css", "text/css; charset=utf-8")
            elif parsed.path == "/manifest.webmanifest":
                self.send_file(ROOT / "web" / "manifest.webmanifest", "application/manifest+json; charset=utf-8")
            elif parsed.path == "/service-worker.js":
                self.send_file(ROOT / "web" / "service-worker.js", "application/javascript; charset=utf-8")
            elif parsed.path in {"/icon.svg", "/favicon.svg"}:
                self.send_file(ROOT / "web" / "icon.svg", "image/svg+xml; charset=utf-8")
            elif parsed.path == "/icon-192.png":
                self.send_file(ROOT / "web" / "icon-192.png", "image/png")
            elif parsed.path == "/icon-512.png":
                self.send_file(ROOT / "web" / "icon-512.png", "image/png")
            elif parsed.path == "/api/state":
                user = self.require_user()
                self.send_json(api_state(user))
            elif parsed.path == "/api/telegram/updates":
                user = self.require_user()
                self.send_json(get_telegram_updates(user["id"]))
            elif parsed.path == "/api/items":
                user = self.require_user()
                query = urllib.parse.parse_qs(parsed.query)
                page = query_int(query, "page", 1)
                page_size = query_int(query, "page_size", 12)
                self.send_json(recent_items(user["id"], page=page, page_size=page_size))
            elif parsed.path == "/api/dashboard-items":
                user = self.require_user()
                query = urllib.parse.parse_qs(parsed.query)
                limit = query_int(query, "limit", 10)
                self.send_json(dashboard_items(user["id"], limit=limit))
            elif parsed.path == "/api/price-analytics":
                user = self.require_user()
                self.send_json(price_analytics(user["id"]))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except AuthenticationError as exc:
            self.send_json({"ok": False, "authenticated": False, "error": str(exc)}, status=401)
        except AuthorizationError as exc:
            self.send_json({"ok": False, "authenticated": True, "error": str(exc)}, status=403)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path == "/api/login":
                client_ip = self.client_address[0]
                if login_is_rate_limited(client_ip):
                    self.send_json(
                        {"ok": False, "error": "Trop de tentatives. Reessaie dans quelques minutes."},
                        status=429,
                    )
                    return
                user = authenticate(
                    str(payload.get("username", "")).strip(),
                    str(payload.get("password", "")),
                )
                if not user:
                    record_login_failure(client_ip)
                    raise AuthenticationError("Identifiants invalides.")
                clear_login_failures(client_ip)
                token = create_session(user["id"])
                self.send_json(
                    {
                        "ok": True,
                        "token": token,
                        "user": {"username": user["username"], "is_admin": bool(user["is_admin"])},
                    },
                    headers={"Set-Cookie": self.session_cookie(token)},
                )
            elif parsed.path == "/api/logout":
                delete_session(self.session_token())
                self.send_json({"ok": True}, headers={"Set-Cookie": self.session_cookie("", expires=True)})
            elif parsed.path == "/api/settings":
                user = self.require_user()
                save_settings(user["id"], payload)
                self.send_json({"ok": True})
            elif parsed.path == "/api/account/password":
                user = self.require_user()
                update_account_password(user["id"], self.session_token(), payload)
                self.send_json({"ok": True})
            elif parsed.path == "/api/searches":
                user = self.require_user()
                create_search(user["id"], payload)
                self.send_json({"ok": True})
            elif parsed.path == "/api/users":
                user = self.require_user()
                if not user["is_admin"]:
                    raise AuthorizationError("Accès admin requis.")
                create_user(payload)
                self.send_json({"ok": True})
            elif parsed.path.startswith("/api/users/"):
                user = self.require_user()
                if not user["is_admin"]:
                    raise AuthorizationError("Accès admin requis.")
                self.update_user(parsed.path, payload)
            elif parsed.path.startswith("/api/searches/"):
                user = self.require_user()
                self.update_search(user, parsed.path, payload)
            elif parsed.path == "/api/check-now":
                user = self.require_user()
                count = run_checks_once(notify=True, user_id=user["id"], raise_on_error=True)
                self.send_json({"ok": True, "new_items": count})
            elif parsed.path == "/api/telegram/test":
                user = self.require_user()
                send_telegram_message(user["id"], "Test Vinted Alerts: la connexion Telegram fonctionne.")
                self.send_json({"ok": True})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except RequestTooLargeError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=413)
        except AuthenticationError as exc:
            self.send_json({"ok": False, "authenticated": False, "error": str(exc)}, status=401)
        except AuthorizationError as exc:
            self.send_json({"ok": False, "authenticated": True, "error": str(exc)}, status=403)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_security_headers()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict, status: int = 200, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_security_headers()
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RuntimeError("Content-Length invalide.") from exc
        if length < 0:
            raise RuntimeError("Content-Length invalide.")
        if length > MAX_JSON_BODY_BYTES:
            raise RequestTooLargeError(
                f"Requete trop volumineuse (maximum {MAX_JSON_BODY_BYTES} octets)."
            )
        if length == 0:
            return {}
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Le corps JSON doit etre un objet.")
        return payload

    def session_token(self) -> str:
        authorization = self.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            return token
        cookies = self.headers.get("Cookie", "")
        for part in cookies.split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return value
        return ""

    def session_cookie(self, token: str, expires: bool = False) -> str:
        bits = [
            f"{SESSION_COOKIE}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if expires:
            bits.append("Max-Age=0")
        if SECURE_COOKIE:
            bits.append("Secure")
        return "; ".join(bits)

    def send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'",
        )

    def require_user(self) -> dict:
        user = get_session_user(self.session_token())
        if not user:
            raise AuthenticationError("Connexion requise.")
        return user

    def update_search(self, user: dict, path: str, payload: dict) -> None:
        parts = path.strip("/").split("/")
        search_id = int(parts[2])
        action = parts[3] if len(parts) > 3 else ""
        if action == "toggle":
            with db() as conn:
                conn.execute(
                    "update searches set enabled = 1 - enabled where id = ? and user_id = ?",
                    (search_id, user["id"]),
                )
                row = conn.execute(
                    "select enabled from searches where id = ? and user_id = ?",
                    (search_id, user["id"]),
                ).fetchone()
            with state_lock:
                if row and row["enabled"]:
                    next_check_at[search_id] = 0
                else:
                    next_check_at.pop(search_id, None)
            worker_wakeup.set()
            self.send_json({"ok": True})
        elif action == "delete":
            with db() as conn:
                conn.execute("delete from searches where id = ? and user_id = ?", (search_id, user["id"]))
            with state_lock:
                initialized_search_ids.discard(search_id)
                next_check_at.pop(search_id, None)
            worker_wakeup.set()
            self.send_json({"ok": True})
        elif action == "save":
            update_search_settings(user["id"], search_id, payload)
            self.send_json({"ok": True})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def update_user(self, path: str, payload: dict) -> None:
        parts = path.strip("/").split("/")
        user_id = int(parts[2])
        action = parts[3] if len(parts) > 3 else ""
        if action == "password":
            update_user_password(user_id, payload)
            self.send_json({"ok": True})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)


def api_state(user: dict) -> dict:
    with state_lock:
        runtime = {
            "last_check_started_at": last_check_started_at,
            "last_check_finished_at": last_check_finished_at,
            "last_error": last_error,
            "worker_running": bool(worker_thread and worker_thread.is_alive()),
        }
    return {
        "authenticated": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "is_admin": bool(user["is_admin"]),
        },
        "settings": {
            "telegram_bot_token": mask_secret(get_setting(user["id"], "telegram_bot_token")),
            "telegram_chat_id": get_setting(user["id"], "telegram_chat_id"),
            "random_interval_percent": get_random_interval_percent(user["id"]),
        },
        "searches": list_searches(user["id"]),
        "users": list_users() if user["is_admin"] else [],
        "runtime": runtime,
    }


def query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(query.get(name, [str(default)])[0] or default)
    except (TypeError, ValueError):
        return default


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "********"
    return f"{value[:4]}...{value[-4:]}"


def list_users() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            select id, username, is_admin, created_at
            from users
            order by username asc
            """
        ).fetchall()
    return [
        {
            "id": row["id"],
            "username": row["username"],
            "is_admin": bool(row["is_admin"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def create_user(payload: dict) -> None:
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", "")).strip()
    is_admin = 1 if payload.get("is_admin") else 0
    if not username:
        raise RuntimeError("Nom d'utilisateur obligatoire.")
    if len(password) < 6:
        raise RuntimeError("Mot de passe: 6 caractères minimum.")
    with db() as conn:
        try:
            conn.execute(
                """
                insert into users(username, password_hash, is_admin, created_at)
                values(?, ?, ?, ?)
                """,
                (username, hash_password(password), is_admin, now_iso()),
            )
        except sqlite3.IntegrityError as exc:
            raise RuntimeError("Cet utilisateur existe déjà.") from exc

 
def update_user_password(user_id: int, payload: dict) -> None:
    password = str(payload.get("password", "")).strip()
    if len(password) < 6:
        raise RuntimeError("Mot de passe: 6 caractères minimum.")
    with db() as conn:
        cursor = conn.execute(
            "update users set password_hash = ? where id = ?",
            (hash_password(password), user_id),
        )
        if cursor.rowcount == 0:
            raise RuntimeError("Utilisateur introuvable.")
        conn.execute("delete from sessions where user_id = ?", (user_id,))


def update_account_password(user_id: int, session_token: str, payload: dict) -> None:
    password = str(payload.get("password", "")).strip()
    if len(password) < 6:
        raise RuntimeError("Mot de passe: 6 caractÃ¨res minimum.")
    with db() as conn:
        cursor = conn.execute(
            "update users set password_hash = ? where id = ?",
            (hash_password(password), user_id),
        )
        if cursor.rowcount == 0:
            raise RuntimeError("Utilisateur introuvable.")
        conn.execute(
            "delete from sessions where user_id = ? and token <> ?",
            (user_id, session_token),
        )


def authenticate(username: str, password: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "select id, username, password_hash, is_admin from users where username = ?",
            (username,),
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"], "is_admin": row["is_admin"]}


def save_settings(user_id: int, payload: dict) -> None:
    if payload.get("clear_telegram_settings"):
        delete_settings(user_id, ("telegram_bot_token", "telegram_chat_id"))
        return
    token = str(payload.get("telegram_bot_token", "")).strip()
    chat_id = str(payload.get("telegram_chat_id", "")).strip()
    if token:
        set_setting(user_id, "telegram_bot_token", token)
    if chat_id:
        set_setting(user_id, "telegram_chat_id", chat_id)
    if "random_interval_percent" in payload:
        random_interval_percent = normalize_random_interval_percent(
            payload.get("random_interval_percent")
        )
        set_setting(user_id, "random_interval_percent", str(random_interval_percent))


def create_search(user_id: int, payload: dict) -> None:
    name = str(payload.get("name", "")).strip()
    url = str(payload.get("url", "")).strip()
    interval = int(payload.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS)
    if not name:
        raise RuntimeError("Nom de recherche obligatoire.")
    if not is_allowed_vinted_search_url(url):
        raise RuntimeError("Colle une URL de recherche Vinted valide.")
    if interval < 60:
        raise RuntimeError("Intervalle minimum: 60 secondes.")
    with db() as conn:
        cursor = conn.execute(
            """
            insert into searches(user_id, name, url, enabled, interval_seconds, created_at)
            values(?, ?, ?, 1, ?, ?)
            """,
            (user_id, name, url, interval, now_iso()),
        )
        search_id = cursor.lastrowid

    try:
        seed_seen_items(search_id, url)
    except Exception as exc:
        with db() as conn:
            conn.execute(
                "update searches set last_error = ? where id = ?",
                (f"Recherche ajoutée, mais initialisation impossible: {exc}", search_id),
            )
    finally:
        schedule_search_by_id(int(search_id))


def update_search_settings(user_id: int, search_id: int, payload: dict) -> None:
    name = str(payload.get("name", "")).strip()
    url = str(payload.get("url", "")).strip()
    interval = int(payload.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS)
    if not name:
        raise RuntimeError("Nom de recherche obligatoire.")
    if not is_allowed_vinted_search_url(url):
        raise RuntimeError("Colle une URL de recherche Vinted valide.")
    if interval < 60:
        raise RuntimeError("Intervalle minimum: 60 secondes.")

    with db() as conn:
        row = conn.execute(
            "select url from searches where id = ? and user_id = ?",
            (search_id, user_id),
        ).fetchone()
        if not row:
            raise RuntimeError("Recherche introuvable.")
        url_changed = row["url"] != url
        conn.execute(
            """
            update searches
            set name = ?, url = ?, interval_seconds = ?, last_error = null
            where id = ?
            """,
            (name, url, interval, search_id),
        )
        if url_changed:
            conn.execute("delete from seen_items where search_id = ?", (search_id,))

    if url_changed:
        with state_lock:
            initialized_search_ids.discard(search_id)
        seed_seen_items(search_id, url)
    schedule_search_by_id(search_id)


def seed_seen_items(search_id: int, url: str) -> None:
    items = fetch_vinted_items(url)
    with db() as conn:
        for item in items:
            conn.execute(
                """
                insert or ignore into seen_items(
                    item_id, search_id, title, price, price_amount, currency, url, photo_url,
                    created_at, notified_at
                ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, null)
                """,
                (
                    item["id"],
                    search_id,
                    item["title"],
                    item["price"],
                    item.get("price_amount"),
                    item.get("currency"),
                    item["url"],
                    item["photo_url"],
                    now_iso(),
                ),
            )
        conn.execute(
            "update searches set last_checked_at = ?, last_error = null where id = ?",
            (now_iso(), search_id),
        )
    with state_lock:
        initialized_search_ids.add(int(search_id))


def get_telegram_updates(user_id: int) -> dict:
    result = telegram_request(user_id, "getUpdates", {})
    if not result.get("ok"):
        raise RuntimeError(f"Erreur Telegram: {result}")
    updates = []
    for update in result.get("result", []):
        message = (
            update.get("message")
            or update.get("channel_post")
            or update.get("my_chat_member")
            or update.get("chat_member")
            or {}
        )
        if "chat" not in message and "from" in message:
            message = {"chat": message.get("from"), "text": ""}
        chat = message.get("chat") or {}
        text = message.get("text") or ""
        if chat.get("id"):
            updates.append(
                {
                    "chat_id": str(chat["id"]),
                    "chat_title": chat.get("title") or chat.get("username") or chat.get("first_name") or "",
                    "text": text,
                }
            )
    return {"ok": True, "updates": updates}


def recent_items(user_id: int, page: int = 1, page_size: int = 12) -> dict:
    page_size = max(1, min(page_size, 50))
    with db() as conn:
        total = conn.execute(
            """
            select count(*)
            from seen_items i
            join searches s on s.id = i.search_id
            where s.user_id = ?
            """,
            (user_id,),
        ).fetchone()[0]
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size
        rows = conn.execute(
            """
            select s.name as search_name, i.title, i.price, i.url, i.photo_url,
                   i.created_at, i.notified_at
            from seen_items i
            join searches s on s.id = i.search_id
            where s.user_id = ?
            order by i.created_at desc
            limit ? offset ?
            """,
            (user_id, page_size, offset),
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


def dashboard_items(user_id: int, limit: int = 10) -> dict:
    limit = max(1, min(limit, 20))
    with db() as conn:
        rows = conn.execute(
            """
            select search_id, search_name, title, price, url, photo_url, created_at, notified_at
            from (
                select i.search_id, s.name as search_name, i.title, i.price, i.url,
                       i.photo_url, i.created_at, i.notified_at,
                       row_number() over (
                           partition by i.search_id
                           order by i.created_at desc, i.id desc
                       ) as item_rank
                from seen_items i
                join searches s on s.id = i.search_id
                where s.user_id = ?
            )
            where item_rank <= ?
            order by search_name collate nocase asc, created_at desc
            """,
            (user_id, limit),
        ).fetchall()
    return {"items": [dict(row) for row in rows], "limit": limit}


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def rounded(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def price_position(price: float | None, reference: float | None) -> dict:
    if price is None or not reference:
        return {"status": "unknown", "label": "Prix non comparable", "delta_percent": None}
    delta = ((price - reference) / reference) * 100
    if delta <= -20:
        status, label = "deal", "Tres bonne affaire"
    elif delta <= -10:
        status, label = "good", "Moins cher que la normale"
    elif delta >= 20:
        status, label = "expensive", "Plus cher que la normale"
    elif delta >= 10:
        status, label = "high", "Un peu au-dessus"
    else:
        status, label = "normal", "Prix normal"
    return {"status": status, "label": label, "delta_percent": rounded(delta)}


def trend_label(first: float | None, latest: float | None) -> dict:
    if not first or latest is None:
        return {"direction": "flat", "label": "Pas assez de donnees", "delta_percent": None}
    delta = ((latest - first) / first) * 100
    if delta <= -8:
        direction, label = "down", "Prix en baisse"
    elif delta >= 8:
        direction, label = "up", "Prix en hausse"
    else:
        direction, label = "flat", "Prix stable"
    return {"direction": direction, "label": label, "delta_percent": rounded(delta)}


def price_analytics(user_id: int) -> dict:
    with db() as conn:
        rows = conn.execute(
            """
            select i.search_id, s.name as search_name, i.title, i.price, i.price_amount,
                   coalesce(i.currency, 'EUR') as currency, i.url, i.photo_url, i.created_at
            from seen_items i
            join searches s on s.id = i.search_id
            where s.user_id = ?
            order by i.created_at asc, i.id asc
            """,
            (user_id,),
        ).fetchall()

    grouped: dict[int, list[dict]] = {}
    for row in rows:
        item = dict(row)
        amount = item.get("price_amount")
        if amount is None:
            amount, currency = parse_price_value(item.get("price"))
            item["price_amount"] = amount
            item["currency"] = item.get("currency") or currency or "EUR"
        if item["price_amount"] is None:
            continue
        item["price_amount"] = float(item["price_amount"])
        grouped.setdefault(int(item["search_id"]), []).append(item)

    searches = []
    best_deals = []
    for search_id, items in grouped.items():
        prices = [item["price_amount"] for item in items]
        reference = median(prices)
        first_window = prices[: max(1, len(prices) // 3)]
        latest_window = prices[-max(1, len(prices) // 3):]
        daily: dict[str, list[float]] = {}
        for item in items:
            day = str(item["created_at"] or "")[:10] or "Inconnu"
            daily.setdefault(day, []).append(item["price_amount"])

        history = [
            {
                "date": day,
                "count": len(day_prices),
                "average": rounded(sum(day_prices) / len(day_prices)),
                "median": rounded(median(day_prices)),
                "minimum": rounded(min(day_prices)),
                "maximum": rounded(max(day_prices)),
            }
            for day, day_prices in sorted(daily.items())
        ]

        assessed_items = []
        for item in reversed(items[-8:]):
            position = price_position(item["price_amount"], reference)
            assessed = {
                "title": item["title"],
                "price": item["price"],
                "price_amount": rounded(item["price_amount"]),
                "currency": item["currency"] or "EUR",
                "url": item["url"],
                "photo_url": item["photo_url"],
                "created_at": item["created_at"],
                "position": position,
            }
            assessed_items.append(assessed)
            if position["status"] in {"deal", "good"}:
                best_deals.append({**assessed, "search_id": search_id, "search_name": item["search_name"]})

        searches.append(
            {
                "search_id": search_id,
                "search_name": items[0]["search_name"],
                "currency": items[-1].get("currency") or "EUR",
                "count": len(items),
                "average": rounded(sum(prices) / len(prices)),
                "median": rounded(reference),
                "minimum": rounded(min(prices)),
                "maximum": rounded(max(prices)),
                "trend": trend_label(median(first_window), median(latest_window)),
                "history": history[-14:],
                "latest_items": assessed_items,
            }
        )

    searches.sort(key=lambda search: search["search_name"].lower())
    best_deals.sort(key=lambda item: item["position"]["delta_percent"] or 0)
    return {"searches": searches, "best_deals": best_deals[:10]}


def validate_configuration() -> None:
    is_loopback = HOST.lower() in {"127.0.0.1", "localhost", "::1"}
    if not is_loopback and ADMIN_PASSWORD == "admin123":
        raise SystemExit(
            "Refus de demarrer sur une adresse publique avec le mot de passe admin par defaut. "
            "Definis VINTED_ALERTS_ADMIN_PASSWORD avec un mot de passe fort."
        )
    if not is_loopback and not SECURE_COOKIE:
        print(
            "AVERTISSEMENT: active VINTED_ALERTS_SECURE_COOKIE=true lorsque l'application "
            "est servie en HTTPS."
        )


def main() -> None:
    validate_configuration()
    init_db()
    start_worker()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Vinted Alerts lancé: http://{HOST}:{PORT}")
    print("Garde cette fenêtre ouverte pour continuer les vérifications.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Arrêt...")
    finally:
        worker_stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
