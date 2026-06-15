"""Title-to-category matching (difflib fuzzy match against the taxonomy)."""

import re
from difflib import SequenceMatcher


def normalize(text):
    return re.sub(r"[^a-zA-Z0-9\s]", "", text).strip().lower()


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def categorize_documents(title, categories, threshold=0.65):
    if not isinstance(title, str):
        print(f"Warning: Invalid title encountered: {title}")
        title = "Unknown"  # Fallback value for invalid or missing titles
    normalized_title = normalize(title)
    best_match = None
    best_group = None
    highest_similarity = 0

    # Check each category
    for group, docs in categories.items():
        for doc in docs:
            normalized_doc = normalize(doc)
            sim = similarity(normalized_title, normalized_doc)
            if sim > highest_similarity and sim >= threshold:
                highest_similarity = sim
                best_match = doc  # noqa: F841
                best_group = group

    # If no group is found with sufficient similarity, assign to "Group 100"
    if not best_group:
        best_group = "100"

    return best_group
