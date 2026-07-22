"""Concurrent-OCR deadlock hammer - dev proof for the verify-pass forever-hang fix.

Run INSIDE the segment-worker (or summarize-worker) container against a real PDF in /app/uploads.
It rasterizes a few pages and fires many image_to_string calls across several threads - the exact
concurrency shape the verify pass uses (up to CLASSIFY_WORKERS boundary OCRs at once), which
deadlocks Tesseract when OMP_THREAD_LIMIT is unset.

    # deadlock repro (expect a low/zero count within the timeout):
    docker compose exec -e OMP_THREAD_LIMIT= -e OMP_NUM_THREADS= segment-worker \
        python scripts/dev/ocr_concurrency_hammer.py /app/uploads/<file>.pdf
    # fixed (expect 60/60):
    docker compose exec segment-worker \
        python scripts/dev/ocr_concurrency_hammer.py /app/uploads/<file>.pdf

The image ships no pkill; between runs restart the worker to clear any stuck tesseract procs:
    docker compose restart segment-worker

Prints "<done>/<total> OCRs in <secs>s" then os._exit(0) so a lingering deadlocked thread cannot
keep the process alive.
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytesseract
from pdf2image import convert_from_path

THREADS = 6
TASKS = 60
PAGES = 8
DPI = 120
WAIT_SECONDS = 90


def _first_pdf(folder: str) -> str:
    try:
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".pdf"):
                return os.path.join(folder, name)
    except FileNotFoundError:
        pass
    return ""


def main() -> None:
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else _first_pdf("/app/uploads")
    if not pdf_path:
        raise SystemExit(
            "usage: ocr_concurrency_hammer.py <pdf-path> (or drop a PDF in /app/uploads)"
        )

    print(
        f"OMP_THREAD_LIMIT={os.environ.get('OMP_THREAD_LIMIT', '(unset)')} "
        f"OMP_NUM_THREADS={os.environ.get('OMP_NUM_THREADS', '(unset)')}"
    )
    print(f"rasterizing {PAGES} page(s) @ {DPI}dpi from {pdf_path} ...")
    images = convert_from_path(pdf_path, first_page=1, last_page=PAGES, dpi=DPI)
    if not images:
        raise SystemExit("no pages rasterized")
    tasks = [images[i % len(images)] for i in range(TASKS)]

    start = time.monotonic()
    done = 0
    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        futures = [pool.submit(pytesseract.image_to_string, image) for image in tasks]
        try:
            for future in as_completed(futures, timeout=WAIT_SECONDS):
                future.result()
                done += 1
        except TimeoutError:
            print("TIMED OUT waiting for OCR - concurrency deadlock (OMP_THREAD_LIMIT unset?)")
    print(f"{done}/{TASKS} OCRs in {time.monotonic() - start:.1f}s")
    os._exit(0)


if __name__ == "__main__":
    main()
