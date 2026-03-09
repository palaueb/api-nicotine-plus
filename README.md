# API Nicotine Plus

Nicotine+ plugin that exposes a local REST API.

## Default Configuration

- `host`: `127.0.0.1`
- `port`: `12339`
- `api_token`: if empty, authentication is not required

## Endpoints

- `GET /health`
- `GET /status`
- `POST /search`
- `GET /searches`
- `GET /search/results`
- `GET /uploads`
- `GET /downloads`
- `GET /uploads/users`
- `GET /downloads/users`
- `POST /downloads/enqueue`
- `POST /search/download`

## Complete API Documentation

For full Postman-style endpoint documentation (all parameters, request/response models, errors, auth, and behavior), see:

- [POSTMAN_API.md](POSTMAN_API.md)

## Curl Examples

```bash
curl http://127.0.0.1:12339/health

curl -X POST http://127.0.0.1:12339/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"metallica", "mode":"global"}'

curl http://127.0.0.1:12339/searches

curl 'http://127.0.0.1:12339/search/results'

curl 'http://127.0.0.1:12339/search/results?token=12345&limit=100&offset=0'

curl -X POST http://127.0.0.1:12339/downloads/enqueue \
  -H 'Content-Type: application/json' \
  -d '{"username":"pepe","virtual_path":"Music\\\\Artist\\\\track.mp3","size":12345678}'

curl -X POST http://127.0.0.1:12339/search/download \
  -H 'Content-Type: application/json' \
  -d '{"token":12345,"index":0}'

curl 'http://127.0.0.1:12339/uploads/users?active_only=true'

curl 'http://127.0.0.1:12339/uploads?user=pepe&active_only=true'
```

## Testing

A test runner is included at `./test_api.sh`.

Prerequisites:
- Nicotine+ is running.
- The plugin is enabled in Nicotine+.
- If you changed plugin code, reload the plugin (or restart Nicotine+) before running tests.
- `curl` and `python3` are available.

Run the full endpoint test suite:

```bash
./test_api.sh
```

Run tests against a custom host/port:

```bash
./test_api.sh --host 127.0.0.1 --port 12339
```

Run tests with API token authentication:

```bash
./test_api.sh --token 'YOUR_TOKEN'
# or
API_TOKEN='YOUR_TOKEN' ./test_api.sh
```

Validate authentication-required behavior (`401` without token, `200` with token):

```bash
./test_api.sh --token 'YOUR_TOKEN' --auth-required
```

Show available options:

```bash
./test_api.sh --help
```

## Notes

- Actions that interact with Nicotine+ (for example search) are executed on the main thread to keep GUI state consistent.
- If `api_token` is not empty, send:
  - `Authorization: Bearer <token>` or
  - `X-API-Token: <token>`
- If `api_token` is empty, no auth header or extra flag is required.
- `POST /search` now returns a `token`, which can be used with `GET /search/results?token=<token>`.
- Use `POST /downloads/enqueue` to queue a download directly.
- Use `POST /search/download` to queue a cached search result by `token` + `index`.

## License

This project is licensed under **GNU General Public License v3.0 or later** (`GPL-3.0-or-later`).

- Source code must remain open and distributed under GPL-compatible terms.
- The software is provided **as is**, without warranty of any kind (see full text in `LICENSE`).

For full terms, see the [LICENSE](LICENSE) file.
