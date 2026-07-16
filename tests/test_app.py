import gc
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import app


class QuietHandler(app.Handler):
    def log_message(self, fmt: str, *args) -> None:
        pass


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.temp_dir.name) / "test.db"
        app.ADMIN_USERNAME = "admin"
        app.ADMIN_PASSWORD = "test-password"
        app.ADMIN_PASSWORD_ENV = None
        app.SESSION_TTL_SECONDS = 3600
        app.initialized_search_ids.clear()
        app.next_check_at.clear()
        app.login_attempts.clear()
        app.last_error = None
        app.init_db()

    def tearDown(self) -> None:
        gc.collect()
        self.temp_dir.cleanup()

    def admin_id(self) -> int:
        with app.db() as conn:
            return int(conn.execute("select id from users where username = 'admin'").fetchone()[0])

    def user_id(self, username: str) -> int:
        with app.db() as conn:
            return int(conn.execute("select id from users where username = ?", (username,)).fetchone()[0])

    def add_search(self, name: str) -> int:
        with app.db() as conn:
            cursor = conn.execute(
                """
                insert into searches(user_id, name, url, enabled, interval_seconds, created_at)
                values(?, ?, ?, 1, 180, ?)
                """,
                (self.admin_id(), name, "https://www.vinted.fr/catalog?search_text=test", app.now_iso()),
            )
            return int(cursor.lastrowid)

    def test_foreign_keys_are_enabled_and_cascade(self) -> None:
        search_id = self.add_search("cascade")
        with app.db() as conn:
            self.assertEqual(conn.execute("pragma foreign_keys").fetchone()[0], 1)
            conn.execute(
                """
                insert into seen_items(item_id, search_id, title, url, created_at)
                values('item-1', ?, 'Article', 'https://www.vinted.fr/items/1', ?)
                """,
                (search_id, app.now_iso()),
            )
            conn.execute("delete from searches where id = ?", (search_id,))
            self.assertEqual(conn.execute("select count(*) from seen_items").fetchone()[0], 0)

    def test_existing_user_schema_is_migrated(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        with sqlite3.connect(legacy_path) as conn:
            conn.execute(
                """
                create table users (
                    id integer primary key autoincrement,
                    username text not null unique,
                    password_hash text not null,
                    is_admin integer not null default 0,
                    created_at text not null
                )
                """
            )
            conn.execute(
                "insert into users(username, password_hash, is_admin, created_at) values('admin', ?, 1, ?)",
                (app.hash_password("test-password"), app.now_iso()),
            )
        app.DB_PATH = legacy_path
        app.init_db()
        with app.db() as conn:
            columns = set(app.table_columns(conn, "users"))
            row = conn.execute("select is_active, last_login_at from users where username = 'admin'").fetchone()
        self.assertIn("is_active", columns)
        self.assertIn("last_login_at", columns)
        self.assertEqual(row["is_active"], 1)

    def test_expired_session_is_deleted(self) -> None:
        token = app.create_session(self.admin_id())
        with app.db() as conn:
            conn.execute("update sessions set created_at = '2000-01-01 00:00:00' where token = ?", (token,))
        self.assertIsNone(app.get_session_user(token))
        with app.db() as conn:
            self.assertEqual(conn.execute("select count(*) from sessions where token = ?", (token,)).fetchone()[0], 0)

    def test_vinted_url_validation(self) -> None:
        self.assertTrue(app.is_allowed_vinted_search_url("https://www.vinted.fr/catalog?search_text=nike"))
        self.assertTrue(app.is_allowed_vinted_search_url("https://www.vinted.fr/api/v2/catalog/items?page=1"))
        self.assertFalse(app.is_allowed_vinted_search_url("http://www.vinted.fr/catalog"))
        self.assertFalse(app.is_allowed_vinted_search_url("https://vinted.fr.example.com/catalog"))

    def test_check_failure_does_not_skip_other_searches(self) -> None:
        first_id = self.add_search("first")
        second_id = self.add_search("second")

        def fake_check(search, notify=True):
            if int(search["id"]) == first_id:
                raise RuntimeError("first failed")
            return 2

        with mock.patch.object(app, "check_search", side_effect=fake_check) as check_mock:
            with mock.patch.object(app, "schedule_next_check"):
                with mock.patch("builtins.print"):
                    total = app.run_checks_once(notify=False)

        self.assertEqual(total, 2)
        self.assertEqual(check_mock.call_count, 2)
        self.assertIn("first failed", app.last_error or "")
        self.assertNotEqual(first_id, second_id)

    def test_search_filter_only_runs_due_ids(self) -> None:
        first_id = self.add_search("first")
        second_id = self.add_search("second")
        checked_ids = []

        def fake_check(search, notify=True):
            checked_ids.append(int(search["id"]))
            return 0

        with mock.patch.object(app, "check_search", side_effect=fake_check):
            with mock.patch.object(app, "schedule_next_check"):
                app.run_checks_once(notify=False, search_ids={second_id})

        self.assertEqual(checked_ids, [second_id])
        self.assertNotIn(first_id, checked_ids)

    def test_disabled_search_can_be_run_manually_without_rescheduling(self) -> None:
        search_id = self.add_search("manual paused")
        with app.db() as conn:
            conn.execute("update searches set enabled = 0 where id = ?", (search_id,))

        with mock.patch.object(app, "check_search", return_value=3) as check_mock:
            with mock.patch.object(app, "schedule_next_check") as schedule_mock:
                count = app.run_search_now(self.admin_id(), search_id)

        self.assertEqual(count, 3)
        self.assertEqual(check_mock.call_count, 1)
        self.assertFalse(bool(check_mock.call_args.args[0]["enabled"]))
        schedule_mock.assert_not_called()
        self.assertNotIn(search_id, app.next_check_at)

    def test_manual_search_run_is_limited_to_its_owner(self) -> None:
        search_id = self.add_search("private")
        app.create_user({"username": "eve", "password": "secret12"})
        with self.assertRaisesRegex(RuntimeError, "introuvable"):
            app.run_search_now(self.user_id("eve"), search_id)

    def test_each_search_uses_its_own_interval(self) -> None:
        search = {
            "id": 42,
            "user_id": self.admin_id(),
            "interval_seconds": 600,
        }
        with mock.patch.object(app, "get_random_interval_percent", return_value=0):
            due_at = app.schedule_next_check(search, from_time=1000)
        self.assertEqual(due_at, 1600)
        self.assertEqual(app.next_check_at[42], 1600)

    def test_telegram_settings_can_be_cleared(self) -> None:
        user_id = self.admin_id()
        app.set_setting(user_id, "telegram_bot_token", "secret")
        app.set_setting(user_id, "telegram_chat_id", "123")
        app.save_settings(user_id, {"clear_telegram_settings": True})
        self.assertEqual(app.get_setting(user_id, "telegram_bot_token"), "")
        self.assertEqual(app.get_setting(user_id, "telegram_chat_id"), "")

    def test_user_profile_can_be_managed(self) -> None:
        app.create_user({"username": "alice", "password": "secret12"})
        alice_id = self.user_id("alice")
        app.update_user_profile(
            self.admin_id(),
            alice_id,
            {"username": "alice-renamed", "is_admin": True, "is_active": True},
        )
        managed = next(user for user in app.list_users() if user["id"] == alice_id)
        self.assertEqual(managed["username"], "alice-renamed")
        self.assertTrue(managed["is_admin"])
        self.assertTrue(managed["is_active"])
        self.assertEqual(managed["search_count"], 0)
        self.assertEqual(managed["session_count"], 0)
        with self.assertRaisesRegex(RuntimeError, "existe déjà"):
            app.create_user({"username": "ALICE-RENAMED", "password": "secret12"})

    def test_deactivating_user_revokes_sessions_and_pauses_checks(self) -> None:
        app.create_user({"username": "bob", "password": "secret12"})
        bob_id = self.user_id("bob")
        self.assertIsNotNone(app.authenticate("bob", "secret12"))
        token = app.create_session(bob_id)
        with app.db() as conn:
            conn.execute(
                """
                insert into searches(user_id, name, url, enabled, interval_seconds, created_at)
                values(?, 'bob search', 'https://www.vinted.fr/catalog?search_text=test', 1, 180, ?)
                """,
                (bob_id, app.now_iso()),
            )
        app.update_user_profile(
            self.admin_id(),
            bob_id,
            {"username": "bob", "is_active": False, "is_admin": False},
        )
        self.assertIsNone(app.get_session_user(token))
        self.assertIsNone(app.authenticate("bob", "secret12"))
        self.assertFalse(any(search["user_id"] == bob_id for search in app.list_searches()))

    def test_admin_can_revoke_sessions_and_reset_password(self) -> None:
        app.create_user({"username": "carol", "password": "secret12"})
        carol_id = self.user_id("carol")
        first_token = app.create_session(carol_id)
        app.revoke_user_sessions(self.admin_id(), carol_id)
        self.assertIsNone(app.get_session_user(first_token))
        second_token = app.create_session(carol_id)
        app.update_user_password(self.admin_id(), carol_id, {"password": "new-secret"})
        self.assertIsNone(app.get_session_user(second_token))
        self.assertIsNotNone(app.authenticate("carol", "new-secret"))

    def test_user_delete_cleans_related_data(self) -> None:
        app.create_user({"username": "david", "password": "secret12"})
        david_id = self.user_id("david")
        app.set_setting(david_id, "telegram_chat_id", "123")
        app.create_session(david_id)
        with app.db() as conn:
            cursor = conn.execute(
                """
                insert into searches(user_id, name, url, enabled, interval_seconds, created_at)
                values(?, 'david search', 'https://www.vinted.fr/catalog?search_text=test', 1, 180, ?)
                """,
                (david_id, app.now_iso()),
            )
            search_id = int(cursor.lastrowid)
            conn.execute(
                """
                insert into seen_items(item_id, search_id, title, url, created_at)
                values('david-item', ?, 'Article', 'https://www.vinted.fr/items/2', ?)
                """,
                (search_id, app.now_iso()),
            )
        app.delete_user(self.admin_id(), david_id)
        with app.db() as conn:
            self.assertEqual(conn.execute("select count(*) from users where id = ?", (david_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from searches where user_id = ?", (david_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from seen_items where search_id = ?", (search_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from settings where user_id = ?", (david_id,)).fetchone()[0], 0)

    def test_last_admin_and_current_account_are_protected(self) -> None:
        admin_id = self.admin_id()
        with mock.patch.object(app, "ADMIN_USERNAME", "different-system-admin"):
            with self.assertRaisesRegex(RuntimeError, "dernier administrateur"):
                app.update_user_profile(
                    999,
                    admin_id,
                    {"username": "admin", "is_admin": False, "is_active": True},
                )
        with self.assertRaisesRegex(RuntimeError, "administrateur système"):
            app.delete_user(999, admin_id)
        with self.assertRaisesRegex(RuntimeError, "propre compte"):
            app.delete_user(admin_id, admin_id)
        with self.assertRaisesRegex(RuntimeError, "Mon compte"):
            app.update_user_password(admin_id, admin_id, {"password": "new-secret"})

    def test_public_host_rejects_default_password(self) -> None:
        with mock.patch.object(app, "HOST", "0.0.0.0"):
            with mock.patch.object(app, "ADMIN_PASSWORD", "admin123"):
                with self.assertRaises(SystemExit):
                    app.validate_configuration()

    def test_http_login_cookie_security_headers_and_body_limit(self) -> None:
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            login_body = json.dumps({"username": "admin", "password": "test-password"}).encode()
            login_request = urllib.request.Request(
                base_url + "/api/login",
                data=login_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(login_request, timeout=3) as response:
                login = json.load(response)
                cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

            self.assertTrue(login["ok"])
            state_request = urllib.request.Request(base_url + "/api/state", headers={"Cookie": cookie})
            with urllib.request.urlopen(state_request, timeout=3) as response:
                state = json.load(response)
            self.assertTrue(state["authenticated"])

            oversized = b"x" * (app.MAX_JSON_BODY_BYTES + 1)
            oversized_request = urllib.request.Request(
                base_url + "/api/login",
                data=oversized,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(oversized_request, timeout=3)
            self.assertEqual(raised.exception.code, 413)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_login_rate_limit(self) -> None:
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        body = json.dumps({"username": "admin", "password": "wrong"}).encode()
        try:
            with mock.patch.object(app, "LOGIN_ATTEMPT_LIMIT", 1):
                for expected_status in (401, 429):
                    request = urllib.request.Request(
                        base_url + "/api/login",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        urllib.request.urlopen(request, timeout=3)
                    self.assertEqual(raised.exception.code, expected_status)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
