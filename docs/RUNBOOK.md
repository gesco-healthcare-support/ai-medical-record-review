# Runbook

How to run the app and retrieve outputs.

> Replace `<SERVER_HOST>` / `<SERVER_IP>` / `<user>` with the real values from your
> internal notes or secret store. Do NOT commit real hosts or credentials.

## Run the app (server)

The app is a Flask app (Python 3.12) that serves on port **5010**.

1. SSH to the app server: `ssh <user>@<SERVER_HOST>`
2. `cd` into the repo directory.
3. Start it (the project now uses uv; the old flow was `pipenv shell` + `python app.py`):

   ```bash
   uv sync
   uv run python app.py
   ```

4. Open `http://<SERVER_IP>:5010/` in a browser.

Secrets (`GEMINI_API_KEY`, `OPENAI_API_KEY`) must be set in `.env` (copy from
`.env.example`). The app fails fast at startup if they are missing.

## Retrieve the generated MRRs

Output documents are written to the `MRRs` folder on the server (under the user's home).
Retrieve them via SFTP:

- Host: `<SERVER_HOST>`  |  Port: `22`  |  User: `<user>`
- Connect and download from the `MRRs` folder.

## Docker (alternative)

```bash
docker build -t ai-medical-record-review .
docker run --env-file .env -p 5010:5010 ai-medical-record-review
```
