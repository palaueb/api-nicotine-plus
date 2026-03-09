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
- `GET /uploads`
- `GET /downloads`
- `GET /uploads/users`
- `GET /downloads/users`

## Curl Examples

```bash
curl http://127.0.0.1:12339/health

curl -X POST http://127.0.0.1:12339/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"metallica", "mode":"global"}'

curl 'http://127.0.0.1:12339/uploads/users?active_only=true'

curl 'http://127.0.0.1:12339/uploads?user=pepe&active_only=true'
```

## Testing

A test runner is included at `./test_api.sh`.

Prerequisites:
- Nicotine+ is running.
- The plugin is enabled in Nicotine+.
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
