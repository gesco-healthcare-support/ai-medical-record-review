"""The corpus convention (#219): one row per distinct PDF for anything pooled corpus-wide.

`one_copy_per_pdf` moved from `date_in_source.py` to `corpus.py` unchanged in behaviour; its three
existing tests stayed in `tests/test_date_in_source.py` and still pass there, exercising it through
that script's namespace. What is tested here is only the part that is new: a document-level helper
for scripts that iterate documents rather than rows, and the invariant that the two cannot disagree
about which copy is canonical.

That invariant is the point of the module. Two helpers each implementing "earliest copy wins" is
exactly the shape that has bitten this repo repeatedly - two renderers disagreeing (#158), three
export paths where the test covered two (#162) - so the rule lives in one function and both public
helpers are asserted to agree with it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "eval"))

from corpus import (  # noqa: E402
    canonical_document_ids,
    one_copy_per_document,
    one_copy_per_pdf,
)


class _Doc:
    """Minimal stand-in for a Document row: only the three fields the rule reads."""

    def __init__(self, doc_id, sha256, created_at):
        self.id = doc_id
        self.sha256 = sha256
        self.created_at = created_at


def test_one_copy_per_document_drops_a_re_upload():
    docs = [_Doc("a", "sha1", 1), _Doc("b", "sha1", 2), _Doc("c", "sha2", 3)]
    kept, dropped = one_copy_per_document(docs)

    assert [doc.id for doc in kept] == ["a", "c"]
    assert dropped == 1


def test_one_copy_per_document_keeps_the_earliest_copy():
    # Earliest wins so a figure does not move as further copies are uploaded. Measured cost of that
    # choice, over all 31 multi-copy PDFs on the box: it keeps 102 of the 103 reviewer corrections a
    # "whichever copy was actually reviewed" rule would find.
    kept, _dropped = one_copy_per_document([_Doc("late", "sha1", 9), _Doc("early", "sha1", 1)])

    assert [doc.id for doc in kept] == ["early"]


def test_one_copy_per_document_preserves_input_order_so_a_run_repeats():
    docs = [_Doc("c", "s3", 3), _Doc("a", "s1", 1), _Doc("b", "s2", 2)]
    kept, _dropped = one_copy_per_document(docs)

    assert [doc.id for doc in kept] == ["c", "a", "b"]


def test_one_copy_per_document_is_deterministic_on_a_timestamp_tie():
    # Two copies uploaded in the same instant must not pick a different winner per run.
    first = one_copy_per_document([_Doc("y", "s", 5), _Doc("x", "s", 5)])[0]
    second = one_copy_per_document([_Doc("x", "s", 5), _Doc("y", "s", 5)])[0]

    assert [d.id for d in first] == [d.id for d in second] == ["x"]


def test_a_single_copy_corpus_is_left_alone():
    docs = [_Doc("a", "sha1", 1), _Doc("b", "sha2", 2)]
    kept, dropped = one_copy_per_document(docs)

    assert dropped == 0
    assert [doc.id for doc in kept] == ["a", "b"]


def test_the_row_and_document_helpers_pick_the_SAME_canonical_copy():
    """The invariant the module exists for: one rule, not two implementations of it.

    A script filtering documents and a script filtering (row, document) pairs must agree, or two
    numbers in two issues are drawn from different corpora and nothing says so.
    """
    docs = [
        _Doc("late", "sha1", 9),
        _Doc("early", "sha1", 1),
        _Doc("mid", "sha1", 5),
        _Doc("solo", "sha2", 3),
    ]
    pairs = [(object(), doc) for doc in docs for _ in range(2)]

    from_documents = {doc.id for doc in one_copy_per_document(docs)[0]}
    from_pairs = {doc.id for _row, doc in one_copy_per_pdf(pairs)[0]}

    assert from_documents == from_pairs == canonical_document_ids(docs) == {"early", "solo"}


def test_a_document_appearing_twice_in_the_input_is_counted_once():
    # A join can yield the same document row more than once; the rule must not read that as copies.
    docs = [_Doc("a", "sha1", 1), _Doc("a", "sha1", 1)]
    assert canonical_document_ids(docs) == {"a"}
