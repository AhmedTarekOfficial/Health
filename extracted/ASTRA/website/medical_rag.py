"""
OrbitNexus – Medical retrieval layer (RAG)
==========================================
Reusable text index. The live chat supplies approved web passages through
trusted_web_rag.py. The bundled JSON corpus remains available for standalone use.

Design notes
------------
* Pure scikit-learn TF-IDF (already a project dependency): no vector database,
  no network calls, no extra API keys.
* Entries live in ``medical_knowledge/*.json`` so the reference set can be
  extended by dropping in another file. Each entry needs ``title`` and ``text``;
  ``id``, ``source``, ``url`` and ``keywords`` are optional. Keywords are indexed
  but never shown to the model, which lets Arabic search terms map onto English
  reference text.
* Retrieval is deliberately defensive: an empty or broken knowledge base must
  never break chat, and a weak match must not inject noise.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.pipeline import FeatureUnion

DEFAULT_KB_DIR = Path(__file__).resolve().parent / "medical_knowledge"
DEFAULT_LIMIT = 3
DEFAULT_MIN_SCORE = 0.10
# A hit scoring far below the best match is usually a stray keyword overlap.
RELATIVE_SCORE_FLOOR = 0.55
DEFAULT_MAX_CHARS = 1800

# Arabic text is normalised before indexing/searching so that common spelling
# variants and the definite article do not hide a match ("الحساسية" vs "حساسية").
_ARABIC_MARKS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")
_ARABIC_PREFIXES = re.compile(r"\b(?:وال|بال|كال|فال|ال)")


def normalize_text(text: str) -> str:
    """Lowercase plus light Arabic normalisation (matching only, never displayed)."""
    text = text.lower()
    text = _ARABIC_MARKS.sub("", text)
    text = re.sub(r"[أإآ]", "ا", text)
    text = text.replace("ى", "ي").replace("ة", "ه")
    return _ARABIC_PREFIXES.sub("", text)


class MedicalRAG:
    """Retrieval over supplied passages or the bundled reference corpus."""

    def __init__(self, directory: Optional[Path] = None, *, entries=None) -> None:
        self.directory = Path(directory) if directory else DEFAULT_KB_DIR
        self._lock = threading.Lock()
        self._entries: List[Dict[str, Any]] = []
        self._pipeline = None
        self._matrix = None
        if entries is None:
            self._load()
        else:
            self._index(entries)

    # ── indexing ────────────────────────────────────────────────────────────
    @staticmethod
    def _search_text(entry: Dict[str, Any]) -> str:
        """Text used for matching only (keywords are not sent to the LLM)."""
        # Title and keywords are repeated to weight them above body text.
        parts = [entry["title"], entry["title"], entry["keywords"], entry["keywords"], entry["text"]]
        return "\n".join(part for part in parts if part)

    def _read_file(self, path: Path) -> List[Dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"[RAG] Skipping {path.name}: {exc}")
            return []

        items = payload.get("entries") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            print(f"[RAG] Skipping {path.name}: expected a list of entries")
            return []

        entries: List[Dict[str, Any]] = []
        for position, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            text = str(item.get("text") or "").strip()
            if not title or not text:
                continue
            keywords = item.get("keywords") or []
            if isinstance(keywords, list):
                keywords = " ".join(str(word) for word in keywords)
            entries.append(
                {
                    "id": str(item.get("id") or f"{path.stem}-{position}"),
                    "title": title,
                    "source": str(item.get("source") or "OrbitNexus medical reference"),
                    "url": item.get("url"),
                    "text": text,
                    "keywords": str(keywords),
                }
            )
        return entries

    def _load(self) -> None:
        if not self.directory.is_dir():
            print(f"[RAG] Knowledge base directory not found: {self.directory}")
            return

        entries: List[Dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json")):
            entries.extend(self._read_file(path))

        self._index(entries)

    def _index(self, entries) -> None:
        """Index supplied documents too, without mixing in the bundled corpus."""
        self._entries = entries
        if not entries:
            print("[RAG] No references indexed.")
            return

        pipeline = FeatureUnion(
            [
                # Word n-grams handle English terms and exact Arabic keywords.
                (
                    "word",
                    TfidfVectorizer(
                        ngram_range=(1, 2), sublinear_tf=True, preprocessor=normalize_text
                    ),
                ),
                # Character n-grams add tolerance for morphology and typos.
                (
                    "char",
                    TfidfVectorizer(
                        analyzer="char_wb",
                        ngram_range=(3, 5),
                        sublinear_tf=True,
                        preprocessor=normalize_text,
                    ),
                ),
            ]
        )
        try:
            matrix = pipeline.fit_transform([self._search_text(entry) for entry in entries])
        except ValueError as exc:
            print(f"[RAG] Could not index the medical knowledge base: {exc}")
            return

        self._pipeline, self._matrix = pipeline, matrix
        print(f"[RAG] Indexed {len(entries)} reference passages")

    # ── retrieval ───────────────────────────────────────────────────────────
    @property
    def ready(self) -> bool:
        return self._pipeline is not None and bool(self._entries)

    def search(
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> List[Dict[str, Any]]:
        """Return the best-matching entries (highest score first)."""
        query = (query or "").strip()
        if not self.ready or len(query) < 3:
            return []

        with self._lock:
            vector = self._pipeline.transform([query])
            scores = cosine_similarity(vector, self._matrix).ravel()

        ranked = scores.argsort()[::-1]
        best = float(scores[ranked[0]]) if len(ranked) else 0.0
        if best < min_score:
            return []
        floor = max(min_score, best * RELATIVE_SCORE_FLOOR)

        matches: List[Dict[str, Any]] = []
        for index in ranked:
            score = float(scores[index])
            if score < floor or len(matches) >= max(1, limit):
                break
            entry = dict(self._entries[int(index)])
            entry["score"] = round(score, 4)
            matches.append(entry)
        return matches

    def format_context(
        self,
        matches: List[Dict[str, Any]],
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> str:
        """Render retrieved entries as a system-prompt block ('' when empty)."""
        if not matches:
            return ""

        lines = [
            "[Medical Reference Context]",
            "Reference material retrieved from the onboard medical knowledge base. Use it when it "
            "answers the question and name the source briefly, for example [NASA HRP], instead of "
            "inventing citations. If it does not cover the question, answer from established "
            "medical knowledge; do not refuse because telemetry lacks the value.",
        ]
        used = 0
        for number, entry in enumerate(matches, start=1):
            block = f"{number}. {entry['title']} — {entry['source']}\n   {entry['text']}"
            if used + len(block) > max_chars:
                break
            lines.append(block)
            used += len(block)
        return "\n".join(lines)

    def build_context(
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
        min_score: float = DEFAULT_MIN_SCORE,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> str:
        """Convenience wrapper: search then format."""
        return self.format_context(self.search(query, limit=limit, min_score=min_score), max_chars)

    @staticmethod
    def sources(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compact source list for API responses."""
        return [
            {
                "id": entry["id"],
                "title": entry["title"],
                "source": entry["source"],
                "url": entry.get("url"),
                "score": entry["score"],
            }
            for entry in matches
        ]
