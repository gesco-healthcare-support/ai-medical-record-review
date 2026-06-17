"""EXP-002: sentence-embeddings + Logistic Regression (the actual A1 hypothesis).

Replaces EXP-001's surface-word TF-IDF with semantic embeddings of the current
and previous page. If meaning-aware features carry the boundary signal that words
did not, this is where we see it. Thin wrapper over the shared pipeline.

Run:  python src/exp002.py          # the 3 clean (CSV) cases (compare vs EXP-001)
      python src/exp002.py --all     # all OCR'd cases (after ocr_prep finishes)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline
from cases import ALL_CASE_IDS, CSV_CASE_IDS
from featurizers import EmbeddingFeaturizer

if __name__ == "__main__":
    case_ids = ALL_CASE_IDS if "--all" in sys.argv else CSV_CASE_IDS
    method = ("Each page's own text and the previous page's text are embedded "
              "separately with the all-MiniLM-L6-v2 sentence-transformer "
              "(384-dim each, concatenated to 768-dim, CPU); a class-balanced "
              "Logistic Regression predicts 'does a new document start here?'. "
              "Unlike EXP-001's word counts, embeddings capture meaning.")
    pipeline.run("EXP-002-embed-lr", "MiniLM embeddings + Logistic Regression",
                 method, EmbeddingFeaturizer, case_ids)
