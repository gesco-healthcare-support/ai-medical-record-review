"""Local dev server entrypoint.

On Windows, psycopg3's async driver cannot run on asyncio's ProactorEventLoop. Uvicorn's own
loop setup forces the Proactor policy on Windows, so setting a policy beforehand is not enough:
we create a SelectorEventLoop ourselves and run the server's serve() coroutine on it, bypassing
uvicorn's loop management. On Linux/prod, run uvicorn directly (this file is dev-only).

Usage (from backend/, with the dev env exported):
    uv run python run_dev.py
"""

import asyncio
import sys

import uvicorn


def main() -> None:
    config = uvicorn.Config("app.main:app", host="127.0.0.1", port=8000)
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        import selectors

        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        finally:
            loop.close()
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
