"""The #153 A/B harness: the properties that make its NULL arm a valid control.

The harness itself makes Vertex calls and is run by hand. What is testable, and worth testing, is
the thing that decides whether its answer means anything: `NULL_INSTRUCTIONS` is only a control if
it matches `CREDENTIAL_INSTRUCTIONS` in volume while carrying none of its content. Both halves can
be broken by an ordinary edit - somebody tightening the prose, or reaching for a concrete example -
and neither break would be visible in the harness's output. It would just quietly stop answering
the question.
"""

import importlib.util
import pathlib
import re

import pytest

_HARNESS = (
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "eval" / "classify_prompt_ab.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("classify_prompt_ab", _HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ab():
    return _load()


def test_the_null_arm_matches_the_credential_arm_in_volume(ab):
    """The control isolates CONTENT, so its volume has to match.

    If B-null were half the length, "B-null does not erode the catch-all" would be explained by it
    being shorter rather than by it saying nothing about categories, and the arm would answer a
    question nobody asked. 10% either way is tight enough to keep the comparison honest and loose
    enough that a wording fix does not fail the suite.
    """
    credential, null = ab.CREDENTIAL_INSTRUCTIONS, ab.NULL_INSTRUCTIONS
    chars = len(null) / len(credential)
    words = len(null.split()) / len(credential.split())
    assert 0.9 <= chars <= 1.1, f"null arm is {chars:.2f}x the credential arm in characters"
    assert 0.9 <= words <= 1.1, f"null arm is {words:.2f}x the credential arm in words"
    assert null.count("\n") == credential.count("\n"), "same number of instruction lines"


def test_the_null_arm_names_no_category(ab):
    """The falsifiable line. A control that names even one category id is not a control - it is a
    second treatment, and any movement it produces is unattributable."""
    ids = set(re.findall(r"\b(?:100|1[0-5]|[1-9])\b", ab.NULL_INSTRUCTIONS))
    assert not ids, f"the null arm names category ids {sorted(ids)}"


def test_the_null_arm_names_no_document_or_clinical_signal(ab):
    """Weaker than the id check and worth having anyway: naming a document TYPE would give the model
    something to act on even without an id, which is exactly the mechanism under test - arm B's
    damage lands in categories its text never mentions, so mentioning any is disqualifying."""
    banned = (
        "encounter",
        "progress note",
        "evaluation",
        "credential",
        "author",
        "chiro",
        "acupunctur",
        "physical therapy",
        "imaging",
        "deposition",
        "laborator",
        "administrative",
        "cover letter",
        "qme",
        "ame",
    )
    lowered = ab.NULL_INSTRUCTIONS.lower()
    named = [term for term in banned if term in lowered]
    assert not named, f"the null arm names {named}"


def test_each_arm_sends_the_instruction_block_it_claims_to(ab, monkeypatch):
    """The dispatcher is a chain of string comparisons and a fall-through `return`, so a new arm
    added above the wrong line silently runs someone else's prompt."""
    sent = {}
    monkeypatch.setattr(
        ab, "call", lambda text, instructions, model: sent.setdefault(text, instructions)
    )
    monkeypatch.setattr(ab, "llm_classify", lambda text, model=None: "A-was-called")

    assert ab.run_arm("A", "a", "m") == "A-was-called"
    ab.run_arm("A-prime", "ap", "m")
    ab.run_arm("B", "b", "m")
    ab.run_arm("B-null", "bn", "m")

    assert sent["ap"] == ab.PRODUCTION_INSTRUCTIONS
    assert ab.CREDENTIAL_INSTRUCTIONS in sent["b"]
    assert ab.NULL_INSTRUCTIONS not in sent["b"]
    assert ab.NULL_INSTRUCTIONS in sent["bn"]
    assert ab.CREDENTIAL_INSTRUCTIONS not in sent["bn"]
    # Every arm but A carries production's block, or it is not measuring an ADDITION to it.
    for key in ("ap", "b", "bn"):
        assert sent[key].startswith(ab.PRODUCTION_INSTRUCTIONS)


def test_the_score_reports_the_catch_all_rate(ab):
    """@adrian-g, on the arm-B damage: "Any future A/B on this call should measure the 100 rate."

    It is the axis the null arm exists to compare, so a score that omits it cannot answer the
    question the arm was added for.
    """
    results = [
        {"reviewer_said": "1", "model_said": "5", "answers": {a: ["100"] for a in ab.ARMS}},
        {"reviewer_said": "1", "model_said": "5", "answers": {a: ["1"] for a in ab.ARMS}},
    ]
    out = ab.score(results)
    assert out["rows"] == 2
    for arm in ab.ARMS:
        assert out[arm]["chose_100"] == 1, f"{arm} did not report its catch-all rate"


def test_the_null_arm_is_in_the_arm_list(ab):
    """`score` iterates ARMS, so an arm that runs but is not listed produces no numbers at all."""
    assert "B-null" in ab.ARMS
