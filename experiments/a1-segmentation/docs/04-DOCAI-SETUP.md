# Google Document AI splitter - setup instructions (researched 2026-07-04)

Goal: benchmark Google's purpose-built Custom Splitter against our Gemini method on the
labeled sample cases. These steps are Adrian's (console + compliance); the scoring
adapter afterwards is Claude's.

## Verified facts (sources at the end)

- **Document AI is explicitly on Google Cloud's HIPAA covered-products list** (entry:
  "Document AI"). Vertex AI is NOT explicitly listed - a separate open question for the
  Gemini path; raise both with Google in one conversation.
- The only splitter is the **Custom Splitter** (`CUSTOM_SPLITTING_PROCESSOR`). It works
  **zero-shot** via the pretrained model (`pretrained-splitter-v1.5-2025-07-14`, GA) -
  no training needed for a first benchmark; it can be uptrained later.
- Limits: **15 pages per synchronous request; 1,000 pages via batch** (batch reads and
  writes Cloud Storage). Our cases are 31-500pp (eligible subset), so BATCH mode + a GCS
  bucket are required. R2 (2,385pp) and R5 (1,647pp) exceed even batch and would need
  pre-splitting - exclude them.
- Price: **$5 per 1,000 pages** (Custom Splitter). The 3 clean cases = 884 pages =
  ~$4.40; all 8 eligible cases = ~2,125 pages = ~$10.60. No free tier.
- Output: `Document.entities[]`, one entity per detected sub-document with
  `type` (classification), `pageAnchor.pageRefs[]` (ZERO-based page indexes; a page
  value of 0 is OMITTED from the JSON - adapter must handle that), and `confidence`.
- BAA acceptance is done in-console and one project opt-in covers the account.

## Phase 1 - the compliance gate (do this FIRST, before any PHI is uploaded)

1. Open the Google Cloud console with an account that has authority to accept legal
   terms for Gesco (an Owner on the org/account; ideally whoever owns the Google
   relationship). Select the project the Vertex runs use (the one set as
   GOOGLE_CLOUD_PROJECT in the app .env).
2. Go to **console.cloud.google.com/iam-admin/privacy** (IAM & Admin -> Legal &
   Compliance / Privacy).
3. Check the current status. IMPORTANT: verify whether a HIPAA BAA was EVER actually
   accepted for this account - our Vertex work has assumed BAA coverage; if no BAA is
   on file, that assumption needs fixing for the Gemini path too, urgently.
4. Under **"Google Cloud Platform HIPAA Business Associate Addendum"** click
   **Review and Accept**, read it, click **I Accept**. One project opt-in covers the
   account.
5. Note the covered-products rule Google states: do NOT use non-covered products with
   PHI. Document AI is covered; keep PHI out of anything not on the list.
6. RECOMMENDED in parallel: ask Google support/account team to confirm in writing how
   "Vertex AI / Gemini API" is treated under the BAA (it is not named on the covered
   list even though a separate Google page says GenAI on Vertex supports HIPAA - the
   ambiguity has been open since June).

## Phase 2 - enable the API and create the processor

1. Console -> **APIs & Services -> Library** -> search **"Cloud Document AI API"** ->
   **Enable**. (CLI equivalent: `gcloud services enable documentai.googleapis.com`.)
2. Console -> search **"Document AI"** -> **Processor Gallery** -> under
   classification/splitting choose **Custom Splitter** -> **Create processor**.
   - Region: **us**.
   - Name: `mrr-splitter-eval`.
3. Open the processor -> **Manage versions** -> confirm the default version is the
   pretrained one (`pretrained-splitter-v1.5-...`). Do NOT start training/labeling -
   the zero-shot pretrained model is what we benchmark first.
4. Copy the **processor ID** (and note the region) - the adapter needs
   project + location + processor id.
5. IAM: the identity that will call it (your user ADC for now) needs
   **roles/documentai.apiUser** on the project.

## Phase 3 - storage for batch mode

1. Console -> Cloud Storage -> **Create bucket**: name like `<project-id>-docai-eval`,
   location **us** (same as the processor), uniform access control, no public access.
2. Add a **lifecycle rule: delete objects after 7 days** - PHI hygiene; the bucket is
   scratch space for the benchmark, nothing should live there.
3. IAM: the calling identity needs read/write on this bucket
   (**roles/storage.objectAdmin** on the bucket is simplest).

## Phase 4 - hand back to Claude

Provide: project id, location (us), processor id, bucket name. The adapter then:
uploads each labeled case PDF to the bucket, runs batchProcess, parses
entities -> (start, end) spans (converting zero-based pageRefs, handling the
omitted-zero quirk), scores with the existing harness (same metrics as the Gemini
rows), and reports cost from page counts. Expected spend: ~$4.40 (clean 3) to
~$10.60 (all 8 eligible).

## Sources (all fetched 2026-07-04)

- HIPAA covered products + BAA conditions: https://cloud.google.com/security/compliance/hipaa (HIGH)
- BAA acceptance steps: https://support.google.com/cloud/answer/6329727 (HIGH)
- Processor list / Custom Splitter, versions, page limits, regions:
  https://docs.cloud.google.com/document-ai/docs/processors-list (HIGH)
- Splitter output format (entities/pageAnchor/confidence, zero-omission caveat):
  https://docs.cloud.google.com/document-ai/docs/splitters (HIGH)
- Pricing ($5/1k pages custom splitter/classifier): https://cloud.google.com/document-ai/pricing (HIGH)
