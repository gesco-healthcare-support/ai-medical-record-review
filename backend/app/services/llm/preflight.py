"""Model-server preflight: never send a record to a server we have not vetted.

WHY THIS IS NOT IN ``config``. A version is a property of the RUNNING SERVER, so the only honest
check is to ask it. ``Settings`` cannot: it is constructed by pytest, by alembic and by every script
in ``scripts/eval``, none of which can reach a pod, and a check there would either break all of them
or compare a configured floor against a configured version - self-reported, and worth nothing.

TWO WAYS A CHECK CAN FAIL, AND THEY ARE HANDLED DIFFERENTLY.

- The server ANSWERS WRONGLY: a version below the floor, a reply of the wrong shape, an error
  status. That is a deployment fault, so every process refuses to start (``RuntimeError``).
- The server DOES NOT ANSWER (``ModelServerOfflineError``): no connection, the tunnel drops it, a
  timeout. On the test box that is the normal state between pod days, not a fault - the rented GPU
  is off overnight and all the records live in this app's database. Refusing to start then left the
  web app crash-looping, so reviewers could not even open or export a finished record. So:

  * the web app SERVES (``check_backends_before_serving``) and logs that the server is silent;
  * a worker WAITS (``wait_for_backends``): it takes no job until the server answers and passes, so
    queued work starts by itself when the pod comes up, and no job is failed row by row against a
    silent server - the reason, recorded in ``config`` above ``_validate_openai_provider``, the
    check sits at startup at all;
  * no vLLM client is ever handed out before the server has passed (``ensure_vllm_verified``, called
    by ``vllm._client``), so a model call from a web app that booted without the server is refused
    with a clear message until it answers - nothing reaches an unverified server.

A pass is remembered for the life of the process, as the boot-time check always was: a pod is only
ever replaced by a redeploy, which restarts every process. A failure is never remembered.

**DO NOT WRAP CALLS TO THESE IN ``except Exception``.** Both entry points have a neighbouring block
that deliberately swallows - ``main._lifespan`` around orphan recovery, so a Redis outage cannot stop
the web app serving, and ``worker.__main__`` around queue-lane enumeration. Copying that shape here
would turn a server that answers wrongly into a warning and a boot, the fail-open outcome this
exists to prevent. Only ``ModelServerOfflineError`` is ever caught, and only in this module.
"""

import logging
import threading
import time
from collections.abc import Callable
from urllib.parse import urlparse

from app.config import get_settings
from app.errors import ModelServerOfflineError

logger = logging.getLogger(__name__)

# Short. This runs before the app serves anything, so a hung probe is a hung deployment - and the
# thing being asked for is a constant the server holds in memory.
_PROBE_TIMEOUT_S = 10.0
# A waiting worker asks again this often. The pod comes up through a redeploy that restarts every
# worker anyway, so this only matters when the tunnel returns without one; 30 s is unnoticeable then.
_WAIT_POLL_S = 30.0
# ...and says it is still waiting once every this many checks (10 minutes), not every 30 s all night.
_WAIT_LOG_EVERY = 20

# Server roots that passed in this process. A set, not a flag, so a process whose VLLM_BASE_URL
# changed (only tests do that) cannot borrow another root's pass.
_verified: set[str] = set()
_verified_lock = threading.Lock()


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


def _verify(root: str, floor: str) -> None:
    """Return only if the server at ``root`` answered with a version at or above ``floor``.

    ``httpx.TransportError`` - no connection, a dropped connection, a timeout - is the one failure
    that means "nobody answered", and the only one reported as ``ModelServerOfflineError``. Every
    other failure means something DID answer, and not the way a vetted server does.
    """
    import httpx

    try:
        served = _probe_vllm_version(root)
    except httpx.TransportError as exc:
        raise ModelServerOfflineError(
            f"the vLLM server at {root} did not answer ({type(exc).__name__}: {exc})"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"could not read the vLLM version from {root}/version ({type(exc).__name__}: {exc}). "
            "Refusing to send records to an unverified server."
        ) from exc

    if _version_tuple(served) < _version_tuple(floor):
        raise RuntimeError(
            f"the vLLM server at {root} reports version {served}, below the required floor {floor}. "
            "That floor is a CVE boundary, not a preference - upgrade the server or change "
            "VLLM_MIN_VERSION deliberately."
        )
    logger.info("vllm preflight: %s reports version %s (floor %s)", root, served, floor)


def ensure_vllm_verified() -> None:
    """Check the configured vLLM server once per process; every vLLM client is handed out through this.

    Raises ``ModelServerOfflineError`` when nobody answers and ``RuntimeError`` when the server
    answers wrongly or the URL does not parse. Only a pass is remembered, so a server that comes up
    later is checked again on the next call.
    """
    settings = get_settings()
    root = _server_root(settings.vllm_base_url)
    if not root:
        raise RuntimeError(
            f"VLLM_BASE_URL={settings.vllm_base_url!r} does not parse as a URL, so the vLLM server "
            "cannot be checked before traffic reaches it."
        )
    if root in _verified:
        return
    with _verified_lock:
        if root not in _verified:
            _verify(root, settings.vllm_min_version)
            _verified.add(root)


def assert_backends_ready() -> None:
    """Check every configured backend; raise if one is not fit to receive traffic.

    Only the vLLM path is probed. Gemini is the backend we roll BACK to, so it must not acquire a new
    network dependency or a new way to fail at boot; and there is nothing to ask it, since Vertex
    does not serve a version we pin.
    """
    settings = get_settings()
    if "vllm" not in settings.resolved_backends():
        return
    ensure_vllm_verified()


def check_backends_before_serving() -> None:
    """The web app's startup check: refuse a server that answers wrongly, serve without a silent one.

    The app holds the records, so with the model server off a reviewer can still open, edit and
    export their work. Any model call it makes meanwhile goes through ``ensure_vllm_verified`` and
    is refused with ``ModelServerOfflineError`` until the server answers and passes.
    """
    try:
        assert_backends_ready()
    except ModelServerOfflineError as exc:
        logger.warning(
            "model server not answering at startup (%s); serving without it - model calls are "
            "refused until it answers and passes the version check",
            exc,
        )


def wait_for_backends(
    *, poll_s: float = _WAIT_POLL_S, sleep: Callable[[float], None] = time.sleep
) -> None:
    """A worker's startup check: take no job until the model server answers and passes.

    A server that answers wrongly raises at once, as before. A silent one is asked again every
    ``poll_s`` seconds; jobs queued meanwhile stay queued and run when it comes up. Stopping the
    container interrupts the sleep, so a waiting worker shuts down as promptly as an idle one.
    """
    checks = 0
    while True:
        try:
            assert_backends_ready()
        except ModelServerOfflineError as exc:
            if checks % _WAIT_LOG_EVERY == 0:
                logger.warning(
                    "model server not answering (%s); this worker takes no job until it does, "
                    "checking every %.0f s",
                    exc,
                    poll_s,
                )
            checks += 1
            sleep(poll_s)
            continue
        if checks:
            logger.info("model server answered after %d check(s); taking jobs", checks + 1)
        return
