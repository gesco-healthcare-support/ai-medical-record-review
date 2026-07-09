# Boundary signals beyond the segmenter LLM (research, 2026-07-04)

Goal: independent signals that make the focused-verification pass TARGETED - flagging
suspicious LLM boundaries (split detection) and suspicious span interiors (MERGE
detection, the worst error class, which currently has no detector). Production
constraint: must work on unseen PDFs with no ground truth, PHI-safe (local
computation preferred).

The field is called Page Stream Segmentation (PSS) / document separation. Landscape:

## A. Local structural signals from data we ALREADY have (build first, ~free)

The OCR text cache and per-page byte sizes already exist for every case; pHash needs
one cheap render pass. Per-signal cost at inference: negligible.

1. **"Page N of M" span validation** (upgrade of the existing page-number cue).
   Today `cues.py` uses printed page numbers only as boundary seeds. As VALIDATORS
   they are stronger: a page printing "3 of 7" contradicts any boundary in the next
   4 pages (split suspect); a "1 of N" printed INSIDE an LLM span contradicts the
   span (merge suspect). Deterministic, explainable.
2. **Fax-banner fingerprints.** WC records are full of fax transmission banners
   (timestamp + sender + page x/y at the page top). Pages sharing one banner run
   came in one transmission (almost always one document); a banner change inside an
   LLM span is a merge suspect. Regex + fuzzy match on the top OCR lines.
3. **Header/footer fingerprint change.** Provider name/address lines repeat within a
   document; a change across consecutive pages is a boundary candidate. (This is a
   targeted structural cue, unlike the failed bag-of-words classifiers.)
4. **Per-page date continuity.** Encounter dates extracted per page: a date change
   across pages = boundary candidate; the same date flowing across an LLM boundary =
   split suspect.
5. **Page byte-density / scan-stats jumps.** Different source documents were scanned
   on different devices/settings; per-page byte size (already computed for chunking)
   jumps at many source transitions.
6. **Visual page similarity (perceptual hash or layout descriptors).** pHash/dHash on
   the 150-200 DPI renders; Hamming distance between consecutive pages. High
   similarity across an LLM boundary = split suspect; a dissimilarity spike inside a
   span = merge suspect. Literature supports layout-visual similarity for PSS
   (Rusinol et al., digital-mailroom PSS; layout-saliency page similarity).

Combination: a weighted suspicion score per boundary and per span-interior page;
the top-scoring suspects go to the adjacent-oracle verification pass (already built,
recall-safe, ~$0.0002/check). Signals also become HUMAN-facing review flags.

## B. Trainable local PSS models (the literature's answer; needs labeled data)

- Multimodal (image + OCR text) CNN/transformer page-pair classifiers are the
  established approach; reported accuracy up to ~93% on public benchmarks - and
  text+image consistently beats text-only (our text-only attempt was at chance,
  consistent with the literature's finding that the visual channel carries the cue).
- 2024-2025 work: fine-tuned decoder LLMs beat smaller multimodal encoders
  (arXiv 2408.11981, TABME++ benchmark; OCR quality is the binding constraint);
  benchmarks: OpenPSS (TPDL 2024), DocSplit (arXiv 2602.15958), TABME.
- Fit for us: the data flywheel (save every job's corrected CSV) produces exactly the
  labels this needs. Revisit when dozens-of-cases scale is reached.

## C. Rented purpose-built splitters (benchmarkable, per-page fees)

- Azure AI Document Intelligence (custom classifier w/ splitting, ~$3/1k pages) and
  Google Document AI splitter ($5/1k pages, 1000-page/file cap) - unmeasured on our
  documents; scoring them on the labeled cases costs a few dollars.
- AWS has offerings in the same space (Bedrock Data Automation / Textract-based IDP)
  - not yet verified in detail.

## D. Buy-the-whole-workflow vendors (business context, not a signal)

A mature SaaS market does medical-record sorting/splitting/chronology end-to-end with
BAAs: Wisedocs, DigitalOwl, SiftMed, InQuery, Parambil, Filevine MedChron, etc.
MRR AI is the in-house version of these; their existence is build-vs-buy context for
the owner, not something to bolt onto the pipeline.

## Recommended order

1. Tier 1 = section A signal library + suspicion scoring, evaluated per-signal against
   the clean-gold cases (which signal actually correlates with real errors), then
   wired into the verify pass for BOTH split and merge suspects.
2. Tier 2 = merge-detector verification live (first-ever detector for the worst class).
3. Tier 3 = score Azure/Google splitters on labeled cases; revisit trainable PSS
   models once the corrected-CSV flywheel accumulates labels.

## Sources

- LLMs for PSS + TABME++: https://arxiv.org/abs/2408.11981
- PSS with CNNs (image+text, ~93%): https://arxiv.org/pdf/1710.03006
- Multimodal PSS (CNNs, Springer LRE): https://link.springer.com/article/10.1007/s10579-019-09476-2
- OpenPSS benchmark (TPDL 2024) and DocSplit benchmark: https://arxiv.org/html/2602.15958v1
- Interpage relations survey: https://arxiv.org/pdf/2205.13530
- Digital-mailroom PSS (similarity clustering incl. page numbers/headers/layout):
  https://www.researchgate.net/publication/261127088
- Layout-saliency page similarity: https://www.researchgate.net/publication/220861993
- Hash-based document separation (patent): https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9233399
- Vendor landscape: https://www.wisedocs.ai/ ; https://www.tavrn.ai/blog/medical-chronology-software
