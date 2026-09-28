"""Stage 3: find likely duplicate pairs among open issues (runs locally, no API)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .text import dedupe_body
from .util import log

TFIDF_THRESHOLD = 0.55
EMBED_THRESHOLD = 0.86
TITLE_WEIGHT = 0.6
MAX_MATCHES_PER_ISSUE = 5
_NUM_RE = re.compile(r"\d+(?:[.\-_]\d+)*")
_PLATFORM_RE = re.compile(r"\b(windows|linux|macos|mac ?os|ios|ipados|android|web|fuchsia|metal|vulkan|opengl|gles"
                          r"|x64|x86|arm64|armv7|arm)\b")


def is_series(title_a: str, title_b: str) -> bool:
    """Titles that differ only in numbers ("Release 3.47.5" / "Release 3.47.6", "CVE-2026-1" / "CVE-2026-2")
    or only in a platform name ("... on Windows" / "... on Linux") are sibling issues filed on purpose
    (releases, backports, per-version or per-platform tracking), not duplicates."""
    a, b = title_a.strip().lower(), title_b.strip().lower()
    if a == b:
        return False

    def norm(t):
        t = _PLATFORM_RE.sub("@", _NUM_RE.sub("#", t))
        return " ".join(re.sub(r"[^\w@#]+", " ", t).split())  # ignore punctuation such as `backticks`

    return norm(a) == norm(b)


def find_duplicates(issues: list[dict], profile, *, threshold: float | None = None, embeddings: bool = False,
                    include_skipped_kinds: bool = False) -> dict:
    import numpy as np

    keep = [i for i in issues
            if include_skipped_kinds or profile.kind(i["labels"], i.get("issue_type")) not in profile.dedupe_skip_kinds]
    method = "embeddings (all-MiniLM-L6-v2)" if embeddings else f"TF-IDF cosine similarity (title weight {TITLE_WEIGHT})"
    threshold = threshold or (EMBED_THRESHOLD if embeddings else TFIDF_THRESHOLD)
    result = {"method": method, "threshold": threshold, "compared": len(keep), "pairs": []}
    if len(keep) < 2:
        return result
    numbers = [i["number"] for i in keep]

    if embeddings:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError("Embeddings need: pip install 'issue-triage[embeddings]'") from e
        log("Embedding issues with all-MiniLM-L6-v2 (downloads ~90 MB the first time)…")
        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        mat = np.asarray(model.encode([i["title"] + ". " + dedupe_body(i.get("body")) for i in keep],
                                      batch_size=64, normalize_embeddings=True), dtype=np.float32)
        sims_for = lambda s, e: mat[s:e] @ mat.T  # noqa: E731
    else:
        from sklearn.feature_extraction.text import TfidfVectorizer
        tok = r"(?u)\b[A-Za-z_][A-Za-z0-9_]{2,}\b"

        def fit(texts, **kw):
            try:
                return TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True,
                                       token_pattern=tok, **kw).fit_transform(texts).astype(np.float32).tocsr()
            except ValueError:  # tiny or empty corpus: no usable vocabulary
                return None

        tmat = fit([i["title"] for i in keep])
        bodies = [dedupe_body(i.get("body")) for i in keep]
        bmat = fit(bodies, min_df=2, max_df=0.3)
        if bmat is None:  # small repos: relax the limits, but still ignore text most issues share (templates)
            bmat = fit(bodies, max_df=0.5)
        if tmat is None and bmat is None:
            return result
        t_t = tmat.T.tocsc() if tmat is not None else None
        b_t = bmat.T.tocsc() if bmat is not None else None
        has_body = (np.diff(bmat.indptr) > 0) if bmat is not None else np.zeros(len(keep), bool)

        def sims_for(s, e):
            # Title and description are compared separately so shared templates can't dominate.
            # When either description has nothing distinctive to compare, the title decides alone.
            t = (tmat[s:e] @ t_t).toarray() if tmat is not None else np.zeros((e - s, len(keep)), np.float32)
            if bmat is None:
                return t
            b = (bmat[s:e] @ b_t).toarray()
            both = np.outer(has_body[s:e], has_body)
            return np.where(both, TITLE_WEIGHT * t + (1 - TITLE_WEIGHT) * b, t)

    pairs = []
    chunk = 1000
    for start in range(0, len(keep), chunk):
        sims = sims_for(start, min(start + chunk, len(keep)))
        for r in range(sims.shape[0]):
            i = start + r
            row = sims[r]
            row[: i + 1] = 0
            hits = np.nonzero(row >= threshold)[0]
            kept = 0
            for j in hits[np.argsort(-row[hits])]:
                if kept >= MAX_MATCHES_PER_ISSUE:
                    break
                if is_series(keep[i]["title"], keep[j]["title"]):
                    result["series_skipped"] = result.get("series_skipped", 0) + 1
                    continue
                pairs.append((numbers[i], numbers[j], round(float(row[j]), 3)))
                kept += 1
        if len(keep) > chunk:
            log(f"  compared {min(start + chunk, len(keep)):,}/{len(keep):,}")
    pairs.sort(key=lambda p: -p[2])
    result["pairs"] = [{"a": a, "b": b, "similarity": s} for a, b, s in pairs]
    return result


def save(result: dict, data_dir: Path) -> None:
    (data_dir / "duplicates.json").write_text(json.dumps(result))


def load(data_dir: Path) -> dict:
    p = data_dir / "duplicates.json"
    return json.loads(p.read_text()) if p.exists() else {"pairs": [], "method": None}
