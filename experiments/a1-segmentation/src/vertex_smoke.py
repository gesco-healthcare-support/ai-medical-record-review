"""NON-PHI smoke test for the Gemini client routing (Vertex vs AI Studio).

Uses only synthetic prompts (no patient data), so it is safe to run anytime to verify auth,
routing, and the model id before spending on a real run. Exercises both call shapes the
oracles use: a plain generate_content and a constrained-enum classify.

  python src/vertex_smoke.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import genai_client as gc


def main():
    mode = "VERTEX (aiplatform.googleapis.com, BAA)" if gc.USE_VERTEX else "AI STUDIO (Developer API)"
    auth = "service-account/ADC" if (gc.USE_VERTEX and os.environ.get("GOOGLE_CLOUD_PROJECT")) else (
        "GCP API key" if gc.USE_VERTEX else "AI Studio API key")
    print(f"routing : {mode}")
    print(f"auth    : {auth}")
    print(f"model   : {gc.MODEL}")
    print("-" * 60)

    cost = gc.Cost()
    # 1) plain generation (mirrors generate_json / window oracle call shape)
    try:
        client = gc.client()
        r = client.models.generate_content(model=gc.MODEL, contents="Reply with exactly: PONG")
        print(f"generate_content: OK  reply={(r.text or '').strip()[:20]!r}")
    except Exception as e:
        code = getattr(e, "code", getattr(e, "status_code", None))
        reason = ""
        if "API_KEY_SERVICE_BLOCKED" in str(e):
            reason = "  -> the API key is not allowed to call the Vertex AI API (enable the API + lift the key's API restriction)"
        elif "PERMISSION_DENIED" in str(e):
            reason = "  -> permission/enablement issue on the project"
        print(f"generate_content: FAIL {type(e).__name__} code={code}{reason}\n  {str(e)[:240]}")
        return

    # 2) constrained-enum (mirrors the adjacent / range-probe oracle call shape)
    try:
        val = gc.classify_enum(
            ["Classify this fruit: a banana is yellow and curved."],
            ("APPLE", "BANANA"),
            "You classify fruit. Return exactly one label.",
            cost,
        )
        print(f"classify_enum  : OK  value={val!r}")
    except Exception as e:
        print(f"classify_enum  : FAIL {type(e).__name__}: {str(e)[:200]}")
        return

    print("-" * 60)
    print(f"cost: {cost.summary()}")
    print("SMOKE OK - Vertex routing is live; safe to run the bake-off." if gc.USE_VERTEX
          else "SMOKE OK on AI Studio (NOT BAA-covered; do not use for PHI).")


if __name__ == "__main__":
    main()
