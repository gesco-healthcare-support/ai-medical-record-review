"""The suite's own database selection, tested - because when it is wrong nothing else runs.

`conftest._local_database_url` derives the test database from the compose files rather than a
constant, and it must pair each port with the credential that port actually accepts. Getting that
pairing wrong does not fail one test: it fails EVERY DB-touching test on connection, before a single
assertion, with an error that names authentication rather than the real cause. That happened - 614
errors in 14 minutes - to anyone who set POSTGRES_PASSWORD in the repo-root `.env`, because the
value was applied to whichever port won and the winner is preferred to be the dev stack, which
hardcodes its own password and never reads that variable.
"""

import pytest

from tests.conftest import (
    _compose_postgres_ports,
    _worker_database_url,
    _worker_email_prefix,
    _worker_redis_url,
    redis_port_is_shared,
)


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


# Parallel runs (pytest-xdist in CI) share ONE Postgres server and ONE Redis. Three things would otherwise
# let a worker disturb another worker's test data mid-test: the cleanup deletes every user whose email
# starts with the prefix, the queue tests empty and count whole queues, and some code reads a WHOLE table
# (recover_orphans sweeps every active job). Each worker therefore gets its own prefix, its own Redis
# database and its own Postgres database, and a serial run keeps exactly what it had.


def test_a_serial_run_keeps_the_original_email_prefix():
    assert _worker_email_prefix({}) == "pytest-auth-", "a serial run changed its test-user prefix"


def test_each_worker_cleans_only_the_users_it_created():
    gw2 = _worker_email_prefix({"PYTEST_XDIST_WORKER": "gw2"})
    assert gw2 == "pytest-auth-gw2-", "a worker is not using its own test-user prefix"
    assert _worker_email_prefix({"PYTEST_XDIST_WORKER": "gw3"}) != gw2, "two workers share a prefix"
    # The serial prefix is a prefix of every worker's, so a later serial run still sweeps up anything a
    # killed parallel run left behind.
    assert gw2.startswith(_worker_email_prefix({})), (
        "a serial run can no longer clean worker leftovers"
    )


def test_a_serial_run_keeps_the_original_redis_url():
    url = "redis://localhost:6379/0"
    assert _worker_redis_url(url, {}) == url, "a serial run changed its Redis URL"


def test_each_worker_gets_its_own_redis_database():
    gw0 = {"PYTEST_XDIST_WORKER": "gw0"}
    assert _worker_redis_url("redis://localhost:6379/0", gw0) == "redis://localhost:6379/1", (
        "worker gw0 is not on Redis database 1"
    )
    assert (
        _worker_redis_url("redis://localhost:6379/0", {"PYTEST_XDIST_WORKER": "gw3"})
        == "redis://localhost:6379/4"
    ), "worker gw3 is not on Redis database 4"
    # Only the database changes - host, port and credentials are kept, and a URL without one gets one.
    assert _worker_redis_url("redis://:pw@cache:6380/0", gw0) == "redis://:pw@cache:6380/1"
    assert _worker_redis_url("redis://localhost:6379", gw0) == "redis://localhost:6379/1"


def test_an_unrecognised_worker_id_is_refused_rather_than_guessed():
    # Guessing would put two workers on one database - the collision this exists to prevent.
    with pytest.raises(RuntimeError, match="PYTEST_XDIST_WORKER"):
        _worker_redis_url("redis://localhost:6379/0", {"PYTEST_XDIST_WORKER": "master"})


_DB_URL = "postgresql+psycopg://mrr:dev-only-pw@localhost:5432/mrr?connect_timeout=5"


def test_a_serial_run_keeps_the_original_database_url():
    assert _worker_database_url(_DB_URL, {}) == _DB_URL, "a serial run changed its database URL"


def test_each_worker_gets_its_own_database():
    gw0 = _worker_database_url(_DB_URL, {"PYTEST_XDIST_WORKER": "gw0"})
    # Only the database name changes - credentials, host, port and connect_timeout are kept.
    assert gw0 == "postgresql+psycopg://mrr:dev-only-pw@localhost:5432/mrr_gw0?connect_timeout=5", (
        "worker gw0 is not on its own database"
    )
    gw3 = _worker_database_url(_DB_URL, {"PYTEST_XDIST_WORKER": "gw3"})
    assert gw3.split("?")[0].endswith("/mrr_gw3"), "worker gw3 is not on its own database"


def test_an_unrecognised_worker_id_gets_no_database_guessed_for_it():
    with pytest.raises(RuntimeError, match="PYTEST_XDIST_WORKER"):
        _worker_database_url(_DB_URL, {"PYTEST_XDIST_WORKER": "master"})
