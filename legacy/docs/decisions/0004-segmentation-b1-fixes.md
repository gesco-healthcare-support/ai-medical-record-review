# ADR-0004: Fix Gemini segmentation (B1)

**Status:** Accepted

## Context
The abandoned Gemini segmentation underperformed manual CSVs. Investigation found three
implementation defects, not a model limitation: `temperature=1.5` (encourages hallucinated
page numbers), a malformed/self-contradictory in-prompt JSON example (two conflicting key
schemas + invalid JSON), and a fragile parser (`item["id"]`) that KeyError-aborted the whole
batch.

## Decision
Set `temperature=0.0` and `response_mime_type="application/json"`; replace the example with a
single consistent valid-JSON schema in a shared `SEGMENTATION_PROMPT`; add a tolerant
`parse_segment_item()` that handles key aliases and skips malformed elements instead of
crashing.

## Alternatives
- Abandon Gemini and only support manual CSVs - rejected; the defects were fixable.
- Also fix chunk-boundary splitting now - deferred (separate, larger problem; see
  `experiments/a1-segmentation/`).

## Consequences
- Verified: 100% ground-truth boundary recall on a real 227-page sample.
- Categorization still routes many segments to bucket 100 - that is a separate fuzzy-match
  limitation, addressed by the B5/B6 work, not this ADR.
