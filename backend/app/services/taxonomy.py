"""Category catalog used by the B5 classification cascade.

This is a catalog (name + description + example doc-type titles per category). The example
titles mirror the hand-authored business taxonomy in ``groups.py`` in full - every title there
appears under its group here - enriched with a name + description per category for the
embedding and LLM stages. It is NOT yet the curated B6 taxonomy (deferred).

Notes:
- Category ids are strings to match the CSV ``category`` column and ``summarize`` options.
- Category 6 is intentionally omitted: it is empty in ``groups.py`` (no titles) and was never
  assignable, so there is nothing to mirror. It exists downstream (a prompt + an editor label,
  seeded by ``seed_catalog._ID_SIX``) and its document types - daily encounter and SOAP notes -
  are auto-assigned into category 5, whose prompt now carries the same specification.
- The classifier reads the DB catalog first (``catalog.get_categories``) and only falls back to
  these constants, so a change here reaches an EXISTING box only with a migration that updates
  the ``categories`` rows. See ``alembic/.../category_modality_vs_specimen``.
- The bare section headers that used to sit under category 5 ("History of Present Illness",
  "Physical Examination", "Diagnosis") were removed on 2026-07-30 (register D-05): they appear in
  nearly every report, so they attracted anything with a physical-exam heading rather than the
  therapy notes the category is for.
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
        # D-03: emergency-department provider notes moved here from category 3 - an ED encounter is
        # a treating visit, not a diagnostic study, and this category's prompt already carries an
        # "Emergency Department Report" point set. D-04: the unqualified "Supplemental Report" moved
        # to category 12, which owns QME/AME supplementals; the pain-management one stays, since it
        # is a treating supplemental.
        "Routine treating-physician progress notes, office/clinic visits, follow-ups, and "
        "emergency-department encounter notes. A supplemental report responding to a prior "
        "medical-legal (QME or AME) evaluation belongs to the QME/AME supplemental category, "
        "not here.",
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
            "Supplemental Report on Pain Management Process",
            "Initial Comprehensive Examination",
            "Ed (Emergency Department) Provider Notes",
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
        # D-01/D-02: this category is MODALITY-based and 14 is SPECIMEN-based. Both descriptions name
        # the distinction and each points at the other, because the classifier's LLM stage sees only
        # this text - listing "Laboratory Report" here (as it did) told it the opposite.
        "Studies performed ON THE BODY with an instrument, reported as an image or a tracing that a "
        "physician reads: X-Ray, MRI, CT, ultrasound, mammogram, EMG/NCS, ECG, sleep study, bone "
        "density, and endoscopy. A test run on a SPECIMEN taken from the body - blood, urine, or a "
        "toxicology screen - is a laboratory result and belongs to the laboratory category, not here.",
        (
            "Diagnostic Study (X-Ray, MRI, CT scan)",
            "Diagnostic Study",
            "Diagnostic",
            "NCS/EMG Report",
            "Electrodiagnostic Study",
            "Unattended Sleep Study",
            "Auto CPAP",
            "Colonoscopy Report",
            "Dexa Bone Density Hip and Spine",
            "Bilateral Mammogram Screening",
            "Diabetic Muscle Infraction",
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
        # D-06: category 6 (daily / SOAP notes) defines the same document types with a conflicting
        # spec and is never auto-assigned, so those notes land here. Naming them makes the auto-assign
        # target explicit; category 5's prompt now carries category 6's point set for them.
        "Physical therapy, occupational therapy, chiropractic, and acupuncture evaluations, progress "
        "reports, and daily encounter or SOAP notes.",
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
            # Added live by an admin via PATCH /admin/categories/5 before 2026-07-30; folded into the
            # code so a fresh box and the server agree (they plainly belong beside the PT notes).
            "Occupational Therapy Daily Note",
            "Occupational Therapy Progress Notes",
            "PT Initial Report",
            "PT Progress",
            "PT Daily",
            "Acupuncture Daily",
            "Daily Encounter",
            "SOAP Notes",
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
        # D-04: an unqualified "Supplemental Report" used to sit under category 1 as well. It resolves
        # here, because a supplemental that answers a prior evaluation is medico-legal work whatever
        # its header says; a treating supplemental names its subject (e.g. pain management).
        "Supplemental reports from a QME (Qualified Medical Evaluator) or AME (Agreed Medical "
        "Evaluator) - follow-ups to a prior medical-legal evaluation. A report headed only "
        '"Supplemental Report" that responds to a prior medical-legal evaluation, an attorney letter, '
        "or newly served records belongs here.",
        (
            "QME/AME Supplemental Reports",
            "QME Supplemental Report",
            "AME Supplemental Report",
            "Supplemental Reports",
            "Supplemental Report",
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
        "Laboratory and specimen test results",
        # D-01/D-02: the mirror of category 3. The old examples ("Results", "Test Results") were broad
        # enough to attract imaging reports, which is half of why lab work and studies were confused;
        # they are replaced by specimen-named titles.
        "Results of a test run on a SPECIMEN taken from the body: blood panels, urinalysis, cultures, "
        "and toxicology or drug screens. The document reports measured values, often against reference "
        "ranges, rather than an image. A study performed on the body itself - X-Ray, MRI, CT, "
        "ultrasound, EMG/NCS, ECG - is a diagnostic study and belongs to that category, not here.",
        (
            "Laboratory Results",
            "Laboratory Report",
            "Laboratory Test Results",
            "Blood Test Results",
            "Complete Blood Count",
            "Comprehensive Metabolic Panel",
            "Urinalysis",
            "Urine Toxicology Screen",
            "Toxicology Report",
            "Culture and Sensitivity",
        ),
    ),
    "15": Category(
        "15",
        "Utilization review and independent medical review (UR/IMR)",
        # The embedding and LLM stages read this text, so the last sentence is load-bearing: this
        # category and 10 are the two halves of one exchange, and the classifier was answering 10
        # for these twelve times. The distinction is WHO WROTE IT and WHICH DIRECTION it runs - the
        # treating physician asking (10) versus a reviewer for the claims administrator answering.
        "A determination on whether requested treatment is medically necessary, written by a "
        "reviewing physician for the claims administrator rather than by the treating physician: "
        "utilization review (UR) decisions certifying, modifying, or denying a request, and "
        "independent medical review (IMR) determinations deciding an appeal against a UR denial. "
        "The treating physician's own REQUEST for that treatment is a Request For Authorization and "
        "belongs to that category, not here - this category holds the ANSWER to it.",
        (
            "Utilization Review Determination",
            "Utilization Review Letter",
            "Utilization Review - Non-Certification",
            "Utilization Review - Modification",
            "Independent Medical Review Determination",
            "IMR Final Determination Letter",
        ),
    ),
    "100": Category(
        "100",
        "General or uncategorized documents",
        # The embedding and LLM stages read this text, so it names what actually lands here: the
        # administrative paperwork around a record. A bare "everything else" gave the classifier
        # nothing to match on, which is why correspondence kept landing in clinical categories.
        "Administrative, correspondence and other documents that do not fit a specific clinical "
        "category: in-house routing slips, cover letters, emails and faxes, legal declarations, "
        "proofs of service, records requests and record indexes.",
        (
            "Medical Records Routing Sheet",
            "Email - Evaluation Cover Letter",
            "Declaration of Compliance",
            "Proof of Service",
            "Schedule of Records",
            "Medical Evaluation Request",
        ),
    ),
}

# Valid classification outputs (strings, matching the CSV category column).
ALLOWED_IDS: tuple[str, ...] = tuple(CATEGORIES.keys())

# The fallback bucket when no category can be determined.
DEFAULT_ID = "100"
