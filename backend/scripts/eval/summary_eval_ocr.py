"""OCR/extraction comparison (phase 2 of the summary-quality eval). Temp fixed at 0.0.

For each duplicate set it summarizes BOTH copies via four extraction paths and reports whether the
path makes the two copies converge (copyA_vs_copyB) and how faithful the summary is to a
high-quality reference (Document AI text). Also dumps raw OCR text + summaries for human review.

Paths:
  - tesseract_current : existing pipeline (pdf2image ~200 dpi, no preprocess)
  - tesseract_tuned   : 300 dpi + grayscale + Otsu binarize + --oem 1 --psm 3 (no deskew yet)
  - docai             : Google Document AI OCR (processor per --processor)
  - gemini_direct     : NO OCR - page images sent straight to Gemini (multimodal)

All Gemini calls go through generate_with_retry (Redis limiter + backoff). Serial. No DB writes.
Run: python /tmp/summary_eval_ocr.py --doc <id> --sets 2
"""

import argparse
import difflib
import io
import json
import os

import numpy as np
import pytesseract
import pymupdf
from google.genai import types
from pdf2image import convert_from_path
from PIL import Image

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Document, ReviewRow
from app.services import catalog
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.ocr import extract_text_from_selected_pages
from app.services.prompts import prompts as DEFAULT_PROMPTS

PROJECT = "515700214157"
LOCATION = "us"

_JUDGE_SYS = (
    "You are a strict medical-record auditor. Given the SOURCE text of a document and a SUMMARY, "
    "list statements in the SUMMARY not supported by the SOURCE (fabrications) or contradicting it. "
    'Respond JSON: {"unsupported": ["..."], "contradictions": ["..."]}.'
)


def _model():
    return get_settings().summary_model


def _gen_text(system_msg, user_text, temp=0.0):
    resp = generate_with_retry(
        get_genai_client(), model=_model(), contents=user_text,
        config=types.GenerateContentConfig(temperature=temp, max_output_tokens=2048, system_instruction=system_msg),
    )
    return (resp.text or "").strip()


def _ratio(a, b):
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


def _resolve_prompt(session, category):
    p = catalog.get_prompt(session, "summary", str(category))
    if p:
        return p
    key = f"category_{int(category):02d}" if str(category) != "100" else "category_100"
    return DEFAULT_PROMPTS.get(key, DEFAULT_PROMPTS["category_100"])


def _faithfulness(source, summary):
    out = _gen_text(_JUDGE_SYS, f"SOURCE:\n{source}\n\nSUMMARY:\n{summary}", 0.0)
    try:
        c = out.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        d = json.loads(c)
        return len(d.get("unsupported", [])), len(d.get("contradictions", []))
    except Exception:
        return -1, -1


# ---- extraction paths ----
def _otsu(gray: np.ndarray) -> np.ndarray:
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    total = gray.size
    sum_total = np.dot(np.arange(256), hist)
    w_b = s_b = 0.0
    best_v = best_t = 0.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        s_b += t * hist[t]
        m_b = s_b / w_b
        m_f = (sum_total - s_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best_v:
            best_v, best_t = between, t
    return ((gray > best_t).astype(np.uint8) * 255)


def extract_tuned(path, start, end):
    text = ""
    for img in convert_from_path(path, first_page=start, last_page=end, dpi=300):
        gray = np.array(img.convert("L"))
        text += pytesseract.image_to_string(Image.fromarray(_otsu(gray)), config="--oem 1 --psm 3")
    return text


def extract_docai(path, start, end, processor):
    from google.cloud import documentai_v1 as documentai

    src = pymupdf.open(path)
    dst = pymupdf.open()
    dst.insert_pdf(src, from_page=start - 1, to_page=end - 1)
    pdf_bytes = dst.tobytes()
    dst.close()
    src.close()
    client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
    )
    name = f"projects/{PROJECT}/locations/{LOCATION}/processors/{processor}"
    raw = documentai.RawDocument(content=pdf_bytes, mime_type="application/pdf")
    result = client.process_document(request=documentai.ProcessRequest(name=name, raw_document=raw))
    return result.document.text


def summarize_gemini_direct(path, start, end, system_prompt):
    parts = []
    for img in convert_from_path(path, first_page=start, last_page=end, dpi=180):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        parts.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"))
    resp = generate_with_retry(
        get_genai_client(), model=_model(),
        contents=parts + ["Summarize the attached document pages following the system instructions."],
        config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=2048, system_instruction=system_prompt),
    )
    return (resp.text or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True)
    ap.add_argument("--sets", type=int, default=2)
    ap.add_argument("--processor", default="ebd3e336dea688fb")
    ap.add_argument("--out", default="/tmp/out/summary_eval_ocr")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    report = []
    with get_sessionmaker()() as session:
        doc = session.get(Document, args.doc)
        path = doc.stored_path
        rows = session.query(ReviewRow).filter(ReviewRow.document_id == args.doc).all()
        groups = {}
        for r in rows:
            groups.setdefault((r.date, r.title), []).append(r)
        dupes = sorted([(k, v) for k, v in groups.items() if len(v) > 1],
                       key=lambda kv: (-len(kv[1]), kv[0][0]))[: args.sets]

        for (date, title), rs in dupes:
            rs = sorted(rs, key=lambda r: r.start)
            a, b = rs[0], rs[1]
            prompt = _resolve_prompt(session, a.category)
            print(f"\n=== {title} ({date}) A={a.start}-{a.end} B={b.start}-{b.end} ===")

            texts = {}  # method -> (textA, textB)
            for name, fn in (
                ("tesseract_current", lambda st, en: extract_text_from_selected_pages(path, list(range(st, en + 1)))),
                ("tesseract_tuned", lambda st, en: extract_tuned(path, st, en)),
            ):
                texts[name] = (fn(a.start, a.end), fn(b.start, b.end))
            try:
                texts["docai"] = (extract_docai(path, a.start, a.end, args.processor),
                                  extract_docai(path, b.start, b.end, args.processor))
                ref = texts["docai"][0]  # reference source-of-truth for faithfulness
            except Exception as exc:
                print(f"  docai FAILED: {type(exc).__name__}: {str(exc)[:160]}")
                texts["docai"] = None
                ref = texts["tesseract_tuned"][0]

            entry = {"title": title, "date": date, "category": a.category,
                     "A": f"{a.start}-{a.end}", "B": f"{b.start}-{b.end}", "methods": {}}

            # text-based methods
            for name, pair in texts.items():
                if pair is None:
                    entry["methods"][name] = {"error": "extraction failed"}
                    continue
                ta, tb = pair
                sa = _gen_text(prompt, ta) if ta.strip() else "(empty OCR)"
                sb = _gen_text(prompt, tb) if tb.strip() else "(empty OCR)"
                uns, con = _faithfulness(ref, sa)
                entry["methods"][name] = {"chars_A": len(ta), "chars_B": len(tb),
                                          "A_vs_B": _ratio(sa, sb), "unsupported": uns,
                                          "contradictions": con, "summary_A": sa, "summary_B": sb,
                                          "ocr_A_head": ta[:400]}
                print(f"  {name}: chars_A={len(ta)} A_vs_B={_ratio(sa, sb)} unsupported={uns} contradictions={con}")

            # image-direct (no OCR)
            sa = summarize_gemini_direct(path, a.start, a.end, prompt)
            sb = summarize_gemini_direct(path, b.start, b.end, prompt)
            uns, con = _faithfulness(ref, sa)
            entry["methods"]["gemini_direct"] = {"A_vs_B": _ratio(sa, sb), "unsupported": uns,
                                                 "contradictions": con, "summary_A": sa, "summary_B": sb}
            print(f"  gemini_direct: A_vs_B={_ratio(sa, sb)} unsupported={uns} contradictions={con}")
            report.append(entry)

    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    lines = ["# OCR/extraction comparison (temp 0.0, current prompt)\n",
             "A_vs_B: lexical similarity of the two duplicate copies' summaries (higher = the path "
             "makes copies converge). unsupported/contradictions: LLM judge vs Document AI text.\n"]
    for e in report:
        lines.append(f"\n## {e['title']} - {e['date']} (A={e['A']} / B={e['B']})\n")
        lines.append("| method | chars_A | A_vs_B | unsupported | contradictions |")
        lines.append("|---|---|---|---|---|")
        for m, v in e["methods"].items():
            if "error" in v:
                lines.append(f"| {m} | - | ERROR | - | - |")
            else:
                lines.append(f"| {m} | {v.get('chars_A','-')} | {v['A_vs_B']} | {v['unsupported']} | {v['contradictions']} |")
        for m, v in e["methods"].items():
            if "summary_A" in v:
                lines.append(f"\n**{m} - summary of copy A:**\n\n{v['summary_A']}\n")
    with open(args.out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nwrote {args.out}.json / .md")


if __name__ == "__main__":
    main()
