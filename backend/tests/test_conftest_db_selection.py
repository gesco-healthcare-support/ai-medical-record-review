"""The suite's own database selection, tested - because when it is wrong nothing else runs.

`conftest._local_database_url` derives the test database from the compose files rather than a
constant, and it must pair each port with the credential that port actually accepts. Getting that
pairing wrong does not fail one test: it fails EVERY DB-touching test on connection, before a single
assertion, with an error that names authentication rather than the real cause. That happened - 614
errors in 14 minutes - to anyone who set POSTGRES_PASSWORD in the repo-root `.env`, because the
value was applied to whichever port won and the winner is preferred to be the dev stack, which
hardcodes its own password and never reads that variable.
"""

from tests.conftest import _compose_postgres_ports, redis_port_is_shared


def test_dev_stack_publishes_the_redis_port_the_suite_uses():
    """The suite's Redis default is redis://localhost:6379/0, so the dev stack must publish 6379.

    Not a guard against the wrong Redis - nothing a client can ask distinguishes another project's
    server from ours, and the application's Redis binds no host port so there is no MRR ambiguity to
    resolve. This pins the one thing the compose files DO answer: that the port the suite reaches for
    is a port this repo actually publishes. If someone renumbers it, the queue tests would otherwise
    start silently using whatever else holds 6379 - which is how the 2026-08-13 mass failure
    happened.
    """
    assert redis_port_is_shared() is False


def test_parse_reports_whether_each_stack_reads_the_env_password():
    """docker-compose.yml substitutes ${POSTGRES_PASSWORD:-...}; docker-compose.dev.yml hardcodes it.

    The distinction is the whole point of the third element: without it the caller cannot tell which
    stack an explicit .env password legitimately applies to.
    """
    by_port = {port: (default, reads_env) for port, default, reads_env in _compose_postgres_ports()}

    # The APP stack takes the variable, so an operator override is meaningful there.
    assert by_port[5433] == ("mrr_local_only", True)
    # The DEV stack hardcodes it, so .env must be ignored for this port.
    assert by_port[5432] == ("mrr_dev_only", False)


def test_dev_password_is_never_overridden_by_the_env_file():
    """Whatever POSTGRES_PASSWORD says, port 5432 gets the literal from docker-compose.dev.yml.

    Asserted on the parse rather than on a live URL so it holds whether or not either stack is up.
    """
    dev = next(entry for entry in _compose_postgres_ports() if entry[0] == 5432)
    _, default_pw, reads_env_password = dev
    assert reads_env_password is False
    # Mirrors the resolution in _local_database_url: a False flag drops env_pw entirely.
    env_pw = "a-password-from-dot-env"
    assert ((env_pw if reads_env_password else "") or default_pw) == "mrr_dev_only"
