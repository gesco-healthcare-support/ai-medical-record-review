This is a deep, multi-source research task with verification requirements — exactly what the deep-research skill is for. Invoking it now.

The deep-research workflow is now running in the background (run ID `wf_03c56cc0-c81`). Here's what it's doing with your brief:

1. **Scope** — decomposing the nine problem areas (A1-C1) into parallel search angles, weighted toward accuracy (A1 page-stream segmentation, A2 classification, A3 medical OCR/document-AI, A4 faithful summarization).
2. **Search** — parallel web-search agents, one per angle.
3. **Fetch** — deduplicating URLs and pulling the top sources (PyPI, GitHub, papers, official docs) to extract falsifiable claims — existence, maintenance signals, benchmarks, BAA/self-hosting posture.
4. **Verify** — adversarial 3-vote verification per claim, so hallucinated packages and marketing-only claims get killed before they reach the report.
5. **Synthesize** — merge, rank by confidence, cite.

When it completes I'll assemble the final deliverable in your requested structure: executive summary, per-problem sections using the solution-entry template, the accuracy-first "try first" shortlist, and open questions/risks. Anything the workflow leaves thin (e.g., a problem area with fewer than 2 verified approaches) I'll backfill with targeted follow-up searches before writing the report.

The workflow finished — strong verified findings for A1, A3, and A4, but it explicitly reports no verified findings yet for A2, R1, R2, L1, S1, and C1. Let me read the full output, then run a second pass to cover the gaps.

Pass 2 is running in the background (run ID `wf_8f0cfbf9-df3`). Quick status on where things stand:

**Pass 1 (complete) — verified findings for the top accuracy problems:**
- **A1 (boundaries):** This is a named research field — Page Stream Segmentation (PSS). The consensus root-cause fix is a per-page binary "start of document" classifier (text + image features); clustering approaches verifiably lose, and your fixed 100-page chunking is confirmed as a symptom patch. Public benchmarks (OpenPSS, ~141k pages; Tobacco800) exist to measure any replacement. Caveat: text-only models transfer better across domains than image models, and no public corpus resembles medical records.
- **A3 (extraction):** Verified self-hostable Tesseract replacements: olmOCR (Allen AI, Apache 2.0, 17.4k stars, explicit tables/handwriting support, needs a 12+ GB NVIDIA GPU) and the OmniDocBench (CVPR 2025) leaderboard where small open-weight document VLMs (MinerU2.5-Pro 95.75, PaddleOCR-VL 94.93) beat Gemini 3 Pro (92.91) at page parsing.
- **A4 (faithful summarization):** A peer-reviewed clinical baseline (npj Digital Medicine, 2025): GPT-4 hallucinates in 1.47% of sentences but **omits at 3.45%** — omissions are the dominant failure, so faithfulness checking must measure coverage, not just fabrication. Prompt iteration alone, measured against a clinician-annotated error taxonomy, cut major hallucinations 75%.

**Pass 2 (running)** covers the verified-findings gaps the first pass explicitly flagged: A2 classification, R1 structured output, R2 coverage verification, L1 parallelism/batching, S1 job state/queues, C1 cost, plus cloud/BAA options for A3 (since all pass-1 A3 winners require local GPUs).

When it completes I'll synthesize both passes into the full deliverable: executive summary, per-problem sections in your entry template, the accuracy-first "try first" shortlist, and open questions/risks.

Pass 2 is done. Reading the full output before synthesizing the final report.

# MRR Pipeline Improvement Research Report

All claims below were verified against primary sources (PyPI JSON API, GitHub API, official vendor docs, arXiv/published papers) on **2026-06-12** by a two-pass adversarial-verification workflow (143 agents total; every surviving claim won a 3-0 verification vote or was corrected with the observed value). Time-sensitive claims carry that date.

---

## 1. Executive Summary (highest-leverage findings, priority order)

1. **(A1) Your boundary problem is a named, well-studied field: Page Stream Segmentation (PSS).** The consensus root-cause architecture is a **per-page binary "start of document" classifier** over text and/or image features -- not chunking, not clustering (clustering verifiably loses; MDPI Algorithms 2023). Fixed 100-page chunks are a symptom patch to retire. Public benchmarks (OpenPSS, ~141k pages; Tobacco800) let you measure any replacement. No production-grade OSS exists (the GitHub `page-stream-segmentation` topic contains exactly 1 repo, verified 2026-06-12) -- but the technique is small enough to build in-house.

2. **(Compliance -- found during verification, affects the pipeline TODAY) The Gemini Developer API (`ai.google.dev` keys) is NOT covered by a Google Cloud BAA.** Google's HIPAA covered-products list does not include the Gemini Developer API or AI Studio. If the segmentation step sends PHI chunks to Gemini via an `ai.google.dev` API key, that traffic is outside BAA coverage right now. The BAA-covered path is the same models via **Gemini Enterprise Agent Platform** (Vertex AI's new name as of Cloud Next, announced 2026-04-22; endpoint `aiplatform.googleapis.com` unchanged). Similarly: **OpenAI's Healthcare Addendum restricts PHI to ZDR-eligible endpoints; `/v1/batches` and `/v1/files` are explicitly ZDR-ineligible** -- so the Batch API's 50% discount is unusable for PHI.

3. **(A3) Tesseract has a verified replacement class: vision-language OCR.** Self-hosted: olmOCR (Apache-2.0, 17.4k stars, explicit tables/handwriting support) -- needs a 12+ GB NVIDIA GPU. On the CVPR 2025 OmniDocBench leaderboard, sub-2B open-weight document VLMs (MinerU2.5-Pro 95.75, PaddleOCR-VL-1.5 94.93) beat Gemini 3 Pro (92.91). Under your existing Google BAA: **Google Cloud Document AI** is on the HIPAA covered list with documented checkbox ("selection mark") and handwriting extraction -- the lowest-friction fix for exactly the content Tesseract loses.

4. **(A2) Reframe your hand-curated title lists as labeled training data.** A ~30-line classifier (sentence-transformers embeddings + scikit-learn LogisticRegression + CalibratedClassifierCV) produces honest probabilities, with the abstain-to-human threshold chosen from a reliability diagram instead of a hardcoded 0.65. RapidFuzz `token_set_ratio` is a same-day symptom patch that fixes the word-order failures of difflib while you validate the classifier.

5. **(A4) Omissions, not hallucinations, are the dominant clinical-summary failure.** Peer-reviewed clinician annotation of GPT-4 (npj Digital Medicine, 2025): hallucinations in 1.47% of generated sentences (44% major) vs omissions at 3.45% of source sentences. Your faithfulness checking must measure coverage, not just fabrication. In the same study, changing *only the prompt* (measured against a clinician-annotated error taxonomy) cut major hallucinations 75%.

6. **(R1) The parse-crash class of bug is eliminable, not just catchable.** OpenAI Structured Outputs with `strict: true` uses constrained decoding (100% schema adherence in OpenAI's eval vs <40% for prompting); Gemini `responseSchema` is the equivalent (via the BAA-covered Cloud endpoint for PHI). One Pydantic model per category should generate the provider schema and validate the response; `json-repair` as last-resort fallback for any non-strict calls.

7. **(R2) Make gaps unrepresentable.** The PSS literature's per-page formulation outputs a sorted list of cut points, so gaps/overlaps cannot exist by construction. Until then, the `portion` interval library implements the full no-gaps/no-overlaps/full-coverage check in ~10 lines, run before any OCR/LLM spend.

8. **(L1/S1/C1) The engineering fixes are standard and low-risk:** async fan-out with the official `AsyncOpenAI` client + `asyncio.Semaphore`; page-level (not sub-document-level) OCR parallelism; a job-row table + RQ or Huey to kill the module-level globals; prompt reordering (static prefix first) to trigger OpenAI's automatic prompt caching (up to 90% input-cost reduction on hits).

---

## 2. Problem Sections

### A1. Document-boundary detection (deepest)

**Approaches considered:**
- **(a) Per-page boundary classification (root cause)** -- classify every page "starts a new document: yes/no" using text and/or image features. State of the art in the literature; gaps unrepresentable by construction.
- **(b) Per-page LLM/decoder classification (root cause, fits existing BAAs)** -- TABME++ (arXiv 2408.11981, 2024) found fine-tuned decoder LLMs now outperform smaller multimodal encoders for PSS; you could prompt Gemini (via the BAA-covered endpoint) per page or small window rather than per 100-page chunk.
- **(c) Overlapping chunks + boundary stitching (symptom patch)** -- keep the current design but overlap chunk windows and reconcile; cheaper, still mislocates boundaries.

Key evidence (all 3-0 verified): multimodal (image+text) wins in-distribution -- Wiedemann & Heyer CNN 0.929/0.911 accuracy ([arXiv:1710.03006](https://arxiv.org/abs/1710.03006)); BERT+EfficientNet late ensemble is OpenPSS SOTA (page F1 0.83 LONG / 0.76 SHORT); **but text-only models transfer far better across domains** (robust task: TEXT-CNN Weighted Doc F1 0.53 vs 0.25 for the image model). Since no public corpus resembles medical records, start text-first. Agglomerative clustering of page embeddings verifiably fails to beat per-page classification ([MDPI Algorithms 16(5):259](https://www.mdpi.com/1999-4893/16/5/259)).

**Name + link:** OpenPSS benchmark + datasets ([paper PDF](https://irlab.science.uva.nl/wp-content/papercite-data/pdf/heus_open24.pdf), [github.com/irlabamsterdam/OpenPSSbenchmark](https://github.com/irlabamsterdam/OpenPSSbenchmark))
**What it does:** Two public PSS datasets (~141k pages / ~32k documents, Dutch FOIA releases) with page images, Tesseract-5 text, and reproduction code -- an off-the-shelf eval harness for any A1 candidate.
**Targets:** A1
**Root cause or symptom:** root cause (enables measuring the root-cause fix)
**Data-flow posture:** SELF-HOSTABLE (data + code)
**Language/stack:** Python/Jupyter
**Evidence of better accuracy:** TPDL 2024 (LNCS 15177) peer-reviewed; table counts verified verbatim.
**Maintenance:** research code -- created 2022-10-06, last push 2024-05-08, 2 stars. Eval harness and data source, not a production dependency.
**Integration effort for Python/Flask:** medium -- offline evaluation only, never in the request path.
**Idea to borrow even if not adopted:** evaluate with PSS metrics (per-page boundary F1 + weighted document F1), not "did the LLM JSON look right".

**Name + link:** agiagoulas/page-stream-segmentation ([github.com/agiagoulas/page-stream-segmentation](https://github.com/agiagoulas/page-stream-segmentation), BERT weights: [huggingface.co/agiagoulas/bert-pss](https://huggingface.co/agiagoulas/bert-pss))
**What it does:** Reference implementation of image-CNN + BERT-text per-page PSS trained on Tobacco800; self-reports 0.942 accuracy / 0.879 Kappa with current+previous page input.
**Targets:** A1
**Root cause or symptom:** root cause
**Data-flow posture:** SELF-HOSTABLE (MIT)
**Language/stack:** Python (Jupyter-heavy)
**Evidence of better accuracy:** self-reported benchmark on Tobacco800 from a 7-star repo -- treat as indicative only.
**Maintenance:** stale -- 7 stars, last push 2021-05-10 (~5 years). Idea source / starting code, not a dependency.
**Integration effort for Python/Flask:** high if adopted as-is; low as a design template.
**Idea to borrow even if not adopted:** feed the classifier the current AND previous page -- boundary detection is inherently pairwise.

**Name + link:** DiT first-page image classifier ([huggingface.co/microsoft/dit-base-finetuned-rvlcdip](https://huggingface.co/microsoft/dit-base-finetuned-rvlcdip), paper [arXiv:2203.02378](https://arxiv.org/abs/2203.02378))
**What it does:** Document-image transformer fine-tuned on RVL-CDIP (400k images / 16 classes); classifies a page image with no dependence on title text. A title-independent second signal for both boundary detection and categorization.
**Targets:** A1, A2
**Root cause or symptom:** root cause (as a feature source)
**Data-flow posture:** SELF-HOSTABLE
**Language/stack:** Python (transformers)
**Evidence of better accuracy:** model card lists no accuracy numbers; RVL-CDIP's 16 generic classes do not map to your 14 -- needs fine-tuning on your own first pages.
**Maintenance:** static artifact; 23,728 downloads/month (2026-06-12).
**Integration effort for Python/Flask:** medium (fine-tune + serve).
**Idea to borrow even if not adopted:** "page looks like a new form/letter/report" is a visual property -- a layout-image signal catches boundaries where titles are missing entirely.

Also verified as an idea source: CoSMo multimodal PSS transformer, 98.10 F1-Macro but on the authors' own comic-book corpus, fully supervised ([github.com/mserra0/CoSMo-ComicsPSS](https://github.com/mserra0/CoSMo-ComicsPSS), MIT, 4 stars, last commit 2025-06-30; [arXiv:2507.10053](https://arxiv.org/html/2507.10053)).

**Honest conclusion for A1:** no strong production-grade OSS exists. The best path is a small in-house per-page classifier (text-first, image later), evaluated on OpenPSS + a labeled QME/AME set you create. That labeled set is the real prerequisite.

---

### A2. Document-type classification (~14 labels)

**Approaches considered:** (a) calibrated classical classifier over embeddings (root cause, lowest ops); (b) few-shot fine-tuned SetFit (root cause); (c) zero-shot NLI (root cause, zero training); (d) LLM with enum-constrained structured output (root cause, no new infra); (e) RapidFuzz (explicit symptom patch). Only (a) -- and (b) via its LogisticRegression head -- emit principled probabilities out of the box; NLI scores and LLM logprobs are rank-useful but uncalibrated.

**Name + link:** sentence-transformers + scikit-learn CalibratedClassifierCV ([calibration docs](https://scikit-learn.org/stable/modules/calibration.html), [pypi.org/project/sentence-transformers](https://pypi.org/project/sentence-transformers/))
**What it does:** Embed each title locally, train LogisticRegression on your curated examples, wrap in CalibratedClassifierCV; `predict_proba` returns honest probabilities and low-confidence titles route to human review.
**Targets:** A2
**Root cause or symptom:** root cause
**Data-flow posture:** SELF-HOSTABLE (BSD-3/Apache-2.0; CPU-only)
**Language/stack:** Python
**Evidence of better accuracy:** official scikit-learn 1.9 docs: sigmoid/Platt for small samples, isotonic for >~1000, reliability diagrams + Brier score for evaluation; LogisticRegression well-calibrated by construction. (Domain accuracy must come from your own holdout.)
**Maintenance:** scikit-learn 1.9.0 released 2026-06-02, 66,305 stars; sentence-transformers v5.5.1 released 2026-05-20, 18,800 stars, both pushed this week. Pin `scikit-learn>=1.9` if using temperature scaling.
**Integration effort for Python/Flask:** low -- ~30 lines, trains in seconds on CPU, trivially unit-testable.
**Idea to borrow even if not adopted:** the evaluation protocol -- hold out a few hundred real titles, plot a reliability diagram, and pick the abstention threshold from the curve instead of hardcoding 0.65. This is the actual fix for the stated problem.

**Name + link:** SetFit ([github.com/huggingface/setfit](https://github.com/huggingface/setfit), paper [arXiv:2209.11055](https://arxiv.org/abs/2209.11055))
**What it does:** Few-shot contrastive fine-tuning of a small sentence-transformer + LogisticRegression head; your example-title lists become the training set (~8+ examples/label is the design point).
**Targets:** A2
**Root cause or symptom:** root cause
**Data-flow posture:** SELF-HOSTABLE (Apache-2.0; pre-download HF weights for air-gapped use)
**Language/stack:** Python
**Evidence of better accuracy:** author-published paper (HF + Intel Labs): with 8 examples/class, competitive with RoBERTa-Large fine-tuned on 3,000 examples. Self-published but reproducible; stronger than marketing.
**Maintenance:** PyPI v1.1.3 (2025-08-05, ~10 months old) but main pushed 2026-05-26; 2,746 stars, ~220k downloads/month.
**Integration effort for Python/Flask:** low-medium -- minutes to train on CPU; build a labeled holdout first.
**Idea to borrow even if not adopted:** reframe curated example lists as labeled training data rather than fuzzy-match targets -- applies to every option here.

**Name + link:** Zero-shot NLI classifier ([huggingface.co/MoritzLaurer/deberta-v3-large-zeroshot-v2.0-c](https://huggingface.co/MoritzLaurer/deberta-v3-large-zeroshot-v2.0-c))
**What it does:** Zero-shot classification via entailment: title + 14 labels phrased as hypotheses through `pipeline("zero-shot-classification")`; no training data at all.
**Targets:** A2
**Root cause or symptom:** root cause
**Data-flow posture:** SELF-HOSTABLE (use the `-c` variant: trained only on commercially-friendly data, MIT)
**Language/stack:** Python
**Evidence of better accuracy:** author's 28-dataset benchmark ([arXiv:2312.17543](https://arxiv.org/abs/2312.17543)): `-c` variant 0.676 mean f1_macro vs facebook/bart-large-mnli 0.497 (~36% better). Self-published. Scores are entailment softmax, NOT calibrated -- calibrate post-hoc or treat as rank-only.
**Maintenance:** static 2024 model artifact; 51.5k downloads/month for the family; rides on actively-maintained `transformers`.
**Integration effort for Python/Flask:** low (~10 lines), but ~435M params -- slower than the sklearn option.
**Idea to borrow even if not adopted:** hypothesis-template phrasing -- describe each label as a sentence ("a physical therapy progress note"), which also improves the LLM option.

**Name + link:** LLM enum-constrained classification ([OpenAI Structured Outputs docs](https://developers.openai.com/api/docs/guides/structured-outputs); Gemini equivalent [structured output docs](https://ai.google.dev/gemini-api/docs/structured-output))
**What it does:** One cheap structured call with the label field as a 14-value enum; an invalid label is impossible at the decoding level. Can be folded into an existing call's schema.
**Targets:** A2 (also R1)
**Root cause or symptom:** root cause
**Data-flow posture:** CLOUD (OpenAI -- BAA yes, ZDR-eligible endpoints; Google -- yes ONLY via Gemini Enterprise Agent Platform endpoint, NOT ai.google.dev)
**Language/stack:** Python
**Evidence of better accuracy:** mechanism documented officially ("will always generate responses that adhere to your supplied JSON Schema"); no published enum-accuracy benchmark on medical titles -- run your own holdout.
**Maintenance:** GA vendor features, docs current 2026-06-12.
**Integration effort for Python/Flask:** low -- app already calls OpenAI.
**Idea to borrow even if not adopted:** never let a free-text string become a routing label; validate against the closed 14-value set at the boundary.

**Name + link:** RapidFuzz ([pypi.org/project/RapidFuzz](https://pypi.org/project/RapidFuzz/))
**What it does:** C++-backed fuzzy matching; `token_set_ratio` handles word-order/subset variation ("Lumbar MRI" vs "MRI Lumbar Spine 04/12/2024") that character-level SequenceMatcher misses.
**Targets:** A2
**Root cause or symptom:** symptom -- scores are still uncalibrated lexical similarity; the brittle-threshold problem persists, just fires less often.
**Data-flow posture:** SELF-HOSTABLE (MIT)
**Language/stack:** Python
**Evidence of better accuracy:** self-published speed benchmarks vs FuzzyWuzzy; no accuracy benchmark vs ML classifiers exists.
**Maintenance:** v3.14.5 (2026-04-07), pushed 2026-06-08, 3,951 stars, ~159M downloads/month.
**Integration effort for Python/Flask:** low -- hours; ideal stopgap while the classifier is validated.
**Idea to borrow even if not adopted:** normalization discipline (lowercase, strip dates/punctuation before comparison) helps every approach above.

Verified-dead, do not adopt: `fuzzywuzzy` (last release 2020-02-13, GPLv2 -- superseded by RapidFuzz).

---

### A3. High-fidelity extraction from medical pages

**Approaches considered:** (a) self-hosted vision-language OCR (root cause; GPU required); (b) BAA-covered cloud document AI (root cause; no GPU, per-page cost); (c) direct multimodal LLM page reading under existing BAAs (root cause; merges OCR+understanding); (d) keep Tesseract but route only failing pages to a better engine (cost-optimized hybrid).

**Name + link:** olmOCR ([github.com/allenai/olmocr](https://github.com/allenai/olmocr))
**What it does:** Converts PDF/PNG/JPEG to clean Markdown via a fine-tuned Qwen2.5-VL-7B; README explicitly claims support for "equations, tables, handwriting, and complex formatting".
**Targets:** A3
**Root cause or symptom:** root cause
**Data-flow posture:** SELF-HOSTABLE (Apache-2.0, open weights; PHI never leaves) -- hard prerequisite: NVIDIA GPU with 12+ GB VRAM, ~30 GB disk, vLLM.
**Language/stack:** Python
**Evidence of better accuracy:** olmOCR-Bench 82.4 +/- 1.1 vs Marker 76.1, MinerU 75.2, Mistral OCR API 72.0. Caveats: benchmark is self-published by Ai2 and shares a framework with the model's RL reward (self-alignment); independent F22 Labs testing found handwriting "inconsistent" (~85-90% vs 98-99% printed); checkbox support not explicitly claimed. Materially better than Tesseract, not solved.
**Maintenance:** 17.4k stars, v0.4.27 released 2026-03-12 -- actively maintained.
**Integration effort for Python/Flask:** medium-high -- GPU provisioning is the real cost; the API is OpenAI-compatible once served.
**Idea to borrow even if not adopted:** OCR-to-Markdown as the extraction contract (tables preserved as structure, not garbled text) before the summarizer.

**Name + link:** OmniDocBench + its leaderboard leaders ([github.com/opendatalab/OmniDocBench](https://github.com/opendatalab/OmniDocBench), paper [arXiv:2412.07626](https://arxiv.org/abs/2412.07626), CVPR 2025)
**What it does:** 1,651-page document-parsing benchmark (10 doc types, 5 layouts, 5 languages) with a quantitative Overall score (text edit distance + table TEDS + formula CDM). Leaderboard v1.6_full (2026-04-30): MinerU2.5-Pro (1.2B) 95.75, PaddleOCR-VL-1.5 (0.9B) 94.93, Gemini 3 Pro 92.91, Marker 78.44.
**Targets:** A3 (tool selection + measurement)
**Root cause or symptom:** root cause (replaces vendor claims with measurement)
**Data-flow posture:** SELF-HOSTABLE benchmark; the two leaders ship open weights on HuggingFace with vLLM paths (SELF-HOSTABLE). Note: I verified the leaderboard numbers and the leaders' arXiv papers ([PaddleOCR-VL-1.5](https://arxiv.org/abs/2601.21957) by Baidu, [MinerU2.5-Pro](https://arxiv.org/abs/2604.04771)), but did not individually verify those two repos' star/release signals -- check before adopting.
**Language/stack:** Python
**Evidence of better accuracy:** CVPR 2025 peer-reviewed benchmark; Baidu's independent result partially offsets the conflict that OpenDataLab maintains both the benchmark and MinerU. No medical document type in the benchmark.
**Maintenance:** 1.8k stars, v1.7 released 2026-04-30.
**Integration effort for Python/Flask:** medium (offline eval); leaders deployable like olmOCR.
**Idea to borrow even if not adopted:** build a small internal eval set of synthetic medical pages and score candidate extractors with edit-distance + TEDS before any swap decision.

**Name + link:** Google Cloud Document AI -- Enterprise Document OCR + Form Parser ([form-parser docs](https://docs.cloud.google.com/document-ai/docs/form-parser), Python client [google-cloud-documentai](https://pypi.org/project/google-cloud-documentai/))
**What it does:** Managed document processing: OCR with documented handwriting support; Form Parser extracts key-value pairs, tables, and selection marks (checkboxes) -- exactly the three Tesseract failure modes named in the brief. Google's own quickstart demos a handwritten medical intake form.
**Targets:** A3
**Root cause or symptom:** root cause
**Data-flow posture:** CLOUD (Google; BAA yes -- "Document AI" appears verbatim on the [HIPAA covered-products list](https://cloud.google.com/security/compliance/hipaa), verified 2026-06-12; confirm your existing BAA is the Google *Cloud* BAA, not Workspace)
**Language/stack:** managed REST; official Python client 3.15.0 (2026-06-03), ~2.8M downloads/month
**Evidence of better accuracy:** capability claims are official docs (vendor self-published). In the independent-of-Google OmniAI benchmark (2025-02-20, 1000 docs), Document AI was among traditional providers that VLMs "matched or exceeded" -- good, not leading.
**Maintenance:** GA service; client released 9 days ago; steady cadence.
**Integration effort for Python/Flask:** low-medium -- swap the pytesseract call; 2000-page PDFs require batch processing via GCS (adds a storage step that must be in BAA scope too).
**Idea to borrow even if not adopted:** emit checkbox state as key-value pairs into the per-category summarizer prompts -- claim forms and PR-2-style checklists lose exactly this under Tesseract.

**Name + link:** Gemini multimodal page reading via Gemini Enterprise Agent Platform ([HIPAA covered list](https://cloud.google.com/security/compliance/hipaa))
**What it does:** Send page images directly to Gemini through the Cloud platform endpoint; replaces Tesseract for scanned/handwritten pages in one step. Naming gotcha verified: Vertex AI was renamed "Gemini Enterprise Agent Platform" (announced 2026-04-22, console cutover 2026-05-21); the covered-list entries are now "Gemini Enterprise Agent Platform" and "Generative AI on Gemini Enterprise Agent Platform".
**Targets:** A3 (and the existing A1 segmentation calls)
**Root cause or symptom:** root cause
**Data-flow posture:** CLOUD (Google; BAA yes for the Cloud platform offering; **NO for ai.google.dev / consumer Gemini API**)
**Language/stack:** Python via `google-genai` SDK in Vertex/Agent-Platform mode (the deprecated `vertexai.generative_models` modules are being retired -- use `google-genai`)
**Evidence of better accuracy:** OmniAI benchmark follow-up (2025-04-06): "Gemini 2.0 topped the accuracy chart" on JSON-extraction accuracy across 1000 real docs (open methodology, MIT, [github.com/getomni-ai/benchmark](https://github.com/getomni-ai/benchmark), 633 stars). Caveat: benchmark numbers are Gemini 2.0-era; no independent numbers for current models.
**Maintenance:** GA Google Cloud platform.
**Integration effort for Python/Flask:** medium -- service-account auth + rewriting the OCR stage image-in; team already calls Gemini.
**Idea to borrow even if not adopted:** two-tier page routing -- keep the embedded text layer where the PDF has one; send only scanned/handwritten pages to the VLM.

**Name + link:** OpenAI vision input under the existing BAA ([your-data guide](https://developers.openai.com/api/docs/guides/your-data))
**What it does:** Image inputs on `/v1/chat/completions` / `/v1/responses` (both ZDR-eligible) so the model reads page images directly.
**Targets:** A3
**Root cause or symptom:** root cause
**Data-flow posture:** CLOUD (OpenAI; BAA yes -- confirm ZDR is enabled on your project; caveat: images are scanned for CSAM on submission and flagged images are retained for manual review even under ZDR)
**Language/stack:** Python (existing SDK)
**Evidence of better accuracy:** OmniAI benchmark: GPT-4o ~75% JSON-extraction accuracy -- mid-pack, below Gemini 2.0. No independent numbers for GPT-4.1/o-series vision.
**Maintenance:** GA vendor API.
**Integration effort for Python/Flask:** low -- same vendor/SDK; rasterize pages with the PyMuPDF you already have.
**Idea to borrow even if not adopted:** pin all PHI traffic to ZDR-eligible endpoints with store=false semantics -- audit the existing summarization calls against this today.

Briefly: **Azure AI Document Intelligence** ([overview](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/overview)) is a strong product (handwriting + selection marks, Markdown output mode; Microsoft signs BAAs by default via the DPA) but is a **new third PHI vendor** -- onboarding/procurement dominates the cost. Python SDK `azure-ai-documentintelligence` 1.0.2 (2025-03-27, ~15 months old) while docs stay current (2026-06-04). Only pursue if the Google/OpenAI options disappoint.

---

### A4. Faithful, structured medical summarization + measurement

**Approaches considered:** (a) measure-then-refine loop against an annotated error taxonomy (root cause for prompt-induced errors; verified evidence below); (b) extraction-first summarization -- pull exact values (impairment %, diagnoses, dates) via strict structured outputs (R1) and have the narrative reference the extracted fields, so exact values are deterministic, with code-level checks that every extracted value appears in the source text (root cause for value drift); (c) automated faithfulness scoring (NLI/QA-based) -- candidates exist but none survived verification this round (see honest-finding note).

**Name + link:** Clinician-annotated faithfulness baseline -- Asgari et al., npj Digital Medicine 8:274 ([nature.com/articles/s41746-025-01670-7](https://www.nature.com/articles/s41746-025-01670-7), 2025-05-13)
**What it does:** Sentence-level clinician annotation (50 physicians, >=2 reviewers/pair, 450 transcript-note pairs) of GPT-4 clinical summaries; defines a hallucination/omission x major/minor error taxonomy.
**Targets:** A4
**Root cause or symptom:** root cause (defines what to measure)
**Data-flow posture:** n/a (methodology); reproducible in-house on synthetic records
**Language/stack:** paper (methodology)
**Evidence of better accuracy:** peer-reviewed: hallucinations 1.47% of generated sentences (44% major); omissions 3.45% of source sentences -- more than 2x. Independently corroborated direction (ED summaries: omissions 47% vs hallucinations 42% of summaries). Caveats: denominators differ; single model; mock primary-care transcripts; authors affiliated with vendor Tortus.
**Maintenance:** n/a (2025 publication)
**Integration effort for Python/Flask:** low-medium -- annotation effort, not code.
**Idea to borrow even if not adopted:** **coverage checking.** A QME summary that omits a prior injury or an impairment rating is the costlier failure mode; any faithfulness gate must verify the source content made it in, not just that nothing was invented.

**Name + link:** Measure-then-refine prompt loop (same paper, Experiments 3 vs 8)
**What it does:** Iterate category prompts against the annotated taxonomy; in the study, changing ONLY the prompt (identical model/seed/temperature) reduced major hallucinations 75% (4 -> 1), major omissions 58% (24 -> 10), minor omissions 35% (114 -> 74).
**Targets:** A4
**Root cause or symptom:** root cause for prompt-induced errors
**Data-flow posture:** CLOUD-compatible with existing OpenAI/Google BAAs; no infra change
**Language/stack:** process + Python eval harness
**Evidence of better accuracy:** verified 3-0 but confidence MEDIUM: tiny counts behind the 75% figure (4 -> 1), no CIs, vendor-affiliated authors. The omission reductions have larger N and are more trustworthy.
**Maintenance:** n/a
**Integration effort for Python/Flask:** low -- build a ~50-document synthetic-record error-annotation set, iterate per-category prompts against it.
**Idea to borrow even if not adopted:** never change a summarization prompt without a before/after error count on the same eval set.

**Honest finding on automated faithfulness scoring:** no off-the-shelf faithfulness scorer survived adversarial verification this round. Ragas's faithfulness metric ([docs.ragas.io](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)) and the Nature Medicine clinical-summarization eval study ([nature.com/articles/s41591-024-02855-5](https://www.nature.com/articles/s41591-024-02855-5)) were both fetched and exist, but their effectiveness claims were not verified -- treat as unverified leads. The best verified alternative is the deterministic subset: extract exact values via strict schemas (R1) and assert string/date/number presence against source text in code, plus the human-annotated loop above.

---

### R1. Reliable structured LLM output

**Approaches:** (a) constrained decoding at the provider (root cause -- invalid tokens cannot be emitted); (b) validate-and-retry in client code (symptom-level but necessary defense); (c) repair-on-failure (last resort).

**Name + link:** OpenAI Structured Outputs, `strict: true` ([docs](https://developers.openai.com/api/docs/guides/structured-outputs))
**What it does:** Converts your JSON Schema into a grammar; the model can only emit schema-valid tokens. Targets the exact calls that crash today.
**Targets:** R1 (also A2, A4)
**Root cause or symptom:** root cause
**Data-flow posture:** CLOUD (OpenAI; BAA yes -- ZDR-eligible endpoints only)
**Language/stack:** Python (existing SDK)
**Evidence of better accuracy:** OpenAI's own eval: 100% on complex schema-following with strict mode vs <40% for gpt-4-0613 prompting (model alone without constrained decoding: 93%). Vendor self-published.
**Maintenance:** GA since Aug 2024; docs current 2026-06-12 (recommends gpt-5.5 for new projects).
**Integration effort for Python/Flask:** low -- `response_format` change + schema definition. Caveats: schema feature subset (`additionalProperties: false` and all-fields-required are mandatory).
**Idea to borrow even if not adopted:** push schema enforcement into generation, not parsing -- the parse-crash bug class disappears instead of being caught.

**Name + link:** Pydantic v2 ([pypi.org/project/pydantic](https://pypi.org/project/pydantic/))
**What it does:** Define each of the ~14 category output shapes once as a BaseModel; `model_validate_json` every response; `model_json_schema()` derives the provider schemas.
**Targets:** R1
**Root cause or symptom:** symptom (validation, not generation) -- but required groundwork for everything else
**Data-flow posture:** SELF-HOSTABLE (local library, MIT)
**Language/stack:** Python
**Evidence of better accuracy:** deterministic validator; ubiquity is the evidence (~1.02B downloads/month).
**Maintenance:** v2.13.4 (2026-05-06), 28,018 stars, pushed 2026-06-11.
**Integration effort for Python/Flask:** low -- do this first regardless of provider feature.
**Idea to borrow even if not adopted:** single source of truth -- one Pydantic model generates the OpenAI strict schema, the Gemini responseSchema, and the runtime validator, so they can never drift.

**Name + link:** Gemini structured output ([ai.google.dev docs](https://ai.google.dev/gemini-api/docs/structured-output); BAA-covered equivalent on the Cloud platform: [control-generated-output](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/control-generated-output))
**What it does:** `responseSchema` + `responseMimeType` constrains generation to your schema; Python SDK accepts Pydantic models directly.
**Targets:** R1
**Root cause or symptom:** root cause
**Data-flow posture:** CLOUD (Google; **BAA only via the Gemini Enterprise Agent Platform endpoint** -- the ai.google.dev surface is refuted for PHI)
**Language/stack:** Python (`google-genai` v2.8.0, 2026-06-03)
**Evidence of better accuracy:** official syntactic-validity guarantee; no published benchmark comparable to OpenAI's eval.
**Maintenance:** docs updated 2026-06-05.
**Integration effort for Python/Flask:** low (config-level), provided calls route through the Cloud endpoint.
**Idea to borrow even if not adopted:** keep output schemas small and flat (deep-nesting rejection is a useful forcing function); pin propertyOrdering for diffable outputs.

**Name + link:** Instructor ([github.com/567-labs/instructor](https://github.com/567-labs/instructor))
**What it does:** Wraps OpenAI/Gemini/other clients to return Pydantic-validated objects; on validation failure it re-prompts WITH the validation error message. One pattern across both BAA'd providers.
**Targets:** R1
**Root cause or symptom:** symptom (validate-and-retry)
**Data-flow posture:** SELF-HOSTABLE library (PHI goes only to your existing providers)
**Language/stack:** Python
**Evidence of better accuracy:** claims only for reliability; adoption is strong: 13,150 stars, ~14M downloads/month. (Correction found in verification: 248 contributors, not the "1,000+" the project markets.)
**Maintenance:** v1.15.1 (2026-04-03), pushed 2026-06-08.
**Integration effort for Python/Flask:** low-medium -- changes the call signature at each summarization site.
**Idea to borrow even if not adopted:** feed the validation error message back into the retry prompt instead of blind re-asking -- cheap, large hit-rate gain, implementable without the library.

**Name + link:** json-repair ([pypi.org/project/json-repair](https://pypi.org/project/json-repair/))
**What it does:** Drop-in `json.loads` replacement that repairs malformed LLM JSON (missing quotes/commas/brackets, truncation) before parsing.
**Targets:** R1
**Root cause or symptom:** symptom (last-resort fallback)
**Data-flow posture:** SELF-HOSTABLE (zero runtime dependencies, in-process)
**Language/stack:** Python (3.10+)
**Evidence of better accuracy:** claims only; repair can silently produce a *wrong* valid document -- always re-validate with Pydantic and log that repair fired.
**Maintenance:** v0.60.1 (2026-06-03), pushed 2026-06-09, 4,970 stars, 0 open issues.
**Integration effort for Python/Flask:** low -- one-line wrapper; useful immediately as a stopgap during the strict-mode migration.
**Idea to borrow even if not adopted:** degrade-don't-crash -- a multi-hour batch run should flag one sub-document for review rather than die on a single parse failure.

Also verified but cut: Outlines (constrained decoding for self-hosted models -- only relevant if you ever self-host summarization; [github.com/dottxt-ai/outlines](https://github.com/dottxt-ai/outlines), 14k+ stars, v1.3.0 May 2026) and Tenacity (retry/backoff orchestration; v9.1.4, 2026-02-07).

---

### R2. Segmentation coverage verification

**Honest finding (verified):** no dedicated off-the-shelf tool or named technique exists for validating that segmentation ranges form an exact partition -- the PSS literature does not treat coverage validation as a step at all, because **per-page boundary classification makes gaps and overlaps unrepresentable by construction** (the output is a sorted list of cut points). That representation change is the root-cause fix and another argument for the A1 redesign. Until then:

**Name + link:** portion ([pypi.org/project/portion](https://pypi.org/project/portion/))
**What it does:** Pure-Python interval arithmetic (union, complement, difference, auto-simplification). The full R2 invariant is ~10 lines: union all ranges as `closedopen(start, end+1)`, assert equality with `closedopen(1, n_pages+1)`; the difference IS the gap/overlap report.
**Targets:** R2
**Root cause or symptom:** root cause (for the checking problem as posed)
**Data-flow posture:** SELF-HOSTABLE
**Language/stack:** Python
**Evidence of better accuracy:** deterministic library; extensive test suite; benchmarks irrelevant at 2000-page scale.
**Maintenance:** v2.6.1 (2025-05-25), last commit 2026-01-28, 523 stars, Production/Stable, Python 3.9-3.13. Note LGPLv3 (fine for internal use; flag if redistribution ever matters).
**Integration effort for Python/Flask:** low -- one pure function after segmentation, before any OCR/LLM spend; fail fast with the exact gap/overlap pages.
**Idea to borrow even if not adopted:** half-open interval convention `[start, end+1)` -- adjacency becomes exact bound equality, eliminating off-by-one ambiguity.

**Name + link:** Hypothesis ([pypi.org/project/hypothesis](https://pypi.org/project/hypothesis/))
**What it does:** Property-based testing -- generate random page counts/boundary lists, assert "every page in exactly one range" holds, and assert the checker rejects deliberately corrupted range sets.
**Targets:** R2
**Root cause or symptom:** symptom-side hardening (tests the validator)
**Data-flow posture:** SELF-HOSTABLE (dev-only)
**Language/stack:** Python
**Evidence of better accuracy:** JOSS 2019 paper; ~38.7M downloads/month.
**Maintenance:** v6.155.2 (2026-06-05), pushed 2026-06-11, 8,696 stars, MPL-2.0.
**Integration effort for Python/Flask:** low -- ~40 lines in the existing pytest suite.
**Idea to borrow even if not adopted:** mutation-style properties catch the classic bug where the safety check itself silently passes everything.

(`intervaltree` also verified -- v3.2.1, 2025-12-24, 690 stars -- but it lacks a complement operation, so gap detection is hand-rolled anyway; worse fit than portion here.)

---

### L1. Latency / throughput

**Approaches:** (a) async fan-out of API calls with bounded concurrency (root cause for the sequential bottleneck); (b) page-level parallel OCR (root cause); (c) content-hash caching so retries/re-runs skip completed work; (d) provider batch APIs -- **ruled out for PHI** (see below).

**Name + link:** AsyncOpenAI + asyncio.Semaphore ([github.com/openai/openai-python](https://github.com/openai/openai-python))
**What it does:** The official SDK's async client (identical surface to sync, built-in exponential-backoff retries incl. 429s). Fan out the 2-calls-per-sub-document across all sub-documents, bounded by a semaphore sized to your rate tier (start ~8-16).
**Targets:** L1
**Root cause or symptom:** root cause
**Data-flow posture:** SELF-HOSTABLE client code (calls go to OpenAI under the existing BAA, ZDR-eligible endpoints)
**Language/stack:** Python
**Evidence of better accuracy:** structural -- speedup bounded by tier TPM/RPM ([rate-limits docs](https://developers.openai.com/api/docs/guides/rate-limits)).
**Maintenance:** v2.41.1 (2026-06-10), 30,980 stars, pushed 2026-06-11.
**Integration effort for Python/Flask:** low -- smallest diff of the L1 options; `asyncio.run()` inside the existing job step. (Skip `aiometer` -- no commits in ~14 months; stdlib Semaphore suffices.)
**Idea to borrow even if not adopted:** respect Retry-After on 429s instead of hand-rolled sleep loops.

**Name + link:** OCRmyPDF ([github.com/ocrmypdf/OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF))
**What it does:** Adds a Tesseract text layer to scanned PDFs using all CPU cores by default (`--jobs N`); same engine underneath, so quality is unchanged but a 2000-page record OCRs in parallel.
**Targets:** L1
**Root cause or symptom:** root cause (for OCR latency; does NOT fix A3 quality)
**Data-flow posture:** SELF-HOSTABLE (MPL-2.0)
**Language/stack:** Python (requires >=3.11; fine on 3.12)
**Evidence of better accuracy:** parallelism is structural; "battle-tested on millions of PDFs" is claims-only.
**Maintenance:** exceptional -- v17.6.0 released 2026-06-12 (today), 33,867 stars.
**Integration effort for Python/Flask:** low -- caveat: `ocrmypdf.ocr()` is not thread-safe in-process; use a child process per concurrent run.
**Idea to borrow even if not adopted:** make the PAGE the unit of parallel work, not the sub-document -- a 1-page claim form vs a 300-page deposition load-balances terribly.

**Name + link:** diskcache ([github.com/grantjenks/python-diskcache](https://github.com/grantjenks/python-diskcache))
**What it does:** SQLite+filesystem cache, thread- AND process-safe, with memoize + stampede prevention. Key OCR text and summaries by SHA-256 of (page-range bytes + prompt/template version) so re-runs and crash recovery skip paid work.
**Targets:** L1 (also C1)
**Root cause or symptom:** symptom (work-skipping, not speedup)
**Data-flow posture:** SELF-HOSTABLE -- keep the cache dir inside the PHI storage boundary, encrypted at rest
**Language/stack:** Python
**Evidence of better accuracy:** structural win (skip repeated Tesseract + paid API calls).
**Maintenance:** WEAK -- last release 5.6.3 (2023-08-31), last commit 2024-03; ~1.66M weekly downloads; open maintenance-status issue. Pure Python so risk is low, but smoke-test on 3.12, or implement the same pattern on a Redis/DB table you already run.
**Integration effort for Python/Flask:** low -- two decorators + a deterministic key function.
**Idea to borrow even if not adopted:** content-hash + prompt-version cache keys -- the idempotency design outlives the library choice.

**Batch APIs (verified, with a blocking caveat):** OpenAI Batch ([docs](https://developers.openai.com/api/docs/guides/batch); 50% discount, 24h window, 50k requests/200 MB JSONL) and Gemini Batch ([docs](https://ai.google.dev/gemini-api/docs/batch-api); 50%, 48h expiry) both work as advertised -- **but neither is usable for PHI as-is**: OpenAI's Healthcare Addendum limits PHI to ZDR-eligible services and `/v1/batches` + `/v1/files` are explicitly ZDR-ineligible; the Gemini Batch docs page is the ai.google.dev surface, outside Google Cloud BAA scope. Worth borrowing anyway: the JSONL manifest with a `custom_id` per sub-document -- an idempotent, resumable work-unit ledger for your own async design. Open question for the team: whether batch prediction via Gemini Enterprise Agent Platform falls under "Generative AI on Gemini Enterprise Agent Platform" BAA coverage.

---

### S1. Per-job state + background processing

**Approaches:** (a) the job-row pattern -- per-job DB rows + enqueue/poll endpoints (the architectural fix, library-independent); (b) a real task queue (RQ / Huey / Celery) running the pipeline outside the Flask process.

**Name + link:** Per-job state pattern (Grinberg Flask Mega-Tutorial Part XXII, [blog.miguelgrinberg.com](https://blog.miguelgrinberg.com/post/the-flask-mega-tutorial-part-xxii-background-jobs); official [Flask+Celery docs pattern](https://flask.palletsprojects.com/en/stable/patterns/celery/))
**What it does:** Every job gets a DB row (String(36) PK = queue job ID) holding owner, status, progress, result path; Flask endpoints only enqueue and poll. Module-level globals are deleted entirely.
**Targets:** S1
**Root cause or symptom:** root cause
**Data-flow posture:** SELF-HOSTABLE
**Language/stack:** Python
**Evidence of better accuracy:** reference implementations verified live (tutorial 2024 edition, posted 2023-12-03; companion repo microblog 4,770 stars, pushed 2025-04-06).
**Maintenance:** n/a (pattern)
**Integration effort for Python/Flask:** low -- a Task model + two endpoints + replacing global reads/writes; this piece ships regardless of queue choice.
**Idea to borrow even if not adopted:** the keyed job-row schema (id, status, progress, stage, result_path, created_by, error) alone makes the app multi-user safe and is HIPAA-audit-friendly.

**Name + link:** RQ ([github.com/rq/rq](https://github.com/rq/rq))
**What it does:** Lightweight Redis-backed job queue ("a lightweight alternative to the heaviness of Celery"); retries, scheduling, priorities; workers run jobs in separate processes.
**Targets:** S1
**Root cause or symptom:** root cause
**Data-flow posture:** SELF-HOSTABLE (RQ + Redis on your servers)
**Language/stack:** Python
**Evidence of better accuracy:** claims only; adoption: 10,650 stars.
**Maintenance:** excellent -- v2.9.1 (2026-06-06), pushed 2026-06-12. Windows folklore corrected during verification: current docs document `SpawnWorker` explicitly for Windows; default fork worker runs fine in your Docker Linux containers.
**Integration effort for Python/Flask:** low -- single dependency + a Redis container; far smaller surface than Celery.
**Idea to borrow even if not adopted:** `job.meta` as the per-job progress channel ({'progress': pct, 'stage': 'ocr'}) so the poll endpoint shows stage-level progress on a 2000-page PDF.

**Name + link:** Huey ([github.com/coleifer/huey](https://github.com/coleifer/huey))
**What it does:** Broker-optional task queue (Redis, SQLite, filesystem, in-memory backends) by the peewee author -- can run with zero extra infrastructure.
**Targets:** S1
**Root cause or symptom:** root cause
**Data-flow posture:** SELF-HOSTABLE (MIT)
**Language/stack:** Python
**Evidence of better accuracy:** claims only; 5,973 stars.
**Maintenance:** v3.0.3 released 2026-06-12 (today); chosen over Dramatiq (v2.1.0, 2026-03-03, 5.3k stars) on recency, license, and Windows-friendliness (native thread workers).
**Integration effort for Python/Flask:** low -- lowest-ops option: SQLite backend means no Redis at all; thread workers run on bare Windows during dev.
**Idea to borrow even if not adopted:** broker-optional design isolates "stop using globals" from "do we need Redis" -- ship the first without deciding the second.

(Celery also verified -- v5.6.3 2026-03-26, 28,582 stars -- but Windows is officially unsupported ("please don't open issues"), so the worker must live in Docker; most moving parts of the three. Justified only if you later need its routing/scheduling depth.)

---

### C1. Cost (brief, as specified)

**Name + link:** OpenAI prompt caching ([docs](https://developers.openai.com/api/docs/guides/prompt-caching))
**What it does:** Automatic prefix caching for prompts >= 1024 tokens; "up to 90%" input-cost reduction on hits; 5-10 min retention (1h max; 24h opt-in via `prompt_cache_retention`, available on gpt-4.1 and the gpt-5.x family).
**Targets:** C1
**Root cause or symptom:** root cause (for the resent-prompt waste)
**Data-flow posture:** CLOUD (OpenAI, existing BAA)
**Language/stack:** vendor feature
**Evidence of better accuracy:** official docs, verified 2026-06-12; effective discount depends on hit rate.
**Maintenance:** GA.
**Integration effort for Python/Flask:** low -- reorder prompts so the large static category block is a stable prefix, dynamic content last. Zero API changes.
**Idea to borrow even if not adopted:** static-first/dynamic-last prompt layout pays on every provider.

Brief bullets (all verified 2026-06-12, all point-in-time):
- **Gemini context caching:** implicit (on by default, 2.5+) and explicit (guaranteed ~90% off cached input + storage: $1.00/MTok/h Flash, $4.50/MTok/h Pro; minimums 2,048 tokens on 2.5, 4,096 on 3.x). For PHI, use the equivalent on the Cloud platform endpoint -- the ai.google.dev caching surface is outside BAA scope.
- **Batch APIs (50% off):** blocked for PHI under current BAA terms (see L1).
- **Model right-sizing:** current OpenAI small tier is gpt-5.4-mini ($0.75/$4.50 per MTok, 400K context) and gpt-5.4-nano; gpt-4o-mini / gpt-4.1-mini are no longer in the current catalog -- don't hardcode old names. Route the cheap title/provider extraction call to a mini model; keep the stronger model for the narrative summary. Validate with a ~20-document before/after eval.
- **Prompt slimming:** with caching in place this matters less; do it after caching, not before.

---

## 3. "Try First" Shortlist (accuracy-first)

1. **(A1) Per-page boundary classifier prototype** -- embed page text (sentence-transformers) + logistic regression "starts new doc" classifier; label ~30-50 real merged PDFs; evaluate with PSS metrics. *Root-cause fix for the #1 problem; medium effort (the labeling is the work).*
2. **(A2) Calibrated title classifier + abstention threshold** -- sentence-transformers + LogisticRegression + CalibratedClassifierCV; threshold from a reliability diagram. *~30 lines, CPU-only, kills the 0.65 cliff; low effort.* Same-day stopgap: swap difflib for RapidFuzz `token_set_ratio`.
3. **(Compliance, do before anything else ships) Audit the Gemini call path** -- if segmentation uses an ai.google.dev key, move it to Gemini Enterprise Agent Platform; confirm ZDR on the OpenAI org. *Hours of config/auth work; removes a live PHI exposure risk.*
4. **(A3) Pilot Google Document AI Form Parser** on your 20 worst pages (checkbox forms, handwritten notes) vs Tesseract; score with edit-distance + TEDS-style metrics. *Existing BAA, no GPU; low-medium effort.* If a GPU is available, run olmOCR on the same set.
5. **(A4) Build the 50-document error-annotation set** (hallucination/omission x major/minor on synthetic records) and iterate category prompts against it. *Low effort, measured 75%/58% error reductions from prompt changes alone.*
6. **(R1) Pydantic models + OpenAI strict structured outputs + json-repair fallback.** *Low effort; eliminates the crash class.*
7. **(R2) portion-based partition check** before any OCR/LLM spend. *~10 lines.*
8. **(L1) AsyncOpenAI + semaphore fan-out; OCRmyPDF for parallel OCR.** *Low effort each.*
9. **(S1) Job-row table + Huey (SQLite backend) or RQ.** *Medium effort; makes the app multi-user.*
10. **(C1) Reorder prompts for automatic prompt caching; mini-tier model for the extraction call.** *Low effort.*

---

## 4. Open Questions / Risks

- **Which Google BAA does Gesco actually hold?** A Google *Cloud* BAA covers Document AI and Gemini Enterprise Agent Platform; a Workspace BAA covers neither. This determines whether items 3 and 4 above are config work or procurement work.
- **Is ZDR enabled on the OpenAI org/project?** OpenAI's Healthcare Addendum ties PHI eligibility to ZDR-eligible endpoint usage; image inputs carry a CSAM-scanning retention caveat even under ZDR.
- **Domain transfer is unproven everywhere.** No verified benchmark contains medical records (PSS corpora are Dutch FOIA docs, tobacco scans, comics; OmniDocBench has no medical type). Every accuracy number above measures a different domain. A labeled QME/AME eval corpus (boundaries + categories + extraction ground truth, synthetic or de-identified per your HIPAA rules) is the single prerequisite that unblocks honest measurement for A1, A2, and A3.
- **How many boundary labels are enough?** Unknown whether dozens or hundreds of labeled merged PDFs are needed for the per-page classifier to beat the current Gemini chunking; weak supervision from the existing human-checkpoint edits (which already correct boundaries) could bootstrap this cheaply.
- **Benchmark self-alignment:** olmOCR-Bench is published by olmOCR's authors; OmniDocBench is maintained by MinerU's org (partially offset by Baidu's independent PaddleOCR-VL result). OCR leaderboards move monthly -- re-check at decision time.
- **Can decoder-LLM PSS (TABME++ line) match a local classifier using only BAA-covered APIs?** If yes, A1 needs no new infrastructure at all -- worth a head-to-head in the same eval harness.
- **GPU availability** determines whether the self-hosted A3 tier (olmOCR/MinerU/PaddleOCR-VL) is even on the table; otherwise Document AI / multimodal Gemini via GEAP is the practical path.
- **Deferred (explicitly, not silently):** individual repo-level maintenance verification for MinerU and PaddleOCR-VL; Ragas/automated faithfulness scorers (existence confirmed, effectiveness unverified); Azure Document Intelligence deep-dive (new-vendor procurement dominates); fine-grained pricing comparisons beyond the C1 bullets.
