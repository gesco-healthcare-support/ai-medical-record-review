"""Entry point. The application now lives in the mrr_ai package (see mrr_ai/__init__.py)."""

import os

from mrr_ai import create_app

app = create_app()

if __name__ == "__main__":
    # Debug must default OFF: the Werkzeug debugger allows code execution. Opt in via env.
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=5010, debug=debug)
