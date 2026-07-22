"""Review-row validation (ported from review_api.validate_rows).

Mirrors the client rules: integer pages, 1 <= start <= end <= total, ascending and
non-overlapping (gaps ALLOWED - users skip junk pages), known category. The list drives page
slicing, so violations must stop the pipeline here. The valid category set is read from the DB
catalog (active categories) at call time via an explicit session.
"""

from sqlalchemy.orm import Session

from app.services import catalog


def _as_int(value: object) -> int:
    """Coerce a page value to int ONLY if it is integer-valued, else raise ValueError.

    Plain int(3.5) silently truncates to 3, which would accept a fractional page the client
    rejects (Number.isInteger); guard so the client and server agree on a valid page number.
    """
    if isinstance(value, bool):  # bool is an int subclass; a checkbox flag is not a page number
        raise ValueError("boolean is not a page number")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("fractional page")
        return int(value)
    if isinstance(value, str):
        return int(value.strip())  # int("3") -> 3; int("3.5") already raises ValueError
    raise ValueError("unsupported page type")


def validate_rows(session: Session, rows, total_pages) -> str | None:
    """Return an error string for the first invalid row, or None."""
    if not rows:
        return "no rows to summarize"
    editable = set(catalog.get_category_ids(session, active_only=True))
    previous_end = 0
    for i, row in enumerate(rows, start=1):
        try:
            start, end = _as_int(row["start"]), _as_int(row["end"])
        except (KeyError, TypeError, ValueError):
            return f"row {i}: start/end must be integers"
        if not 1 <= start <= end <= total_pages:
            return f"row {i}: pages must satisfy 1 <= start <= end <= {total_pages}"
        if start <= previous_end:
            return f"row {i}: overlaps or is out of order with the previous row"
        previous_end = end
        if str(row.get("category")) not in editable:
            return f"row {i}: unknown category {row.get('category')!r}"
    return None
