"""Shared configuration for the A1 page-stream-segmentation spike.

A1 = detect where each sub-document starts inside a large merged medical-record
PDF. This module centralises paths and constants so the OCR, labeling, and
training steps agree on where data lives.
"""
import os

# Tesseract is installed but not on the Git Bash PATH; point pytesseract at it.
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

EXP_ROOT = r"P:\MRR_AI_Source\Experiments\a1-segmentation"
OCR_CACHE = os.path.join(EXP_ROOT, "cache", "ocr")
FEAT_CACHE = os.path.join(EXP_ROOT, "cache", "features")
OUTPUTS = os.path.join(EXP_ROOT, "outputs")

# The three CSV-labeled cases (input merged PDF + human-corrected page-range CSV).
AI_SYSTEM_DIR = r"P:\MRR_AI_Source\MR Samples\AI System Samples"
CASES = ["Case 1", "Case 2", "Case 3"]

OCR_DPI = 200          # balance of OCR quality vs render/OCR speed
OCR_THREADS = 12       # tesseract shells out per call, so threads parallelise well
CHUNK_SIZE = 100       # the current pipeline's fixed chunk size (baseline to beat)
