"""Single registry of every labeled case.

Each case has a stable `id`, its input `pdf`, and a `label_csv` (the hand-typed
CSV for the 3 clean cases, or the ROR-converted CSV for the other 8). OCR and all
experiments load cases from here, so adding a case is a one-place change.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import AI_SYSTEM_DIR
from ror_to_csv import ROR_CASES, ROR_LABELS, find_input_pdf


def _csv_cases():
    out = []
    for c in ["Case 1", "Case 2", "Case 3"]:
        d = os.path.join(AI_SYSTEM_DIR, c)
        pdf = glob.glob(os.path.join(d, "INPUT *.pdf"))[0]
        csvf = glob.glob(os.path.join(d, "INPUT *.csv"))[0]
        out.append(dict(id=c, pdf=pdf, label_csv=csvf, source="csv"))
    return out


def _ror_cases():
    out = []
    for label, d in ROR_CASES:
        pdf, _ = find_input_pdf(d)
        csvf = os.path.join(ROR_LABELS, f"{label}.csv")
        out.append(dict(id=label, pdf=pdf, label_csv=csvf, source="ror"))
    return out


CASES = _csv_cases() + _ror_cases()
CSV_CASE_IDS = [c["id"] for c in CASES if c["source"] == "csv"]
ALL_CASE_IDS = [c["id"] for c in CASES]


def by_id(case_id):
    for c in CASES:
        if c["id"] == case_id:
            return c
    raise KeyError(f"Unknown case id: {case_id}")
