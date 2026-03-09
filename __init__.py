import json
import queue
import threading
import time
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
            "max_cached_searches": 20,
            "max_results_per_search": 5000,
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
            "max_cached_searches": {
                "description": "Maximum number of API-triggered searches kept in memory",
                "type": "int",
                "minimum": 1,
                "maximum": 200,
            },
            "max_results_per_search": {
                "description": "Maximum number of cached results per search token",
                "type": "int",
                "minimum": 100,
                "maximum": 50000,
            },
        }

        self._server = None
        self._server_thread = None
        self._main_thread = None
        self._search_cache_lock = threading.Lock()
        self._search_cache_order = []
        self._search_cache_meta = {}
        self._search_cache_results = {}
        self._last_api_search_token = None
        self._file_search_response_connected = False

    def init(self):
        self._main_thread = threading.current_thread()
        if not self._file_search_response_connected:
            events.connect("file-search-response", self._on_file_search_response)
            self._file_search_response_connected = True
        self._start_api_server()

    def disable(self):
        self._stop_api_server()
        self._disconnect_events()

    def shutdown_notification(self):
        self._stop_api_server()
        self._disconnect_events()

    def _disconnect_events(self):
        if not self._file_search_response_connected:
            return

        try:
            events.disconnect("file-search-response", self._on_file_search_response)
        except ValueError:
            pass

        self._file_search_response_connected = False

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

                if route == "/searches":
                    return plugin._get_searches()

                if route == "/search/results":
                    token = self._first(query, "token")
                    limit = self._int_param(query, "limit", 100)
                    offset = self._int_param(query, "offset", 0)
                    return plugin._get_search_results(token, limit=limit, offset=offset)

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

                if route == "/downloads/enqueue":
                    return plugin._call_main_thread(
                        plugin._enqueue_download_api,
                        payload,
                    )

                if route == "/search/download":
                    return plugin._call_main_thread(
                        plugin._enqueue_download_from_search_result,
                        payload,
                    )

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

            def _int_param(self, query, key, default):
                raw_value = self._first(query, key)

                if raw_value is None:
                    return int(default)

                try:
                    return int(raw_value)
                except ValueError as error:
                    raise ValueError(f"{key} must be an integer") from error

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

        token = int(self.core.search.token)
        timestamp = time.time()

        with self._search_cache_lock:
            self._search_cache_meta[token] = {
                "token": token,
                "query": query,
                "mode": mode,
                "room": room,
                "users": list(users),
                "switch_page": bool(switch_page),
                "created_at": timestamp,
                "result_count": 0,
            }
            self._search_cache_results[token] = []
            self._last_api_search_token = token
            self._touch_search_token(token)
            self._prune_search_cache_locked()

        return {
            "ok": True,
            "token": token,
            "query": query,
            "mode": mode,
            "room": room,
            "users": users,
            "switch_page": bool(switch_page),
        }

    def _touch_search_token(self, token):
        if token in self._search_cache_order:
            self._search_cache_order.remove(token)

        self._search_cache_order.append(token)

    def _prune_search_cache_locked(self):
        max_cached_searches = int(self.settings.get("max_cached_searches", 20))

        while len(self._search_cache_order) > max_cached_searches:
            oldest = self._search_cache_order.pop(0)
            self._search_cache_meta.pop(oldest, None)
            self._search_cache_results.pop(oldest, None)

    def _serialize_search_result_row(self, row, msg, private):
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            return None

        _code, file_path, size, *_unused = row
        ext = row[3] if len(row) > 3 else ""
        file_attributes = row[4] if len(row) > 4 else {}

        if not isinstance(file_attributes, dict):
            file_attributes = {}

        return {
            "username": msg.username,
            "file_path": file_path,
            "size": size,
            "extension": ext,
            "is_private": bool(private),
            "free_upload_slots": bool(msg.freeulslots),
            "queue_position": msg.inqueue or 0,
            "upload_speed": msg.ulspeed or 0,
            "file_attributes": file_attributes,
            "received_at": time.time(),
        }

    def _on_file_search_response(self, msg):
        token = getattr(msg, "token", None)

        if token is None:
            return

        with self._search_cache_lock:
            if token not in self._search_cache_meta:
                # Only cache searches triggered via this API.
                return

            cached_results = self._search_cache_results.setdefault(token, [])
            max_results = int(self.settings.get("max_results_per_search", 5000))

            for private, result_list in ((False, getattr(msg, "list", None)), (True, getattr(msg, "privatelist", None))):
                if not result_list:
                    continue

                for row in result_list:
                    if len(cached_results) >= max_results:
                        break

                    serialized_row = self._serialize_search_result_row(row, msg, private)

                    if serialized_row is not None:
                        cached_results.append(serialized_row)

            self._search_cache_meta[token]["result_count"] = len(cached_results)

    def _get_searches(self):
        with self._search_cache_lock:
            items = [
                dict(self._search_cache_meta[token])
                for token in reversed(self._search_cache_order)
                if token in self._search_cache_meta
            ]

        return {
            "count": len(items),
            "last_token": self._last_api_search_token,
            "items": items,
        }

    def _get_search_results(self, token=None, limit=100, offset=0):
        with self._search_cache_lock:
            if token is None:
                token = self._last_api_search_token
            else:
                try:
                    token = int(token)
                except (TypeError, ValueError) as error:
                    raise ValueError("token must be an integer") from error

            if token is None:
                raise ValueError("No API search has been started yet")

            if token not in self._search_cache_meta:
                raise ValueError(f"Unknown search token: {token}")

            limit = max(1, min(int(limit), 1000))
            offset = max(0, int(offset))

            meta = dict(self._search_cache_meta[token])
            all_results = self._search_cache_results.get(token, [])
            items = all_results[offset:offset + limit]

        return {
            "token": token,
            "query": meta["query"],
            "mode": meta["mode"],
            "created_at": meta["created_at"],
            "total": len(all_results),
            "offset": offset,
            "limit": limit,
            "count": len(items),
            "items": items,
        }

    @staticmethod
    def _normalize_file_attributes(file_attributes):
        if file_attributes is None:
            return None

        if not isinstance(file_attributes, dict):
            raise ValueError("file_attributes must be an object/dict")

        normalized = {}

        for key, value in file_attributes.items():
            try:
                normalized[int(key)] = value
            except (TypeError, ValueError):
                normalized[key] = value

        return normalized

    def _enqueue_download(self, username, virtual_path, folder_path=None, size=0, file_attributes=None,
                          bypass_filter=False):
        username = str(username or "").strip()
        virtual_path = str(virtual_path or "").strip()

        if not username:
            raise ValueError("username is required")

        if not virtual_path:
            raise ValueError("virtual_path is required")

        try:
            size = int(size or 0)
        except (TypeError, ValueError) as error:
            raise ValueError("size must be an integer") from error

        if size < 0:
            raise ValueError("size must be >= 0")

        file_attributes = self._normalize_file_attributes(file_attributes)
        previous_count = len(self.core.downloads.transfers)

        self.core.downloads.enqueue_download(
            username=username,
            virtual_path=virtual_path,
            folder_path=folder_path,
            size=size,
            file_attributes=file_attributes,
            bypass_filter=bool(bypass_filter),
        )

        transfer = self.core.downloads.transfers.get(username + virtual_path)
        is_duplicate = (len(self.core.downloads.transfers) == previous_count)

        return {
            "ok": True,
            "username": username,
            "virtual_path": virtual_path,
            "folder_path": transfer.folder_path if transfer is not None else folder_path,
            "size": size,
            "bypass_filter": bool(bypass_filter),
            "queued": transfer is not None,
            "duplicate": bool(is_duplicate),
            "status": transfer.status if transfer is not None else None,
        }

    def _enqueue_download_api(self, payload):
        username = payload.get("username")
        virtual_path = payload.get("virtual_path", payload.get("file_path"))
        folder_path = payload.get("folder_path")
        size = payload.get("size", 0)
        file_attributes = payload.get("file_attributes")
        bypass_filter = bool(payload.get("bypass_filter", False))

        return self._enqueue_download(
            username=username,
            virtual_path=virtual_path,
            folder_path=folder_path,
            size=size,
            file_attributes=file_attributes,
            bypass_filter=bypass_filter,
        )

    def _enqueue_download_from_search_result(self, payload):
        token = payload.get("token", self._last_api_search_token)
        result_index = payload.get("index")
        folder_path = payload.get("folder_path")
        bypass_filter = bool(payload.get("bypass_filter", False))

        if result_index is None:
            raise ValueError("index is required")

        try:
            result_index = int(result_index)
        except (TypeError, ValueError) as error:
            raise ValueError("index must be an integer") from error

        if result_index < 0:
            raise ValueError("index must be >= 0")

        with self._search_cache_lock:
            if token is None:
                raise ValueError("No API search has been started yet")

            try:
                token = int(token)
            except (TypeError, ValueError) as error:
                raise ValueError("token must be an integer") from error

            if token not in self._search_cache_results:
                raise ValueError(f"Unknown search token: {token}")

            items = self._search_cache_results[token]

            if result_index >= len(items):
                raise ValueError("index out of range for search results")

            item = dict(items[result_index])

        result = self._enqueue_download(
            username=item["username"],
            virtual_path=item["file_path"],
            folder_path=folder_path,
            size=item.get("size", 0),
            file_attributes=item.get("file_attributes"),
            bypass_filter=bypass_filter,
        )
        result["token"] = token
        result["index"] = result_index
        return result

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
