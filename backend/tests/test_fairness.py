"""One reviewer's batch cannot hold every identify worker while another reviewer waits.

`app/worker/fairness.py` decides; `tasks._run` acts on it before a job is marked running. The rule
is checked against the real test Redis (each xdist worker has its own database, and --dist loadfile
keeps this file on one worker), because what it reads - a lane's queue length and RQ's
StartedJobRegistry - is exactly the part a fake would get wrong.
"""

import time
import uuid

import pytest
from rq import Queue
from rq.registry import StartedJobRegistry

from app.auth.password import MrrPasswordHelper
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Document, Job, User
from app.services import jobs
from app.worker import fairness
from app.worker import tasks as tasks_mod
from app.worker.cancel import request_cancel
from app.worker.queues import get_redis, lane_name
from app.worker.tasks import _run
from tests.conftest import unique_test_email

# Owner ids no real test user will have, so these lanes belong to this file alone.
A, B, C = 90101, 90102, 90103


@pytest.fixture
def redis():
    connection = get_redis()
    names = [lane_name(base, owner) for base in ("segment", "summarize") for owner in (A, B, C)]

    def clear():
        for name in names:
            Queue(name, connection=connection).empty()
            connection.delete(StartedJobRegistry(name, connection=connection).key)

    clear()
    yield connection
    clear()
    fairness.serve([])


def _serve(*owners, base="segment"):
    fairness.serve([base] + [lane_name(base, owner) for owner in owners])


def _running(connection, owner, count, *, base="segment", ids=None):
    """Put `count` jobs in the owner's started registry, as RQ 2 stores them (job_id:execution)."""
    registry = StartedJobRegistry(lane_name(base, owner), connection=connection)
    ids = ids or [f"run-{owner}-{i}" for i in range(count)]
    connection.zadd(registry.key, {f"{job_id}:exec": time.time() + 600 for job_id in ids})
    return ids


def _waiting(connection, owner, count=1, *, base="segment"):
    queue = Queue(lane_name(base, owner), connection=connection)
    for _ in range(count):
        queue.enqueue("builtins.len", "x")


def test_a_reviewer_at_the_cap_steps_aside_for_one_who_is_waiting(redis):
    _serve(A, B)
    _running(redis, A, 3)
    _waiting(redis, B)
    assert fairness.should_step_aside("segment", A, 3, redis)


def test_classify_jobs_count_as_identify_too(redis):
    # classify rides the segment queue and the same lane, so it is identify work for the cap.
    _serve(A, B)
    _running(redis, A, 3)
    _waiting(redis, B)
    assert fairness.should_step_aside("classify", A, 3, redis)


def test_a_reviewer_working_alone_is_never_capped(redis):
    _serve(A, B)
    _running(redis, A, 5)
    assert not fairness.should_step_aside("segment", A, 3, redis)


def test_below_the_cap_the_job_runs(redis):
    _serve(A, B)
    _running(redis, A, 2)
    _waiting(redis, B)
    assert not fairness.should_step_aside("segment", A, 3, redis)


def test_the_job_asking_does_not_count_against_its_own_reviewer(redis):
    # A running job is already in its own lane's started registry; counting it would cap a
    # reviewer one job early.
    _serve(A, B)
    ids = _running(redis, A, 3)
    _waiting(redis, B)
    assert not fairness.should_step_aside("segment", A, 3, redis, current_rq_id=ids[0])


def test_a_waiting_reviewer_already_at_the_cap_does_not_count(redis):
    # The livelock guard: if every waiting reviewer is at the cap, nobody steps aside, so a free
    # worker always takes something.
    _serve(A, B)
    _running(redis, A, 3)
    _running(redis, B, 3)
    _waiting(redis, B)
    assert not fairness.should_step_aside("segment", A, 3, redis)


def test_a_lane_this_worker_does_not_serve_does_not_count(redis):
    # A reviewer added after the workers started has a lane nobody reads. Stepping aside for them
    # would hand the turn to no one, and the busy reviewer would step aside forever.
    _serve(A, B)
    _running(redis, A, 3)
    _waiting(redis, C)
    assert not fairness.should_step_aside("segment", A, 3, redis)


def test_an_ownerless_job_on_the_base_queue_does_not_count(redis):
    _serve(A, B)
    _running(redis, A, 3)
    Queue("segment", connection=redis).enqueue("builtins.len", "x")
    try:
        assert not fairness.should_step_aside("segment", A, 3, redis)
    finally:
        Queue("segment", connection=redis).empty()


def test_summarize_work_is_never_capped(redis):
    _serve(A, B, base="summarize")
    _running(redis, A, 3, base="summarize")
    _waiting(redis, B, base="summarize")
    assert not fairness.should_step_aside("summarize", A, 3, redis)
    assert not fairness.should_step_aside("dedup", A, 3, redis)


@pytest.mark.parametrize("cap", [0, -1])
def test_a_cap_of_zero_turns_the_rule_off(redis, cap):
    _serve(A, B)
    _running(redis, A, 3)
    _waiting(redis, B)
    assert not fairness.should_step_aside("segment", A, cap, redis)


def test_without_served_lanes_the_rule_is_off(redis):
    # Tests and scripts call task functions directly, with no worker around them.
    _running(redis, A, 3)
    _waiting(redis, B)
    assert not fairness.should_step_aside("segment", A, 3, redis)


def test_a_redis_failure_runs_the_job(redis):
    class _Broken:
        def __getattr__(self, name):
            raise ConnectionError("redis down")

    _serve(A, B)
    assert not fairness.should_step_aside("segment", A, 3, _Broken())


# --- the runner ---------------------------------------------------------------------------------


def _job(kind="segment") -> int:
    with get_sessionmaker()() as session:
        user = User(
            email=unique_test_email(),
            name="Fairness",
            password=MrrPasswordHelper().hash("Str0ng#pw1"),
            active=True,
        )
        session.add(user)
        session.flush()
        document = Document(
            id=str(uuid.uuid4()),
            user_id=user.id,
            original_filename="synthetic.pdf",
            stored_path="/nonexistent/synthetic.pdf",
            sha256="0" * 64,
            page_count=2,
        )
        session.add(document)
        session.commit()
        return jobs.create_job(session, document.id, kind, model="m", prompt_version="1").id


def _owner_lane(job_id) -> Queue:
    with get_sessionmaker()() as session:
        owner = session.get(Document, session.get(Job, job_id).document_id).user_id
    return Queue(lane_name("segment", owner), connection=get_redis())


def test_a_job_that_steps_aside_stays_queued_at_the_front_of_its_lane(monkeypatch):
    job_id = _job()
    lane = _owner_lane(job_id)
    lane.empty()
    lane.enqueue("builtins.len", "already-waiting")
    monkeypatch.setattr(fairness, "should_step_aside", lambda *a, **k: True)
    ran = []
    try:
        _run(job_id, lambda session, job, report: ran.append(True))

        assert ran == []
        with get_sessionmaker()() as session:
            job = session.get(Job, job_id)
            assert job.state == "queued"
            assert job.started_at is None
            assert job.rq_job_id.startswith(f"{job_id}-turn-")
        front = lane.jobs[0]
        # The same DB job, under the id that was committed first, with the finalizers a re-dispatch
        # must not drop: without them a force-stopped run stays "running" forever.
        assert front.id == job.rq_job_id
        assert front.args == (job_id,)
        assert front.func_name.endswith("tasks.segment_document")
        assert front.success_callback is None
        assert front.stopped_callback is not None
        assert front.failure_callback is not None
        assert front.timeout == get_settings().effective_job_timeout(2)
    finally:
        lane.empty()


def test_a_job_the_reviewer_asked_to_stop_never_steps_aside(monkeypatch):
    job_id = _job()
    # Both halves of a stop, as the cancel route sets them: the row flag this check reads, and the
    # Redis flag the job's first progress report reads.
    with get_sessionmaker()() as session:
        session.get(Job, job_id).cancel_requested = True
        session.commit()
    request_cancel(job_id)
    asked = []
    monkeypatch.setattr(fairness, "should_step_aside", lambda *a, **k: asked.append(True) or True)

    _run(job_id, lambda session, job, report: report("segmenting", 1, 2))

    assert asked == []
    with get_sessionmaker()() as session:
        assert session.get(Job, job_id).state == "cancelled"


def test_a_job_that_cannot_go_back_runs_now_with_its_id_restored(monkeypatch):
    job_id = _job()
    with get_sessionmaker()() as session:
        before = session.get(Job, job_id).rq_job_id
    monkeypatch.setattr(fairness, "should_step_aside", lambda *a, **k: True)

    def no_redis(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(tasks_mod, "_dispatch_again", no_redis)
    ran = []

    _run(job_id, lambda session, job, report: ran.append(True))

    assert ran == [True]
    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        assert job.state == "done"
        assert job.rq_job_id == before


def test_the_runner_asks_with_the_reviewer_the_setting_and_its_own_rq_id(monkeypatch):
    job_id = _job("classify")
    with get_sessionmaker()() as session:
        owner = session.get(Document, session.get(Job, job_id).document_id).user_id
    monkeypatch.setattr(get_settings(), "identify_per_reviewer_cap", 7)
    monkeypatch.setattr("rq.get_current_job", lambda: type("_J", (), {"id": "rq-self"})())
    seen = {}

    def spy(kind, who, cap, connection, current_rq_id=None):
        seen.update(kind=kind, owner=who, cap=cap, current=current_rq_id)
        return False

    monkeypatch.setattr(fairness, "should_step_aside", spy)

    _run(job_id, lambda session, job, report: None)

    assert seen == {"kind": "classify", "owner": owner, "cap": 7, "current": "rq-self"}


def test_the_worker_records_the_lanes_it_serves(monkeypatch, redis):
    # Without this the rule is off in production: should_step_aside reads _served_lanes, and a forked
    # work-horse inherits whatever main() put there.
    from app.worker import __main__ as worker_main

    class _Worker:
        def __init__(self, queues, connection=None, **kwargs):
            pass

        def work(self, **kwargs):
            pass

    monkeypatch.setattr(worker_main, "RoundRobinWorker", _Worker)
    monkeypatch.setattr(worker_main, "_user_ids", lambda: [A, B])
    worker_main.main(["segment"])

    assert fairness.served() == ("segment", f"segment:{A}", f"segment:{B}")
