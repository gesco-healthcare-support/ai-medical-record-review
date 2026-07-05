"""Google Document AI Custom Splitter adapter - benchmark against our Gemini methods.

Runs a labeled case PDF through the zero-shot pretrained Custom Splitter via BATCH
processing (sync caps at 15 pages; batch reads/writes the scratch GCS bucket) and scores
the returned sub-document spans with the SAME harness as every Gemini row, so the
comparison is like-for-like. See docs/04-DOCAI-SETUP.md for the setup this assumes.

PHI hygiene: the bucket is BAA-covered scratch space with a 7-day lifecycle rule, and
this script deletes each case's input and output objects right after scoring anyway.
Only case ALIASES and page numbers are printed.

Usage:
  python -u src/docai_splitter.py check          # verify processor + bucket (no PHI moves)
  python -u src/docai_splitter.py run "Case 3"   # upload, split, score, clean up
"""

import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bake_off import _score_spans
from config import OUTPUTS
from diagnose_naive import analyze, resolve
from run_phase0 import _case

PROJECT = os.environ.get("DOCAI_PROJECT", "gen-lang-client-0785241985")
LOCATION = os.environ.get("DOCAI_LOCATION", "us")
PROCESSOR = os.environ.get("DOCAI_PROCESSOR", "e137bbc6446a54c9")
BUCKET = os.environ.get("DOCAI_BUCKET", "gen-lang-client-0785241985-docai-eval")
USD_PER_PAGE = 0.005  # custom splitter $5 / 1,000 pages (pricing page, 2026-07-04)

OUT_DIR = os.path.join(OUTPUTS, "docai-diagnosis")


def _clients():
    from google.api_core.client_options import ClientOptions
    from google.cloud import documentai, storage

    opts = ClientOptions(
        api_endpoint=f"{LOCATION}-documentai.googleapis.com", quota_project_id=PROJECT
    )
    docai = documentai.DocumentProcessorServiceClient(client_options=opts)
    gcs = storage.Client(project=PROJECT)
    return documentai, docai, gcs


def check():
    documentai, docai, gcs = _clients()
    name = docai.processor_path(PROJECT, LOCATION, PROCESSOR)
    try:
        proc = docai.get_processor(name=name)
        print(f"processor : {proc.display_name}  type={proc.type_}  state={proc.state.name}")
        print(f"  default version: {proc.default_processor_version.rsplit('/', 1)[-1]}")
    except Exception as exc:  # apiUser can PROCESS but not GET metadata; that's acceptable
        if "documentai.processors.get" not in str(exc):
            raise
        print(f"processor : {name}")
        print("  metadata not readable (role lacks documentai.processors.get - processing "
              "still allowed; grant 'Document AI Viewer' to see version info)")
    # Probe exactly what the run path needs (object write/list/delete): Storage Object Admin
    # does not include storage.buckets.get, so bucket-metadata reads would false-fail.
    bucket = gcs.bucket(BUCKET)
    probe = bucket.blob("input/_probe.txt")
    probe.upload_from_string("probe")
    found = any(b.name == "input/_probe.txt" for b in gcs.list_blobs(BUCKET, prefix="input/"))
    probe.delete()
    print(f"bucket    : {BUCKET}  object write/list/delete OK={found}")
    print("  (lifecycle rule not verifiable without storage.buckets.get - confirm the "
          "7-day delete rule exists in the console)")
    print("reminder  : PHI may only flow after the HIPAA BAA is accepted on this account")
    print("CHECK OK")


# The 13 document classes the zero-shot (Gemini-based) splitter splits into. Names mirror the
# MRR taxonomy because the pretrained model READS them to decide boundaries and classifications.
SCHEMA_LABELS = (
    "progress_report",
    "initial_or_comprehensive_evaluation",
    "imaging_or_diagnostic_report",
    "laboratory_results",
    "physical_therapy_or_chiro_note",
    "qme_ame_medical_legal_report",
    "operative_or_pathology_report",
    "deposition",
    "legal_claim_form",
    "request_for_authorization",
    "work_status_report",
    "correspondence_or_letter",
    "administrative_or_other",
)


def setup():
    """One-time processor preparation via API (console UI drifts; the API does not):
    (1) attach a managed dataset in the scratch bucket, (2) push the splitter schema,
    (3) set the pretrained version as default. Requires Document AI Editor on the caller."""
    from google.api_core.client_options import ClientOptions
    from google.cloud import documentai_v1beta3 as dai3

    _documentai, docai, _gcs = _clients()
    name = docai.processor_path(PROJECT, LOCATION, PROCESSOR)
    opts = ClientOptions(
        api_endpoint=f"{LOCATION}-documentai.googleapis.com", quota_project_id=PROJECT
    )
    ds_client = dai3.DocumentServiceClient(client_options=opts)

    print("1/3 attaching managed dataset ...", flush=True)
    dataset = dai3.Dataset(
        name=f"{name}/dataset",
        gcs_managed_config=dai3.Dataset.GCSManagedConfig(
            gcs_prefix=dai3.GcsPrefix(gcs_uri_prefix=f"gs://{BUCKET}/dataset/")
        ),
    )
    try:
        ds_client.update_dataset(request={"dataset": dataset}).result(timeout=300)
        print("  dataset attached", flush=True)
    except Exception as exc:
        if "already" in str(exc).lower() or "FAILED_PRECONDITION" in str(exc):
            print(f"  dataset already configured ({exc})", flush=True)
        else:
            raise

    print("2/3 pushing splitter schema ...", flush=True)
    schema = dai3.DatasetSchema(
        name=f"{name}/dataset/datasetSchema",
        document_schema=dai3.DocumentSchema(
            display_name="mrr_split_schema",
            description="California workers' compensation medical-record sub-document classes",
            metadata=dai3.DocumentSchema.Metadata(document_splitter=True),
            entity_types=[
                dai3.DocumentSchema.EntityType(
                    name=label, base_types=["document"], display_name=label
                )
                for label in SCHEMA_LABELS
            ],
        ),
    )
    ds_client.update_dataset_schema(request={"dataset_schema": schema})
    print(f"  schema saved with {len(SCHEMA_LABELS)} classes", flush=True)

    print("3/3 setting pretrained version as default ...", flush=True)
    versions = list(docai.list_processor_versions(parent=name))
    pretrained = [v for v in versions if "pretrained-splitter" in v.name]
    if not pretrained:
        raise SystemExit(
            "no pretrained-splitter version visible: " + ", ".join(v.name for v in versions)
        )
    target = sorted(pretrained, key=lambda v: v.name)[-1]
    docai.set_default_processor_version(
        request={"processor": name, "default_processor_version": target.name}
    ).result(timeout=300)
    print(f"  default version = {target.name.rsplit('/', 1)[-1]}", flush=True)
    print("SETUP OK", flush=True)


def _resolve_pages(shard, ref):
    """Global 1-based page for a pageRef: `page` indexes into the shard's pages[], whose
    page_number is global; a page value of 0 is omitted from the JSON (API quirk)."""
    idx = int(ref.page or 0)
    try:
        pn = shard.pages[idx].page_number
        return int(pn) if pn else idx + 1
    except IndexError:
        return idx + 1


def run(alias):
    documentai, docai, gcs = _clients()
    cid, alias = resolve(alias)
    c = _case(cid)
    n = c["n"]
    bucket = gcs.bucket(BUCKET)
    in_blob = f"input/{alias}.pdf"
    out_prefix = f"output/{alias}/"

    print(f"=== {alias} [docai]: {n} pages, {len(c['gold_starts'])} gold docs", flush=True)
    print(f"  uploading to gs://{BUCKET}/{in_blob} ...", flush=True)
    bucket.blob(in_blob).upload_from_filename(c["pdf"])

    request = documentai.BatchProcessRequest(
        name=docai.processor_path(PROJECT, LOCATION, PROCESSOR),
        input_documents=documentai.BatchDocumentsInputConfig(
            gcs_documents=documentai.GcsDocuments(
                documents=[
                    documentai.GcsDocument(
                        gcs_uri=f"gs://{BUCKET}/{in_blob}", mime_type="application/pdf"
                    )
                ]
            )
        ),
        document_output_config=documentai.DocumentOutputConfig(
            gcs_output_config=documentai.DocumentOutputConfig.GcsOutputConfig(
                gcs_uri=f"gs://{BUCKET}/{out_prefix}"
            )
        ),
    )
    t0 = time.time()
    operation = docai.batch_process_documents(request)
    print(f"  batch operation started; polling (batch can take minutes) ...", flush=True)
    operation.result(timeout=2400)
    wall = time.time() - t0
    print(f"  batch complete in {wall:.0f}s", flush=True)

    # Collect entities from every output shard; resolve pageRefs to global 1-based pages.
    entities, shards = [], 0
    for blob in gcs.list_blobs(BUCKET, prefix=out_prefix):
        if not blob.name.endswith(".json"):
            continue
        shards += 1
        shard = documentai.Document.from_json(
            blob.download_as_bytes(), ignore_unknown_fields=True
        )
        for ent in shard.entities:
            pages = sorted(
                _resolve_pages(shard, ref) for ref in ent.page_anchor.page_refs
            ) or [1]
            entities.append(
                dict(
                    type=ent.type_,
                    confidence=round(float(ent.confidence), 3),
                    start=pages[0],
                    end=pages[-1],
                )
            )
    entities.sort(key=lambda e: (e["start"], e["end"]))
    print(f"  {shards} output shards -> {len(entities)} sub-document entities", flush=True)

    spans = [(e["start"], e["end"]) for e in entities]
    rows = [
        dict(s=e["start"], e=e["end"], t=e["type"], d="-", i="-",
             m="-" if e["confidence"] >= 0.5 else "x", chunk=(1, n))
        for e in entities
    ]

    case_dir = os.path.join(OUT_DIR, alias)
    os.makedirs(case_dir, exist_ok=True)
    with open(os.path.join(case_dir, "entities.json"), "w", encoding="utf-8") as f:
        json.dump(entities, f, indent=1)
    with open(os.path.join(case_dir, "pred.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow([r["s"], r["e"], "-", "-", "-", r["m"]])

    m = _score_spans(spans, c)
    conf_low = sum(1 for e in entities if e["confidence"] < 0.5)
    report = analyze(alias, c, rows, [(1, n)], [], 0)
    report += [
        "",
        f"[docai] entities={len(entities)} shards={shards} low-confidence(<0.5)={conf_low}",
        f"  headline: bF1={m['f1']:.2f} R={m['recall']:.2f} P={m['precision']:.2f} "
        f"DocF1={m['doc_f1']:.2f} wDocF1={m['wdoc_f1']:.2f} over={m['over_seg']:.2f}",
        f"  cost: {n} pages x ${USD_PER_PAGE} = ${n * USD_PER_PAGE:.2f} | wall-clock {wall:.0f}s",
    ]
    with open(os.path.join(case_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(f"# DocAI splitter diagnosis: {alias}\n\n```\n" + "\n".join(report) + "\n```\n")
    print("\n".join(report), flush=True)

    # PHI hygiene: remove this case's objects immediately (lifecycle rule is the backstop).
    bucket.blob(in_blob).delete()
    deleted = 0
    for blob in gcs.list_blobs(BUCKET, prefix=out_prefix):
        blob.delete()
        deleted += 1
    print(f"  cleaned up: input + {deleted} output objects deleted", flush=True)
    print(f"saved: {case_dir}", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        check()
    elif cmd == "setup":
        setup()
    elif cmd == "run":
        run(sys.argv[2])
    else:
        raise SystemExit('usage: docai_splitter.py check | setup | run "Case 3"')
