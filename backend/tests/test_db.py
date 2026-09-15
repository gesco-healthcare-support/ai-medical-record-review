"""The session factory's flags, which the thread pools depend on."""

from app.db import get_sessionmaker


def test_the_session_factory_keeps_the_flags_the_thread_pools_depend_on():
    """GUARD, and it passes on main - the point is that it fails if either flag is flipped.

    `expire_on_commit=False` is not a preference. The segmentation pool's `_stored_page_text`
    closure reads `document.id` and `document.stored_path` from a WORKER THREAD, off an ORM object
    owned by the caller's session. With expiry on, any commit in the outer thread - and `report()`
    commits throughout segmentation - would turn those reads into a SELECT on the outer session
    from another thread, which is the concurrent use a Session does not support.

    Nothing else states that dependency, and a reviewer looking at `get_sessionmaker` alone would
    see two flags that look like ordinary defaults to tidy up.
    """
    factory = get_sessionmaker()
    assert factory.kw["expire_on_commit"] is False, (
        "the segmentation pools read ORM attributes across threads; expiry would make those reads "
        "hit the outer session from a worker"
    )
    assert factory.kw["autoflush"] is False
