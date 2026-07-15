"""Category catalog used by the B5 classification cascade.

This is a catalog (name + description + example doc-type titles per category). The example
titles mirror the hand-authored business taxonomy in ``groups.py`` in full - every title there
appears under its group here - enriched with a name + description per category for the
embedding and LLM stages. It is NOT yet the curated B6 taxonomy (deferred).

Notes:
- Category ids are strings to match the CSV ``category`` column and ``summarize`` options.
- Category 6 is intentionally omitted: it is empty in ``groups.py`` (no titles) and was never
  assignable, so there is nothing to mirror.
- Some group-5 entries are section headers ("History of Present Illness", "Physical
  Examination", "Diagnosis") rather than document types. They are kept to match ``groups.py``
  by decision; because they appear in nearly every report they can add matching noise, which
  the cascade's embedding-vs-LLM cross-check is relied on to dampen. Refining this is B6.
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
            "Primary Treating Physician's Progress Report (PR-2)",
            "Patient Progress Notes",
            "Progress Notes",
            "Progress Report",
            "Physician Notes",
            "Office Visit",
            "Encounter - Office Visit",
            "Encounter Office Visit",
            "Post Operative Visit",
            "Orthopedic Follow Up",
            "Orthopedic Re-evaluation",
            "Treating Orthopedic Evaluation",
            "Follow up Video Visit",
            "Telephone Appointment Visit",
            "Telephone Visit",
            "Family Medicine Clinic Note",
            "Nephrology Consult Note",
            "Transplant Follow Up",
            "Outpatient Palliative Care Consult",
            "Admission History & Physical",
            "Preoperative Hospital Admission History and Physical",
            "Physical Examination Reevaluation",
            "Supplemental Report",
            "Supplemental Report on Pain Management Process",
            "Initial Comprehensive Examination",
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
            "Primary Treating Physician's Maximum Medical Improvement for Impairment Rating Purposes",
            "Doctor's First Report of Occupational Injury or Illness",
            "Initial Patient Consultation",
            "Initial Orthopedic Consultation",
            "Specialist Initial Consultation",
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
            "Diagnostic Study",
            "Diagnostic",
            "Laboratory Report",
            "NCS/EMG Report",
            "Electrodiagnostic Study",
            "Unattended Sleep Study",
            "Auto CPAP",
            "Colonoscopy Report",
            "Dexa Bone Density Hip and Spine",
            "Bilateral Mammogram Screening",
            "Diabetic Muscle Infraction",
            "Ed (Emergency Department) Provider Notes",
            "X-Ray Report",
            "X Ray Report",
            "XRay Report",
            "XR Wrist Minimum 3 Views",
            "MRI Report",
            "MRI Shoulder",
            "MRI Left Shoulder",
            "MRI Left Shoulder w/o Contrast",
            "MRI Right Shoulder",
            "MRI Right Shoulder w/o Contrast",
            "MRI Lumbar",
            "MRI Lumbar Spine",
            "MRI Lumbar Spine w/o Contrast",
            "MRI Lumbar Spine Without Contrast",
            "CT Scan",
            "CT Scan Report",
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
            "Chiropractic Evaluation",
            "Chiropractic Progress Report",
            "Acupuncture Worksheet",
            "Acupuncture Worksheet Established",
            "Acupuncture Worksheet Final",
            "Physical Therapy Note",
            "Physical Therapy Daily Note",
            "PT Initial Report",
            "PT Progress",
            "PT Daily",
            "Acupuncture Daily",
            "Daily Encounter",
            "SOAP Notes",
            "Chiropractor Notes",
            # Section headers kept to mirror groups.py (see module docstring): present in
            # nearly every report, so they add matching noise the cross-check must dampen.
            "History of Present Illness",
            "Physical Examination",
            "Diagnosis",
        ),
    ),
    "7": Category(
        "7",
        "Workers' compensation legal claim forms",
        "Workers' compensation claim forms and applications for adjudication of claim.",
        (
            "Worker's Compensation Claim Form",
            "Application for Adjudication of Claim",
            "Application of Adjudication of Claim",
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
        ("Deposition", "Video Conference Deposition", "Deposition Transcript", "Transcript"),
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
        (
            "QME/AME Supplemental Reports",
            "QME Supplemental Report",
            "AME Supplemental Report",
            "Supplemental Reports",
        ),
    ),
    "13": Category(
        "13",
        "QME/AME medical-legal evaluations",
        "Comprehensive medical-legal evaluations by a QME (Qualified Medical Evaluator) or AME "
        "(Agreed Medical Evaluator).",
        ("QME/AME reports", "QME report", "QME reports", "AME report", "AME reports"),
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
