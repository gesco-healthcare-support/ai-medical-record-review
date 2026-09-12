"""Startup preflight for the model backends: refuse to run against a server we have not vetted.

WHY THIS IS NOT IN ``config``. A version is a property of the RUNNING SERVER, so the only honest
check is to ask it. ``Settings`` cannot: it is constructed by pytest, by alembic and by every script
in ``scripts/eval``, none of which can reach a pod, and a check there would either break all of them
or compare a configured floor against a configured version - self-reported, and worth nothing.

WHY IT IS NOT ON THE FIRST CALL EITHER. ``config`` already records the reason, in the comment above
``_validate_openai_provider``: a worker that boots and then errors per row burns a job and leaves the
reviewer with a half-processed document. Startup is where a deployment fault should surface.

**DO NOT WRAP CALLS TO THIS IN ``except Exception``.** Both entry points that invoke it have a
neighbouring block that deliberately swallows - ``main._lifespan`` around orphan recovery, so a Redis
outage cannot stop the web app serving, and ``worker.__main__`` around queue-lane enumeration. Both
are right for what they guard, and copying that shape here would produce a guard that logs a warning
and boots anyway, which is precisely the fail-open outcome this exists to prevent. The local idiom
argues the wrong way; that is why this paragraph exists.
"""

import logging
from urllib.parse import urlparse

from app.config import get_settings

logger = logging.getLogger(__name__)

# Short. This runs before the app serves anything, so a hung probe is a hung deployment - and the
# thing being asked for is a constant the server holds in memory.
_PROBE_TIMEOUT_S = 10.0


def _server_root(base_url: str) -> str:
    """``scheme://netloc`` for a base URL that normally ends in ``/v1``.

    ``/version`` is served at the ROOT, not under the OpenAI-compatible prefix, so appending it to
    ``vllm_base_url`` would request ``/v1/version``, get a 404, and this module would then refuse
    every boot while reporting an unreachable server.
    """
    parsed = urlparse((base_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _version_tuple(raw: str) -> tuple[int, ...]:
    """Leading numeric components of a version string: "0.28.0" -> (0, 28, 0).

    Stops at the first component that does not begin with a digit, so "0.28.0rc1" reads as (0, 28, 0)
    rather than raising. Deliberately does NOT model pre-release ordering: this is a floor check
    against a CVE fix, and deciding whether a release candidate of the floor version carries that fix
    is not something to do silently in a comparison.
    """
    out: list[int] = []
    for part in (raw or "").split("."):
        digits = ""
        for char in part:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def _probe_vllm_version(root: str) -> str:
    """The vLLM server's self-reported version, via ``GET /version``.

    Verified on the live 0.28.0 build 2026-09-11: HTTP 200, body ``{"version": "0.28.0"}``, and
    UNAUTHENTICATED - identical with and without the api-key header, because vLLM's ``--api-key``
    covers only the ``/v1``, ``/v2`` and ``/inference`` prefixes. So no credential is sent here, and
    this works before auth is configured.

    The route is undocumented (absent from the OpenAI-compatible server docs at every version,
    including the one we run), so its shape is not contractual. Anything unexpected is treated as a
    failed probe rather than as a pass.
    """
    import httpx

    response = httpx.get(f"{root}/version", timeout=_PROBE_TIMEOUT_S)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(
            f"GET {root}/version returned {type(payload).__name__}, expected an object"
        )
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"GET {root}/version returned no usable 'version' field: {payload!r}")
    return version.strip()


def assert_backends_ready() -> None:
    """Refuse to finish starting up if a configured backend is not fit to receive traffic.

    Only the vLLM path is probed. Gemini is the backend we roll BACK to, so it must not acquire a new
    network dependency or a new way to fail at boot; and there is nothing to ask it, since Vertex
    does not serve a version we pin.
    """
    settings = get_settings()
    if "vllm" not in settings.resolved_backends():
        return

    root = _server_root(settings.vllm_base_url)
    if not root:
        raise RuntimeError(
            f"VLLM_BASE_URL={settings.vllm_base_url!r} does not parse as a URL, so the vLLM server "
            "cannot be checked before traffic reaches it."
        )

    try:
        served = _probe_vllm_version(root)
    except Exception as exc:
        # Re-raised, never swallowed. An unreachable pod at boot is a real deployment fault: the
        # alternative is a worker that starts, accepts a job, and fails it per row. Recovery is
        # LLM_BACKEND=gemini plus a redeploy, which is one setting and needs no code change.
        raise RuntimeError(
            f"could not read the vLLM version from {root}/version ({type(exc).__name__}: {exc}). "
            "Refusing to start rather than send records to an unverified server."
        ) from exc

    floor = settings.vllm_min_version
    if _version_tuple(served) < _version_tuple(floor):
        raise RuntimeError(
            f"the vLLM server at {root} reports version {served}, below the required floor {floor}. "
            "That floor is a CVE boundary, not a preference - upgrade the server or change "
            "VLLM_MIN_VERSION deliberately."
        )
    logger.info("vllm preflight: %s reports version %s (floor %s)", root, served, floor)
