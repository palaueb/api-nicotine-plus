# API Nicotine Plus - Complete Postman-Style API Reference

This document is the complete API contract for this plugin.

If an operation is not documented here, it is not supported by the current API version.

## 1. Base URL

Default:

```text
http://127.0.0.1:12339
```

Recommended Postman environment variables:

- `baseUrl` = `http://127.0.0.1:12339`
- `apiToken` = `<your token or empty>`
- `searchToken` = `<token returned by POST /search>`

## 2. Authentication

Authentication is controlled by plugin setting `api_token`.

- If `api_token` is empty: no authentication is required.
- If `api_token` is set: every `GET` and `POST` request must include one of:
  - `Authorization: Bearer <token>`
  - `X-API-Token: <token>`

Unauthorized response:

- HTTP `401`
- Body:

```json
{"error":"unauthorized"}
```

## 3. CORS / OPTIONS

All endpoints support `OPTIONS`:

- HTTP `204`
- Headers:
  - `Access-Control-Allow-Origin: *`
  - `Access-Control-Allow-Methods: GET, POST, OPTIONS`
  - `Access-Control-Allow-Headers: Content-Type, Authorization, X-API-Token`

## 4. Common Request/Response Rules

- Content type for POST requests: `Content-Type: application/json`
- Successful responses are JSON with HTTP `200`.
- Validation errors return HTTP `400` with body:

```json
{"error":"<message>"}
```

- Internal errors return HTTP `500` with body:

```json
{"error":"internal server error"}
```

- Main-thread callback timeout returns HTTP `504` with body:

```json
{"error":"Main thread callback timed out"}
```

## 5. Plugin Settings (GUI -> Plugin Settings)

These values affect API behavior:

- `host` (string)
  - Default: `127.0.0.1`
- `port` (int)
  - Default: `12339`
  - Allowed: `1024..65535`
- `api_token` (string)
  - Default: empty
  - Empty disables auth
- `default_active_only` (bool)
  - Default: `true`
  - Used when `active_only` query param is omitted
- `max_cached_searches` (int)
  - Default: `20`
  - Allowed: `1..200`
  - Maximum number of API-triggered searches kept in memory
- `max_results_per_search` (int)
  - Default: `5000`
  - Allowed: `100..50000`
  - Maximum cached rows per search token

## 6. Type Parsing Rules

### 6.1 Query booleans (`active_only`)

Accepted true values (case-insensitive):

- `1`, `true`, `yes`, `on`

Any other provided value is treated as `false`.
If omitted, default is `default_active_only` plugin setting.

### 6.2 Query integers (`token`, `limit`, `offset`)

- Must parse as integer, otherwise HTTP `400`.
- `/search/results`:
  - `limit` default `100`, clamped to `1..1000`
  - `offset` default `0`, minimum `0`

### 6.3 Body booleans (`switch_page`, `bypass_filter`)

Current implementation uses Python truthiness (`bool(value)`).
Use real JSON booleans (`true`/`false`) to avoid unexpected behavior.

## 7. Data Models

### 7.1 Transfer object

Returned by `/uploads` and `/downloads`:

```json
{
  "username": "string",
  "virtual_path": "string",
  "folder_path": "string|null",
  "size": 0,
  "current_byte_offset": 0,
  "speed": 0,
  "avg_speed": 0,
  "time_elapsed": 0,
  "time_left": 0,
  "queue_position": 0,
  "status": "string",
  "token": 0,
  "progress_pct": 12.34
}
```

Known status values include:

- `Queued`
- `Getting status`
- `Transferring`
- `Paused`
- `Cancelled`
- `Filtered`
- `Finished`
- `User logged off`
- `Connection closed`
- `Connection timeout`
- `Download folder error`
- `Local file error`

### 7.2 Search result item

Returned by `/search/results`:

```json
{
  "username": "string",
  "file_path": "string",
  "size": 0,
  "extension": "string",
  "is_private": false,
  "free_upload_slots": true,
  "queue_position": 0,
  "upload_speed": 0,
  "file_attributes": {},
  "received_at": 0.0
}
```

## 8. Endpoints

---

## 8.1 GET /health

### Postman Request

- Method: `GET`
- URL: `{{baseUrl}}/health`
- Auth headers: optional/required depending on `api_token`

### 200 Response

```json
{
  "status": "ok",
  "plugin": "API Nicotine Plus"
}
```

---

## 8.2 GET /status

### Postman Request

- Method: `GET`
- URL: `{{baseUrl}}/status`

### 200 Response

```json
{
  "connected": true,
  "login_status": 2,
  "login_username": "your_username",
  "uploads_total": 0,
  "downloads_total": 0,
  "uploads_active_users": 0,
  "downloads_active_users": 0
}
```

---

## 8.3 POST /search

Starts a Nicotine+ search and returns a token.

### Postman Request

- Method: `POST`
- URL: `{{baseUrl}}/search`
- Body (raw JSON):

```json
{
  "query": "metallica",
  "mode": "global",
  "room": null,
  "users": [],
  "switch_page": true
}
```

### Body Fields

- `query` (string, required, non-empty)
- `mode` (string, optional, default `global`)
  - Allowed: `global`, `rooms`, `buddies`, `user`
- `room` (string, optional)
  - Used with `mode=rooms`
- `users` (string or array of strings, optional)
  - Required when `mode=user`
- `switch_page` (boolean, optional, default `true`)
  - Switches GUI page to the new search tab

### 200 Response

```json
{
  "ok": true,
  "token": 12345,
  "query": "metallica",
  "mode": "global",
  "room": null,
  "users": [],
  "switch_page": true
}
```

### Common 400 Errors

- `query is required`
- `mode must be one of: global, rooms, buddies, user`
- `users must be a string or list`
- `users is required when mode is 'user'`

---

## 8.4 GET /searches

Lists API-triggered search sessions kept in memory.

### Postman Request

- Method: `GET`
- URL: `{{baseUrl}}/searches`

### 200 Response

```json
{
  "count": 1,
  "last_token": 12345,
  "items": [
    {
      "token": 12345,
      "query": "metallica",
      "mode": "global",
      "room": null,
      "users": [],
      "switch_page": true,
      "created_at": 1773086000.123,
      "result_count": 42
    }
  ]
}
```

Notes:

- Only searches started via `POST /search` are stored.
- Older entries are evicted according to `max_cached_searches`.

---

## 8.5 GET /search/results

Returns cached results for a search token.

### Postman Request

- Method: `GET`
- URL: `{{baseUrl}}/search/results`
- Query params:
  - `token` (int, optional)
  - `limit` (int, optional, default `100`, range `1..1000`)
  - `offset` (int, optional, default `0`, min `0`)

Examples:

- `{{baseUrl}}/search/results` (uses latest token)
- `{{baseUrl}}/search/results?token={{searchToken}}`
- `{{baseUrl}}/search/results?token={{searchToken}}&limit=100&offset=0`

### 200 Response

```json
{
  "token": 12345,
  "query": "metallica",
  "mode": "global",
  "created_at": 1773086000.123,
  "total": 42,
  "offset": 0,
  "limit": 100,
  "count": 42,
  "items": [
    {
      "username": "peer_user",
      "file_path": "Music\\Artist\\Track.mp3",
      "size": 12345678,
      "extension": "mp3",
      "is_private": false,
      "free_upload_slots": true,
      "queue_position": 0,
      "upload_speed": 0,
      "file_attributes": {},
      "received_at": 1773086002.456
    }
  ]
}
```

### Common 400 Errors

- `token must be an integer`
- `No API search has been started yet`
- `Unknown search token: <token>`
- `limit must be an integer`
- `offset must be an integer`

---

## 8.6 GET /uploads

Returns uploads list.

### Postman Request

- Method: `GET`
- URL: `{{baseUrl}}/uploads`
- Query params:
  - `user` (string, optional)
  - `active_only` (bool-like string, optional)

Examples:

- `{{baseUrl}}/uploads`
- `{{baseUrl}}/uploads?active_only=true`
- `{{baseUrl}}/uploads?user=pepe&active_only=true`

### 200 Response

```json
{
  "direction": "uploads",
  "active_only": true,
  "user": "pepe",
  "count": 1,
  "items": []
}
```

---

## 8.7 GET /downloads

Returns downloads list.

### Postman Request

- Method: `GET`
- URL: `{{baseUrl}}/downloads`
- Query params:
  - `user` (string, optional)
  - `active_only` (bool-like string, optional)

Examples:

- `{{baseUrl}}/downloads`
- `{{baseUrl}}/downloads?active_only=false`
- `{{baseUrl}}/downloads?user=pepe&active_only=true`

### 200 Response

```json
{
  "direction": "downloads",
  "active_only": false,
  "user": null,
  "count": 0,
  "items": []
}
```

---

## 8.8 GET /uploads/users

Aggregates uploads by user.

### Postman Request

- Method: `GET`
- URL: `{{baseUrl}}/uploads/users`
- Query params:
  - `active_only` (bool-like string, optional)

### 200 Response

```json
{
  "direction": "uploads",
  "active_only": true,
  "count": 1,
  "items": [
    {
      "username": "pepe",
      "count": 10,
      "transferring": 2,
      "queued": 8
    }
  ]
}
```

---

## 8.9 GET /downloads/users

Aggregates downloads by user.

### Postman Request

- Method: `GET`
- URL: `{{baseUrl}}/downloads/users`
- Query params:
  - `active_only` (bool-like string, optional)

### 200 Response

```json
{
  "direction": "downloads",
  "active_only": true,
  "count": 0,
  "items": []
}
```

---

## 8.10 POST /downloads/enqueue

Queues a download directly in Nicotine+ downloads.

### Postman Request

- Method: `POST`
- URL: `{{baseUrl}}/downloads/enqueue`
- Body (raw JSON):

```json
{
  "username": "pepe",
  "virtual_path": "Music\\Artist\\Track.mp3",
  "folder_path": null,
  "size": 12345678,
  "file_attributes": {},
  "bypass_filter": false
}
```

### Body Fields

- `username` (string, required)
- `virtual_path` (string, required unless `file_path` is provided)
- `file_path` (string, optional alias of `virtual_path`)
- `folder_path` (string, optional)
- `size` (int, optional, default `0`, must be `>= 0`)
- `file_attributes` (object/dict, optional)
  - Keys are converted to int when possible
- `bypass_filter` (boolean, optional, default `false`)

### 200 Response

```json
{
  "ok": true,
  "username": "pepe",
  "virtual_path": "Music\\Artist\\Track.mp3",
  "folder_path": "/downloads/pepe",
  "size": 12345678,
  "bypass_filter": false,
  "queued": true,
  "duplicate": false,
  "status": "Queued"
}
```

Response fields:

- `queued`: transfer object exists after enqueue call
- `duplicate`: transfer count unchanged (likely already queued)

### Common 400 Errors

- `username is required`
- `virtual_path is required`
- `size must be an integer`
- `size must be >= 0`
- `file_attributes must be an object/dict`

---

## 8.11 POST /search/download

Queues one cached search result item by `token + index`.

### Postman Request

- Method: `POST`
- URL: `{{baseUrl}}/search/download`
- Body (raw JSON):

```json
{
  "token": 12345,
  "index": 0,
  "folder_path": null,
  "bypass_filter": false
}
```

### Body Fields

- `token` (int, optional)
  - If omitted, uses latest API search token
- `index` (int, required, `>= 0`)
- `folder_path` (string, optional)
- `bypass_filter` (boolean, optional, default `false`)

### 200 Response

```json
{
  "ok": true,
  "username": "peer_user",
  "virtual_path": "Music\\Artist\\Track.mp3",
  "folder_path": "/downloads/peer_user",
  "size": 12345678,
  "bypass_filter": false,
  "queued": true,
  "duplicate": false,
  "status": "Queued",
  "token": 12345,
  "index": 0
}
```

### Common 400 Errors

- `index is required`
- `index must be an integer`
- `index must be >= 0`
- `No API search has been started yet`
- `token must be an integer`
- `Unknown search token: <token>`
- `index out of range for search results`

---

## 8.12 Unknown Route Behavior

Any unknown `GET` or `POST` path returns:

- HTTP `400`
- Body:

```json
{"error":"Unknown endpoint: /your/path"}
```

## 9. Supported vs Not Supported

Supported:

- Start searches
- Retrieve cached results for API-triggered searches
- List uploads/downloads and user aggregates
- Queue downloads directly or from search results

Not supported (no endpoint currently):

- Pause/resume/cancel specific transfers
- Delete/clear transfer history
- Browse shares via API
- Chat/private messages via API
- Upload queue manipulation via API

## 10. Quick Postman Workflow

1. `POST /search` -> copy `token`
2. `GET /search/results?token=<token>` -> pick result `index`
3. `POST /search/download` with `token` and `index`
4. `GET /downloads?active_only=true` to track progress

