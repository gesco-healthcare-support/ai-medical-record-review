"""Featurizers turn (current_page, previous_page) text pairs into numeric vectors.

Both expose sklearn-style fit_transform/transform so pipeline.run() can swap them
freely. Add a new one here to try a new representation.
"""
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


class TfidfFeaturizer:
    """Bag-of-words TF-IDF over 'current [SEP] previous' page text.

    Fast and dependency-light, but only sees surface words and suffers on noisy
    OCR. Must be fit on the training fold only (it learns the vocabulary).
    """

    def __init__(self):
        self.vec = TfidfVectorizer(min_df=2, ngram_range=(1, 2),
                                   sublinear_tf=True, max_features=50000)

    @staticmethod
    def _join(pairs):
        return [cur + "\n[SEP]\n" + prev for cur, prev in pairs]

    def fit_transform(self, pairs):
        return self.vec.fit_transform(self._join(pairs))

    def transform(self, pairs):
        return self.vec.transform(self._join(pairs))


class EmbeddingFeaturizer:
    """Sentence-transformer embeddings of the current AND previous page,
    concatenated into one vector (so the model sees both sides of a boundary).

    Stateless (the encoder is pretrained), so fit_transform == transform. The
    model and a per-text cache are class-level, so each unique page is encoded
    once even though it appears in many CV folds.
    """

    _model = None
    _cache = {}

    def __init__(self, model_name="all-MiniLM-L6-v2"):
        self.model_name = model_name
        if EmbeddingFeaturizer._model is None:
            from sentence_transformers import SentenceTransformer
            print(f"[embed] loading {model_name} (first run downloads weights) ...", flush=True)
            EmbeddingFeaturizer._model = SentenceTransformer(model_name)

    def _encode(self, texts):
        cache = EmbeddingFeaturizer._cache
        uniq = [t for t in dict.fromkeys(texts) if t not in cache]
        if uniq:
            vecs = EmbeddingFeaturizer._model.encode(
                uniq, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
            for t, v in zip(uniq, vecs, strict=True):
                cache[t] = np.asarray(v, dtype=np.float32)
        return np.vstack([cache[t] for t in texts])

    def transform(self, pairs):
        cur = self._encode([c for c, _ in pairs])
        prev = self._encode([p for _, p in pairs])
        return np.hstack([cur, prev])

    def fit_transform(self, pairs):
        return self.transform(pairs)
