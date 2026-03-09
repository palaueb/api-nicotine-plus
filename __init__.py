import json
import queue
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs
from urllib.parse import urlparse

from pynicotine.events import events
from pynicotine.pluginsystem import BasePlugin
from pynicotine.slskmessages import UserStatus
from pynicotine.transfers import TransferStatus


class _PluginHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, request_handler_class, plugin):
        super().__init__(server_address, request_handler_class)
        self.plugin = plugin


class Plugin(BasePlugin):

    ACTIVE_TRANSFER_STATUSES = {
        TransferStatus.QUEUED,
        TransferStatus.GETTING_STATUS,
        TransferStatus.TRANSFERRING,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.settings = {
            "host": "127.0.0.1",
            "port": 12339,
            "api_token": "",
            "default_active_only": True,
        }
        self.metasettings = {
            "host": {
                "description": "Bind address for REST API (recommended: 127.0.0.1)",
                "type": "string",
            },
            "port": {
                "description": "REST API TCP port",
                "type": "int",
                "minimum": 1024,
                "maximum": 65535,
            },
            "api_token": {
                "description": "API token for auth (leave empty to disable auth)",
                "type": "string",
            },
            "default_active_only": {
                "description": "Default to active transfers only when active_only is omitted",
                "type": "bool",
            },
        }

        self._server = None
        self._server_thread = None
        self._main_thread = None

    def init(self):
        self._main_thread = threading.current_thread()
        self._start_api_server()

    def disable(self):
        self._stop_api_server()

    def shutdown_notification(self):
        self._stop_api_server()

    def _start_api_server(self):
        self._stop_api_server()

        host = str(self.settings.get("host", "127.0.0.1")).strip() or "127.0.0.1"
        port = int(self.settings.get("port", 12339))

        handler_class = self._build_handler_class()

        try:
            self._server = _PluginHTTPServer((host, port), handler_class, self)
        except OSError as error:
            self._server = None
            self.log("Failed to start REST API on %s:%s: %s", (host, port, error))
            return

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"{self.internal_name}-rest-api",
            daemon=True,
        )
        self._server_thread.start()
        self.log("REST API listening on http://%s:%s", (host, port))

    def _stop_api_server(self):
        if self._server is None:
            return

        try:
            self._server.shutdown()
            self._server.server_close()
        finally:
            self._server = None

        if self._server_thread is not None and self._server_thread.is_alive():
            self._server_thread.join(timeout=2)

        self._server_thread = None

    def _build_handler_class(self):
        plugin = self

        class RestHandler(BaseHTTPRequestHandler):
            server_version = "NicotinePlusPluginREST/1.0"

            def do_OPTIONS(self):
                self.send_response(204)
                self._send_common_headers(content_type=None)
                self.end_headers()

            def do_GET(self):
                try:
                    self._require_auth()
                    response = self._dispatch_get()
                    self._send_json(200, response)
                except PermissionError:
                    self._send_json(401, {"error": "unauthorized"})
                except ValueError as error:
                    self._send_json(400, {"error": str(error)})
                except TimeoutError as error:
                    self._send_json(504, {"error": str(error)})
                except Exception as error:  # pylint: disable=broad-except
                    plugin.log("REST GET error: %s", (error,))
                    self._send_json(500, {"error": "internal server error"})

            def do_POST(self):
                try:
                    self._require_auth()
                    response = self._dispatch_post()
                    self._send_json(200, response)
                except PermissionError:
                    self._send_json(401, {"error": "unauthorized"})
                except ValueError as error:
                    self._send_json(400, {"error": str(error)})
                except TimeoutError as error:
                    self._send_json(504, {"error": str(error)})
                except Exception as error:  # pylint: disable=broad-except
                    plugin.log("REST POST error: %s", (error,))
                    self._send_json(500, {"error": "internal server error"})

            def _dispatch_get(self):
                parsed = urlparse(self.path)
                route = parsed.path
                query = parse_qs(parsed.query, keep_blank_values=False)

                if route == "/health":
                    return {
                        "status": "ok",
                        "plugin": plugin.human_name,
                    }

                if route == "/status":
                    return plugin._call_main_thread(plugin._get_status)

                if route in {"/uploads", "/downloads"}:
                    direction = route.lstrip("/")
                    user = self._first(query, "user")
                    active_only = self._bool_param(query, "active_only", plugin.settings["default_active_only"])
                    return plugin._call_main_thread(
                        plugin._get_transfers,
                        direction,
                        user=user,
                        active_only=active_only,
                    )

                if route in {"/uploads/users", "/downloads/users"}:
                    direction = route.split("/")[1]
                    active_only = self._bool_param(query, "active_only", plugin.settings["default_active_only"])
                    return plugin._call_main_thread(
                        plugin._get_transfer_users,
                        direction,
                        active_only=active_only,
                    )

                raise ValueError(f"Unknown endpoint: {route}")

            def _dispatch_post(self):
                parsed = urlparse(self.path)
                route = parsed.path
                payload = self._read_json_body()

                if route == "/search":
                    query = str(payload.get("query", "")).strip()
                    mode = str(payload.get("mode", "global")).strip().lower()
                    room = payload.get("room")
                    users = payload.get("users")
                    switch_page = bool(payload.get("switch_page", True))

                    result = plugin._call_main_thread(
                        plugin._start_search,
                        query,
                        mode,
                        room,
                        users,
                        switch_page,
                    )
                    return result

                raise ValueError(f"Unknown endpoint: {route}")

            def _read_json_body(self):
                content_length = int(self.headers.get("Content-Length", "0"))

                if content_length <= 0:
                    return {}

                raw_body = self.rfile.read(content_length)

                try:
                    return json.loads(raw_body.decode("utf-8"))
                except json.JSONDecodeError as error:
                    raise ValueError(f"Invalid JSON body: {error}") from error

            def _send_common_headers(self, content_type):
                if content_type:
                    self.send_header("Content-Type", content_type)

                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Token")

            def _send_json(self, status_code, payload):
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")

                self.send_response(status_code)
                self._send_common_headers("application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _require_auth(self):
                token = str(plugin.settings.get("api_token", "")).strip()

                if not token:
                    return

                authorization = self.headers.get("Authorization", "")
                header_token = self.headers.get("X-API-Token", "").strip()

                bearer_token = ""
                if authorization.lower().startswith("bearer "):
                    bearer_token = authorization[7:].strip()

                if token not in {header_token, bearer_token}:
                    raise PermissionError("Unauthorized")

            @staticmethod
            def _first(query, key, default=None):
                values = query.get(key)

                if not values:
                    return default

                return values[0]

            def _bool_param(self, query, key, default):
                raw_value = self._first(query, key)

                if raw_value is None:
                    return bool(default)

                return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}

            def log_message(self, _format, *_args):
                # Keep plugin logs clean from per-request noise.
                return

        return RestHandler

    def _call_main_thread(self, callback, *args, timeout=3.0, **kwargs):
        if threading.current_thread() is self._main_thread:
            return callback(*args, **kwargs)

        result_queue = queue.Queue(maxsize=1)

        def run_callback():
            try:
                result_queue.put((True, callback(*args, **kwargs)))
            except Exception as error:  # pylint: disable=broad-except
                result_queue.put((False, error))

        events.invoke_main_thread(run_callback)

        try:
            success, payload = result_queue.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError("Main thread callback timed out") from error

        if success:
            return payload

        raise payload

    def _start_search(self, query, mode, room=None, users=None, switch_page=True):
        query = str(query or "").strip()

        if not query:
            raise ValueError("query is required")

        mode = str(mode or "global").lower()

        if mode not in {"global", "rooms", "buddies", "user"}:
            raise ValueError("mode must be one of: global, rooms, buddies, user")

        if users is None:
            users = []
        elif isinstance(users, str):
            users = [users]
        elif not isinstance(users, list):
            raise ValueError("users must be a string or list")

        if mode == "user" and not users:
            raise ValueError("users is required when mode is 'user'")

        self.core.search.do_search(
            query,
            mode,
            room=room,
            users=users,
            switch_page=bool(switch_page),
        )

        return {
            "ok": True,
            "query": query,
            "mode": mode,
            "room": room,
            "users": users,
            "switch_page": bool(switch_page),
        }

    def _get_status(self):
        login_status = self.core.users.login_status

        return {
            "connected": login_status != UserStatus.OFFLINE,
            "login_status": login_status,
            "login_username": self.core.users.login_username,
            "uploads_total": len(self.core.uploads.transfers),
            "downloads_total": len(self.core.downloads.transfers),
            "uploads_active_users": len(self.core.uploads.active_users),
            "downloads_active_users": len(self.core.downloads.active_users),
        }

    def _transfer_to_dict(self, transfer):
        current = transfer.current_byte_offset
        size = transfer.size
        progress = None

        if size and current is not None and size > 0:
            progress = round(min(100.0, (current / size) * 100.0), 2)

        return {
            "username": transfer.username,
            "virtual_path": transfer.virtual_path,
            "folder_path": transfer.folder_path,
            "size": size,
            "current_byte_offset": current,
            "speed": transfer.speed,
            "avg_speed": transfer.avg_speed,
            "time_elapsed": transfer.time_elapsed,
            "time_left": transfer.time_left,
            "queue_position": transfer.queue_position,
            "status": transfer.status,
            "token": transfer.token,
            "progress_pct": progress,
        }

    def _get_transfers(self, direction, user=None, active_only=True):
        if direction not in {"uploads", "downloads"}:
            raise ValueError("direction must be uploads or downloads")

        manager = self.core.uploads if direction == "uploads" else self.core.downloads
        transfer_objects = list(manager.transfers.values())

        if user:
            transfer_objects = [item for item in transfer_objects if item.username == user]

        if active_only:
            transfer_objects = [
                item for item in transfer_objects
                if item.status in self.ACTIVE_TRANSFER_STATUSES
            ]

        transfer_objects.sort(
            key=lambda item: (
                item.username or "",
                item.status or "",
                item.virtual_path or "",
            )
        )

        items = [self._transfer_to_dict(item) for item in transfer_objects]

        return {
            "direction": direction,
            "active_only": bool(active_only),
            "user": user,
            "count": len(items),
            "items": items,
        }

    def _get_transfer_users(self, direction, active_only=True):
        transfer_payload = self._get_transfers(direction, user=None, active_only=active_only)
        users = defaultdict(lambda: {"count": 0, "transferring": 0, "queued": 0})

        for item in transfer_payload["items"]:
            username = item["username"]
            users[username]["count"] += 1

            if item["status"] == TransferStatus.TRANSFERRING:
                users[username]["transferring"] += 1
            elif item["status"] in {TransferStatus.QUEUED, TransferStatus.GETTING_STATUS}:
                users[username]["queued"] += 1

        sorted_users = [
            {
                "username": username,
                "count": data["count"],
                "transferring": data["transferring"],
                "queued": data["queued"],
            }
            for username, data in sorted(users.items(), key=lambda item: (-item[1]["count"], item[0]))
        ]

        return {
            "direction": direction,
            "active_only": bool(active_only),
            "count": len(sorted_users),
            "items": sorted_users,
        }
