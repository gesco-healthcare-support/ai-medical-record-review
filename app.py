"""Entry point. The application now lives in the mrr_ai package (see mrr_ai/__init__.py)."""

from mrr_ai import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5010, debug=True)
