"""OCR-quality comparison on explicit (hard) page ranges of an explicit PDF. Temp 0.0.

For each page range it extracts text via tesseract_current / tesseract_tuned / docai, scores each
(char count, LLM coherence 0-10, similarity to the Document AI reference), summarizes each (general
prompt) + a gemini_direct (image) summary, and judges faithfulness vs the Document AI text. Saves
every engine's raw OCR text to a file for human eyeballing (the definitive quality check).

All Gemini calls route through generate_with_retry (Redis limiter + backoff). Serial. No DB writes.
Run: python /tmp/ocr_quality.py --pdf /tmp/arroyo_full.pdf --ranges 27-33,80-89,361-370
"""

import argparse
import difflib
import gc
import io
import json
import os

import numpy as np
import pymupdf
import pytesseract
from google.genai import types
from pdf2image import convert_from_path
from PIL import Image

from app.config import get_settings
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.ocr import extract_text_from_selected_pages
from app.services.prompts import prompts as DEFAULT_PROMPTS

PROJECT, LOCATION = "515700214157", "us"
GEN_PROMPT = DEFAULT_PROMPTS["category_100"]
_JUDGE_SYS = (
    "You are a strict medical-record auditor. Given SOURCE text and a SUMMARY, list summary "
    'statements not supported by the SOURCE or contradicting it. JSON: {"unsupported":[],"contradictions":[]}.'
)
_COH_SYS = (
    "Rate the OCR quality of the text on a 0-10 integer scale: 10 = clean, readable, coherent "
    "medical text; 0 = garbled/nonsense. Reply with ONLY the integer."
)


def _model():
    return get_settings().summary_model


def _gen(system_msg, contents, temp=0.0):
    resp = generate_with_retry(
        get_genai_client(), model=_model(), contents=contents,
        config=types.GenerateContentConfig(temperature=temp, max_output_tokens=2048, system_instruction=system_msg),
    )
    return (resp.text or "").strip()


def _ratio(a, b):
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


def _coherence(text):
    if not text.strip():
        return 0
    out = _gen(_COH_SYS, text[:6000], 0.0)
    try:
        return int("".join(c for c in out if c.isdigit())[:2] or "0")
    except Exception:
        return -1


def _faith(source, summary):
    out = _gen(_JUDGE_SYS, f"SOURCE:\n{source}\n\nSUMMARY:\n{summary}", 0.0)
    try:
        c = out.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        d = json.loads(c)
        return len(d.get("unsupported", [])), len(d.get("contradictions", []))
    except Exception:
        return -1, -1


def _otsu(gray):
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    total, sum_total = gray.size, np.dot(np.arange(256), hist)
    w_b = s_b = best_v = best_t = 0.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        s_b += t * hist[t]
        between = w_b * w_f * (s_b / w_b - (sum_total - s_b) / w_f) ** 2
        if between > best_v:
            best_v, best_t = between, t
    return (gray > best_t).astype(np.uint8) * 255


def extract_tuned(path, st, en):
    text = ""
    for p in range(st, en + 1):  # one page at a time to cap memory
        imgs = convert_from_path(path, first_page=p, last_page=p, dpi=300)
        for img in imgs:
            text += pytesseract.image_to_string(
                Image.fromarray(_otsu(np.array(img.convert("L")))), config="--oem 1 --psm 3"
            )
        del imgs
        gc.collect()
    return text


def extract_docai(path, st, en, processor):
    from google.cloud import documentai_v1 as documentai

    src = pymupdf.open(path)
    dst = pymupdf.open()
    dst.insert_pdf(src, from_page=st - 1, to_page=en - 1)
    data = dst.tobytes()
    dst.close()
    src.close()
    client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": f"{LOCATION}-documentai.googleapis.com"}
    )
    name = f"projects/{PROJECT}/locations/{LOCATION}/processors/{processor}"
    res = client.process_document(
        request=documentai.ProcessRequest(
            name=name, raw_document=documentai.RawDocument(content=data, mime_type="application/pdf")
        )
    )
    return res.document.text


def gemini_direct(path, st, en, max_pages=8):
    parts = []
    for p in range(st, min(en, st + max_pages - 1) + 1):  # cap pages; lean JPEG @110dpi
        imgs = convert_from_path(path, first_page=p, last_page=p, dpi=110)
        buf = io.BytesIO()
        imgs[0].convert("RGB").save(buf, format="JPEG", quality=70)
        parts.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"))
        del imgs
        gc.collect()
    out = _gen(GEN_PROMPT, parts + ["Summarize the attached document pages per the system instructions."])
    del parts
    gc.collect()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--ranges", required=True, help="e.g. 27-33,80-89,361-370")
    ap.add_argument("--processor", default="ebd3e336dea688fb")
    ap.add_argument("--out", default="/tmp/out/ocr_quality")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    ranges = [tuple(int(x) for x in r.split("-")) for r in args.ranges.split(",")]

    report = []
    for st, en in ranges:
        print(f"\n=== pages {st}-{en} ===")
        texts = {
            "tesseract_current": extract_text_from_selected_pages(args.pdf, list(range(st, en + 1))),
            "tesseract_tuned": extract_tuned(args.pdf, st, en),
        }
        try:
            texts["docai"] = extract_docai(args.pdf, st, en, args.processor)
            ref = texts["docai"]
        except Exception as exc:
            print(f"  docai FAILED: {type(exc).__name__}: {str(exc)[:150]}")
            texts["docai"] = None
            ref = texts["tesseract_tuned"]

        entry = {"range": f"{st}-{en}", "methods": {}}
        for name, text in texts.items():
            if text is None:
                entry["methods"][name] = {"error": "extraction failed"}
                continue
            with open(f"{args.out}_{st}-{en}_{name}.txt", "w", encoding="utf-8") as f:
                f.write(text)
            summ = _gen(GEN_PROMPT, text) if text.strip() else "(empty OCR)"
            uns, con = _faith(ref, summ)
            m = {"chars": len(text), "coherence_0_10": _coherence(text),
                 "sim_to_docai": _ratio(text, ref) if ref else None,
                 "unsupported": uns, "contradictions": con, "summary": summ}
            entry["methods"][name] = m
            print(f"  {name}: chars={m['chars']} coherence={m['coherence_0_10']} "
                  f"sim_to_docai={m['sim_to_docai']} unsupported={uns} contradictions={con}")

        gd = gemini_direct(args.pdf, st, en)
        uns, con = _faith(ref, gd)
        entry["methods"]["gemini_direct"] = {"summary": gd, "unsupported": uns, "contradictions": con}
        print(f"  gemini_direct: unsupported={uns} contradictions={con}")
        report.append(entry)
        del texts
        gc.collect()

    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    lines = ["# OCR-quality on hard page ranges (temp 0.0)\n",
             "coherence 0-10 (LLM; higher=cleaner) | sim_to_docai (lexical; low=this engine diverges "
             "from Document AI) | unsupported/contradictions (judge vs Document AI text).\n"]
    for e in report:
        lines.append(f"\n## pages {e['range']}\n")
        lines.append("| method | chars | coherence | sim_to_docai | unsupported | contradictions |")
        lines.append("|---|---|---|---|---|---|")
        for m, v in e["methods"].items():
            if "error" in v:
                lines.append(f"| {m} | ERROR | - | - | - | - |")
            elif "chars" in v:
                lines.append(f"| {m} | {v['chars']} | {v['coherence_0_10']} | {v['sim_to_docai']} | {v['unsupported']} | {v['contradictions']} |")
            else:
                lines.append(f"| {m} | (image) | - | - | {v['unsupported']} | {v['contradictions']} |")
        for m, v in e["methods"].items():
            if "summary" in v:
                lines.append(f"\n**{m} - summary:**\n\n{v['summary']}\n")
    with open(args.out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nwrote {args.out}.json / .md + per-engine .txt files")


if __name__ == "__main__":
    main()
