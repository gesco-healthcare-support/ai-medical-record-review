"""Shared OCR-variant definitions for the eval scripts (single source of "current" vs "tuned").

- current: the exact app path (extract_text_from_selected_pages) - Poppler default DPI + Tesseract
  defaults. This is what production uses today.
- tuned:   300 DPI + grayscale + projection-profile DESKEW + Otsu binarization + --oem 1 --psm 3.
  Deskew is the biggest OCR lever (research); the earlier "tuned" omitted it and underperformed, so
  this is the fair comparison. Dependency-free (numpy + PIL only; cv2/skimage absent in-container).

No Gemini here. Import from a /tmp eval script: `from ocr_variants import extract_current, extract_tuned`.
"""

import gc

import numpy as np
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from app.services.ocr import extract_text_from_selected_pages


def extract_current(pdf_path, st, en):
    """The production OCR path over pages [st, en]."""
    return extract_text_from_selected_pages(pdf_path, list(range(st, en + 1)))


def _otsu_threshold(gray):
    """Otsu's optimal global threshold for a uint8 grayscale array."""
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
    return best_t


def _deskew_angle(gray, limit=5.0, step=0.5):
    """Estimate page skew (degrees) by the projection-profile method: the angle whose horizontal
    ink-per-row profile has the sharpest peaks (max sum of squared row-to-row deltas) is the one
    that makes text lines horizontal. Runs on a downscaled ink mask for speed; the estimate is
    resolution-insensitive so it is applied to the full-res page afterward."""
    thr = _otsu_threshold(gray)
    mask = (gray < thr)  # ink = True
    # Downscale the mask so the angle sweep is cheap (angle estimate is robust to this).
    img = Image.fromarray((mask * 255).astype(np.uint8))
    scale = 800.0 / max(img.size)
    if scale < 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    small = np.array(img) > 0
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-limit, limit + step, step):
        rot = np.array(
            Image.fromarray((small * 255).astype(np.uint8)).rotate(angle, resample=Image.NEAREST, fillcolor=0)
        ) > 0
        proj = rot.sum(axis=1).astype(float)
        score = float(((proj[1:] - proj[:-1]) ** 2).sum())
        if score > best_score:
            best_score, best_angle = score, float(angle)
    return best_angle


def extract_tuned(pdf_path, st, en):
    """300 DPI + grayscale + deskew + Otsu + Tesseract --oem 1 --psm 3, one page at a time."""
    text = ""
    for p in range(st, en + 1):
        imgs = convert_from_path(pdf_path, first_page=p, last_page=p, dpi=300)
        for img in imgs:
            gray = np.array(img.convert("L"))
            angle = _deskew_angle(gray)
            if abs(angle) >= 0.5:
                gray = np.array(
                    Image.fromarray(gray).rotate(angle, resample=Image.BICUBIC, fillcolor=255)
                )
            binary = (gray > _otsu_threshold(gray)).astype(np.uint8) * 255
            text += pytesseract.image_to_string(Image.fromarray(binary), config="--oem 1 --psm 3")
        del imgs
        gc.collect()
    return text
