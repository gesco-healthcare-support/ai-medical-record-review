"""EXP-001: TF-IDF + Logistic Regression (the fast baseline experiment).

This is the simplest text-only approach: it tells us whether plain word features
carry any document-boundary signal. Thin wrapper over the shared pipeline.

Run:  python src/train_eval.py          # the 3 clean (CSV) cases
      python src/train_eval.py --all     # all OCR'd cases (3 or 11)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline
from featurizers import TfidfFeaturizer
from cases import CSV_CASE_IDS, ALL_CASE_IDS

if __name__ == "__main__":
    case_ids = ALL_CASE_IDS if "--all" in sys.argv else CSV_CASE_IDS
    method = ("Each page is represented by TF-IDF over its own text plus the "
              "previous page's text (1-2 word grams); a class-balanced Logistic "
              "Regression predicts 'does a new document start on this page?'. "
              "TF-IDF is the fast first feature set; word embeddings are EXP-002.")
    pipeline.run("EXP-001-tfidf-lr", "TF-IDF + Logistic Regression",
                 method, TfidfFeaturizer, case_ids)
