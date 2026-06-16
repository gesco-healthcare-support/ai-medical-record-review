"""Category catalog used by the B5 classification cascade.

This is a lightweight catalog (name + description + example doc-type titles per category),
derived from the existing taxonomy (``groups.py`` and the "Categories" source doc). It is
NOT the curated B6 taxonomy (deferred): just enough to drive the embedding and LLM stages of
the cascade, plus integrity guarantees.

Notes:
- Category ids are strings to match the CSV ``category`` column and ``summarize`` options.
- Category 6 is intentionally omitted: it is undefined in the source taxonomy (empty in
  ``groups.py``), matching prior behavior where it was never assignable by the fuzzy matcher.
- Section names ("History of Present Illness", "Physical Examination", "Diagnosis") that the
  old taxonomy mislabeled as document types are excluded - they appear in nearly every report
  and caused systematic mis-categorization. Cleaning the full taxonomy is B6.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    """One categorization target: its id and the text used for semantic matching."""

    id: str
    name: str
    description: str
    examples: tuple[str, ...]

    @property
    def corpus(self) -> str:
        """Representative text for this category (embedded + shown to the LLM)."""
        return f"{self.name}. {self.description} Examples: " + "; ".join(self.examples)


CATEGORIES: dict[str, "Category"] = {
    "1": Category(
        "1",
        "Treating progress and follow-up reports (PR-2)",
        "Routine treating-physician progress notes, office/clinic visits, and follow-ups.",
        (
            "Medical Progress Reports (PR-2)",
            "Patient Progress Notes",
            "Physician Notes",
            "Office Visit",
            "Post Operative Visit",
            "Orthopedic Follow Up",
            "Follow up Video Visit",
            "Telephone Appointment Visit",
            "Family Medicine Clinic Note",
        ),
    ),
    "2": Category(
        "2",
        "Comprehensive and permanent evaluations (PR-4)",
        "Permanent and Stationary (PR-4), Maximum Medical Improvement, initial comprehensive "
        "consultations, and the Doctor's First Report of Occupational Injury.",
        (
            "Primary Treating Physician's Permanent and Stationary Report (PR-4)",
            "Maximum Medical Improvement for Impairment Rating Purposes",
            "Doctor's First Report of Occupational Injury or Illness",
            "Initial Patient Consultation",
            "Initial Orthopedic Consultation",
            "Complex Orthopedic Evaluation",
            "Consultative Rating Determination",
        ),
    ),
    "3": Category(
        "3",
        "Diagnostic studies and imaging",
        "Imaging and diagnostic studies: X-Ray, MRI, CT, EMG/NCS, laboratory reports, sleep "
        "studies, and similar.",
        (
            "Diagnostic Study (X-Ray, MRI, CT scan)",
            "Laboratory Report",
            "NCS/EMG Report",
            "Unattended Sleep Study",
            "Colonoscopy Report",
            "Dexa Bone Density Hip and Spine",
            "Bilateral Mammogram Screening",
        ),
    ),
    "4": Category(
        "4",
        "GI outpatient procedure H&P",
        "Gastrointestinal outpatient procedure history and physical.",
        ("GI Outpatient Procedure H&P",),
    ),
    "5": Category(
        "5",
        "Physical therapy, chiropractic, and acupuncture",
        "Physical therapy, chiropractic, and acupuncture evaluations and progress reports.",
        (
            "Initial Acupuncture Intake Form",
            "Initial Chiropractic Evaluation",
            "Chiropractic Progress Report",
            "Acupuncture Worksheet",
            "Physical Therapy Note",
            "PT Initial Report",
            "PT Progress",
            "Chiropractor Notes",
        ),
    ),
    "7": Category(
        "7",
        "Workers' compensation legal claim forms",
        "Workers' compensation claim forms and applications for adjudication of claim.",
        (
            "Worker's Compensation Claim Form",
            "Application for Adjudication of Claim",
            "Amended Application for Adjudication of Claim",
        ),
    ),
    "8": Category(
        "8",
        "Operative and surgical pathology reports",
        "Operative reports and surgical pathology reports.",
        ("Operative Report", "Surgical Pathology Report", "Oversight Physician Report"),
    ),
    "9": Category(
        "9",
        "Depositions",
        "Deposition transcripts of testimony.",
        ("Deposition", "Video Conference Deposition", "Deposition Transcript"),
    ),
    "10": Category(
        "10",
        "Request For Authorization (RFA)",
        "Request For Authorization for treatment or services.",
        ("RFA (Request For Authorization)",),
    ),
    "11": Category(
        "11",
        "Comprehensive interval history / medical decision making",
        "Comprehensive interval history forms and medical decision making documents.",
        ("Comprehensive Interval History Form", "Medical Decision Making"),
    ),
    "12": Category(
        "12",
        "QME/AME supplemental reports",
        "Supplemental reports from a QME (Qualified Medical Evaluator) or AME (Agreed Medical "
        "Evaluator) - follow-ups to a prior medical-legal evaluation.",
        ("QME/AME Supplemental Reports", "QME Supplemental Report", "AME Supplemental Report"),
    ),
    "13": Category(
        "13",
        "QME/AME medical-legal evaluations",
        "Comprehensive medical-legal evaluations by a QME (Qualified Medical Evaluator) or AME "
        "(Agreed Medical Evaluator).",
        ("QME/AME reports", "QME report", "AME report"),
    ),
    "14": Category(
        "14",
        "Laboratory and test results",
        "Standalone laboratory or test result documents.",
        ("Results", "Laboratory Results", "Test Results"),
    ),
    "100": Category(
        "100",
        "General or uncategorized documents",
        "Documents that do not clearly fit any specific category.",
        ("General Documents", "Everything else"),
    ),
}

# Valid classification outputs (strings, matching the CSV category column).
ALLOWED_IDS: tuple[str, ...] = tuple(CATEGORIES.keys())

# The fallback bucket when no category can be determined.
DEFAULT_ID = "100"
