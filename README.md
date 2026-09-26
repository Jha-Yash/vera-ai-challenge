# Vera Challenge Bot

Minimal deterministic FastAPI bot implementing the Magicpin Vera challenge contract.

## Run locally

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080
```

Health:
`http://localhost:8080/v1/healthz`

## Public URL

For a one-hour submission, deploy this folder to a Docker-capable host such as Render, or expose the local port with an HTTPS tunnel such as ngrok.

Submit only the base URL, e.g. `https://your-service.example.com`.

## Required endpoints

- GET `/v1/healthz`
- GET `/v1/metadata`
- POST `/v1/context`
- POST `/v1/tick`
- POST `/v1/reply`

The bot stores contexts by `(scope, context_id, version)`, suppresses duplicate sends, composes merchant/customer messages from the four contexts, and handles repeated WhatsApp auto-replies and explicit declines.
