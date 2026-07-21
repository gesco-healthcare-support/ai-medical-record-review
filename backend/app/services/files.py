"""Filesystem-safe filename helper + small date utils.

The FastAPI backend does not depend on werkzeug (that is Flask's WSGI layer), so the upload
path's sanitizer is reimplemented here, reproducing `werkzeug.secure_filename`: NFKD-normalize
to ASCII, replace path separators, collapse to [A-Za-z0-9_.-], strip leading/trailing dots and
underscores, and guard Windows reserved device names. Use at every boundary where request data
builds a filesystem path.
"""

import os
import re
import unicodedata
from datetime import datetime

ALLOWED_EXTENSIONS = {"pdf"}

_FILENAME_ASCII_STRIP_RE = re.compile(r"[^A-Za-z0-9_.-]")
_WINDOWS_DEVICE_FILES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _secure_filename(filename: str) -> str:
    filename = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    for sep in (os.sep, os.path.altsep):
        if sep:
            filename = filename.replace(sep, " ")
    filename = _FILENAME_ASCII_STRIP_RE.sub("", "_".join(filename.split())).strip("._")
    # Prefix Windows reserved device names so they can never become a special file.
    if os.name == "nt" and filename and filename.split(".")[0].upper() in _WINDOWS_DEVICE_FILES:
        filename = f"_{filename}"
    return filename


def safe_name(filename: str | None, fallback: str = "upload") -> str:
    """A filesystem-safe basename for a user-supplied filename; falls back when empty."""
    return _secure_filename(filename or "") or fallback


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%m/%d/%Y")
    except ValueError:
        return datetime.min  # sort invalid dates first


def is_valid_date(date_str: str, date_format: str = "%m/%d/%Y") -> bool:
    try:
        if date_str.strip() == "-":
            return True  # "-" is the intentional "unavailable" sentinel
        datetime.strptime(date_str, date_format)
        return True
    except ValueError:
        return False
