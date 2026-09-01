"""THE CORPUS CONVENTION for every eval script in this directory. Read before writing a query.

**Any count pooled across the corpus must be one row per distinct `documents.sha256`.**

`sha256` is indexed and NOT unique, and the same PDF is uploaded more than once as a matter of
course: re-running a case is legitimate, and the duplicate-upload warning is scoped to one user
(`api/documents.py`, `Document.user_id == user.id`), so a second account re-uploading a record is
not warned at all. That scoping is right and must not be "fixed" - cross-account detection would
leak between accounts in a PHI system - which means the duplication is permanent and the
measurement side has to deal with it.

Measured on the box 2026-08-31: **87 document rows for 42 distinct PDFs.** More than half the rows
are redundant copies, and 29 shas are held by more than one user. It got worse rather than better
on 2026-08-31, when all 27 of one reviewer's documents were copied under another account (#194) so
the corrections could be measured against without risking the originals - necessary and authorised,
and it took the duplication from about a quarter of rows to over half.

Copies are NOT independent samples. They are the same pages through the same pipeline, so pooling
them does not merely inflate n, it fabricates agreement.

## This has already produced wrong numbers, twice

* #139 recomputed a pooled figure and it moved from *1,775 rows / 25 absent = 1.4%* to
  *954 rows / 19 absent = 2.0%* - **understated by about a third.** #135's description still carries
  the wrong version.
* On the reviewer-correction corpus - the ground truth for #153, #144 and #125 - pooling every copy
  reports **204 corrections where there are 102.** Exactly 2x, measured 2026-09-01. Anyone quoting
  a correction count without deduplicating is out by a factor of two.

## Earliest copy wins, and here is what that costs

`one_copy_per_pdf` keeps the copy with the lowest `(created_at, id)`, so a figure is stable as
further copies are uploaded. The obvious worry is that the copy a HUMAN reviewed may not be the
earliest one, in which case deduplicating would discard the corrections instead of the noise.

Measured rather than assumed, over all 31 multi-copy PDFs on the box: **16 have a corrected copy,
and on exactly 1 of those the earliest copy carries no corrections while a later one does.** So the
earliest-copy rule keeps 102 of the 103 corrections a "best copy wins" rule would find. One
correction in 103 is not worth making the denominator depend on which copy someone happened to
work, so the simple rule stands - but a script whose entire unit of analysis is corrections should
say which rule it used, because that 1 row is a real row.

## How to use it

Default to deduplicating and offer `--all-copies` as the escape hatch, the way `date_in_source.py`
established:

    ap.add_argument("--all-copies", action="store_true",
                    help="count every uploaded copy of a record, not one per distinct PDF")
    ...
    if not args.all_copies:
        pairs, dropped_rows, dropped_docs = one_copy_per_pdf(pairs)

and PRINT what was dropped, so a reader of the output knows which denominator they are holding. A
pooled number looks perfectly plausible, gets quoted in an issue, and is wrong by a third; the whole
failure mode is that nothing about it looks wrong.

A single-document script (`date_vs_human_entries.py`) has no exposure here and needs none of this.
"""

from __future__ import annotations


def canonical_document_ids(documents) -> set:
    """The id of the earliest copy of each distinct `sha256`.

    The single implementation of the earliest-copy rule; both public helpers below go through it, so
    a script that filters DOCUMENTS and one that filters (row, document) pairs cannot end up
    disagreeing about which copy is canonical.

    Only `.sha256`, `.created_at` and `.id` are read, so any object carrying those three will do -
    which is how this is tested without a database.
    """
    by_sha: dict[str, object] = {}
    for doc in {d.id: d for d in documents}.values():
        winner = by_sha.get(doc.sha256)
        # (created_at, id): earliest copy wins, id breaks a same-timestamp tie so the choice is
        # deterministic. `created_at` is NOT NULL on documents, so no null case to carry.
        if winner is None or (doc.created_at, doc.id) < (winner.created_at, winner.id):
            by_sha[doc.sha256] = doc
    return {doc.id for doc in by_sha.values()}


def one_copy_per_document(documents):
    """Keep the earliest copy of each distinct PDF; -> (kept documents, docs dropped).

    For a script that iterates documents rather than rows. Order is preserved, so a run stays
    repeatable.
    """
    keep = canonical_document_ids(documents)
    kept = [doc for doc in documents if doc.id in keep]
    return kept, len(documents) - len(kept)


def one_copy_per_pdf(pairs):
    """Keep the rows of ONE document per distinct `sha256`; -> (kept, rows dropped, docs dropped).

    `pairs` is `[(row, document)]`; only `document.sha256`, `.created_at` and `.id` are read, so a
    plain object with those three attributes is enough (which is how this is tested).

    Earliest copy wins - see the module docstring for the measured cost of that choice, and for why
    the duplication exists in the first place.
    """
    documents = {doc.id: doc for _row, doc in pairs}
    keep_ids = canonical_document_ids(documents.values())
    kept = [(row, doc) for row, doc in pairs if doc.id in keep_ids]
    return kept, len(pairs) - len(kept), len(documents) - len(keep_ids)
