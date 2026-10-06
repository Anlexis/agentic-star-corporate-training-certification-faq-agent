"""AgentCore Platform v1.0"""

# Service layer: domain queries and data access for the training catalog.
# Must NOT contain business logic, routing, or credentials. Nodes call this;
# this reads the seeded catalog file shipped with the template.
#
# The shipped build is deterministic and network-free: the catalog is a JSON
# file (config/kb/training_catalog_kb.json) and matching is keyword scoring.
# The contract below is store-agnostic on purpose — swapping in a vector store
# or a live catalog API replaces this module's internals only, leaving the
# node code and the retrieved-record shape unchanged.

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Repo root: src/services/catalog_service.py -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Minimal stopword set for query tokenisation (deterministic, no NLP deps).
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "be",
        "with",
        "under",
        "what",
        "which",
        "when",
        "how",
        "who",
        "do",
        "does",
        "did",
        "must",
        "should",
        "before",
        "after",
        "by",
        "at",
        "from",
        "that",
        "this",
        "it",
        "as",
        "was",
        "were",
        "can",
        "may",
        "any",
        "about",
        "there",
        "have",
        "has",
        "get",
        "you",
        "your",
        "our",
        "am",
        "into",
        "than",
        "then",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Per-field match weights: a query token found in the program name counts
# for more than one found only in the body content.
_NAME_WEIGHT = 1.0
_TAG_WEIGHT = 0.8
_CONTENT_WEIGHT = 0.5

# Excerpt length carried into the retrieved records (keeps State small).
_EXCERPT_CHARS = 400


def _singular(token: str) -> str:
    """Fold a simple English plural onto its singular form.

    Deliberately minimal and deterministic — no stemmer dependency. Without
    it, "programs" in a question would not match "program" in a catalog
    record, which is the difference between a grounded answer and a
    no-coverage reply for an ordinary phrasing.
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens, stopwords and 1-2 char noise removed."""
    return [_singular(t) for t in _TOKEN_RE.findall(text.lower()) if len(t) > 2 and t not in _STOPWORDS]


def load_catalog(kb_path: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Load the seeded catalog JSON.

    Returns (records, notes). A missing, unreadable or malformed file degrades
    to an empty catalog with an explanatory note rather than raising — the
    pipeline then answers with the explicit no-coverage message instead of
    failing the whole request.
    """
    notes: List[str] = []
    path = Path(kb_path)
    if not path.is_absolute():
        path = _REPO_ROOT / path
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        notes.append("catalog unavailable — answering without catalog coverage.")
        return [], notes
    if not isinstance(loaded, list):
        notes.append("catalog malformed — answering without catalog coverage.")
        return [], notes
    return [record for record in loaded if isinstance(record, dict)], notes


def score_record(record: Dict[str, Any], query_tokens: List[str]) -> float:
    """Score one catalog record against the tokenised query.

    Deterministic weighted overlap, normalised to 0.0-1.0 by the number of
    distinct query tokens so the score does not drift with query length.
    """
    if not query_tokens:
        return 0.0

    name_tokens = set(tokenize(str(record.get("program_name", ""))))
    tag_tokens = {_singular(str(tag).lower()) for tag in record.get("tags", []) if isinstance(tag, str)}
    content_tokens = set(tokenize(str(record.get("content", ""))))

    distinct = set(query_tokens)
    total = 0.0
    for token in distinct:
        if token in name_tokens:
            total += _NAME_WEIGHT
        elif token in tag_tokens:
            total += _TAG_WEIGHT
        elif token in content_tokens:
            total += _CONTENT_WEIGHT
    return total / len(distinct)


def excerpt(record: Dict[str, Any]) -> str:
    """Return the record's body text, capped at the excerpt length."""
    content = str(record.get("content", "")).strip()
    if len(content) <= _EXCERPT_CHARS:
        return content
    return content[:_EXCERPT_CHARS].rstrip() + "..."
