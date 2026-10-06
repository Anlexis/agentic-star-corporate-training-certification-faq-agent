"""AgentCore Platform v1.0"""

# EDU-C2-014 — QueryNormalizeNode
# Domain node 2: language resolution and query normalization.
#
# Node contract: extend FunctionNode; implement execute(self, state) -> dict —
# no `config` parameter. Return ONLY the fields this node changes (never full
# state). Never import from mediator/, api/, or other agents.
#
# Detects Japanese / Vietnamese / English from the validated question via
# unicode-range heuristics, applies NFC normalization, collapses whitespace,
# and lowercases the query for English. A caller-selected language (already
# validated against the fixed set by InputValidateNode) always wins over the
# heuristic; a detected language outside the configured supported set falls
# back to English.
#
# Deterministic heuristics only — no model call, no network, no side effects.
# The normalized query drives retrieval; it is never rendered into the answer.

import logging
import unicodedata
from typing import Any, ClassVar, Dict, List, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json

logger = logging.getLogger(__name__)

# Defaults mirror the `answer.supported_languages` block in config/config.yaml.
_DEFAULT_SUPPORTED_LANGUAGES: List[str] = ["ja", "vi", "en"]
_FALLBACK_LANGUAGE = "en"

# CJK Unified Ideographs and CJK-adjacent ranges that indicate Japanese text.
_CJK_RANGES: List[Tuple[int, int]] = [
    (0x3000, 0x9FFF),  # Hiragana, Katakana, CJK Unified Ideographs, misc
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x20000, 0x2A6DF),  # CJK Extension B
]

# Latin Extended and combining diacritic ranges common in Vietnamese, which
# uses Latin script with a rich set of combining marks and precomposed letters.
_VI_RANGES: List[Tuple[int, int]] = [
    (0x00C0, 0x024F),  # Latin Extended-A/B (includes accented letters)
    (0x1E00, 0x1EFF),  # Latin Extended Additional (core Vietnamese block)
    (0x0300, 0x036F),  # Combining Diacritical Marks
]

# Fraction of characters in the Vietnamese ranges needed to classify as "vi".
_VI_RATIO_THRESHOLD = 0.05


def _has_cjk(text: str) -> bool:
    """Return True if any character in *text* falls within a CJK range."""
    return any(any(low <= ord(ch) <= high for low, high in _CJK_RANGES) for ch in text)


def _vi_score(text: str) -> int:
    """Count how many characters fall in Vietnamese-specific unicode ranges."""
    count = 0
    for ch in text:
        codepoint = ord(ch)
        for low, high in _VI_RANGES:
            if low <= codepoint <= high:
                count += 1
                break
    return count


def _detect_language(text: str, supported_languages: List[str]) -> str:
    """Detect the language of *text* using unicode-range heuristics.

    Checks in priority order: ja -> vi -> en. A detected language outside
    *supported_languages* falls back to English.
    """
    if not text:
        return _FALLBACK_LANGUAGE

    if _has_cjk(text):
        language = "ja"
    elif _vi_score(text) / len(text) >= _VI_RATIO_THRESHOLD:
        language = "vi"
    else:
        language = _FALLBACK_LANGUAGE

    if language not in supported_languages:
        language = _FALLBACK_LANGUAGE
    return language


def _normalize_query(raw: str, language: str) -> str:
    """NFC-normalize, collapse whitespace, and lowercase English queries."""
    normalized = " ".join(unicodedata.normalize("NFC", raw).split())
    if language == _FALLBACK_LANGUAGE:
        normalized = normalized.lower()
    return normalized


def _supported_languages(state: AgentState) -> List[str]:
    """Effective supported-language list: state-seeded answer_config > defaults.

    No `config` parameter is read here — execute() takes only `state`. The
    runtime `answer` block reaches this node exclusively via the state-seeded
    `answer_config` field.
    """
    seeded = from_json(state.get("answer_config"), None)
    if isinstance(seeded, dict):
        configured = seeded.get("supported_languages")
        if isinstance(configured, list):
            valid = [item for item in configured if isinstance(item, str) and item]
            if valid:
                return valid
    return list(_DEFAULT_SUPPORTED_LANGUAGES)


class QueryNormalizeNode(FunctionNode):
    """Resolve the answer language and normalize the question for retrieval.

    Input state keys:
        question:      validated question text (written by InputValidateNode)
        query_filters: JSON dict — a caller-selected `language` wins over the
                       heuristic
        answer_config: JSON dict — supported_languages

    Output state keys (partial dict):
        normalized_query:  NFC-normalized, whitespace-collapsed query
        detected_language: "ja", "vi" or "en"
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        raw_question = state.get("question") or ""
        if not isinstance(raw_question, str) or not raw_question.strip():
            # An empty request is not an error here: the pipeline degrades to
            # the explicit no-coverage answer rather than refusing outright.
            return {
                "normalized_query": "",
                "detected_language": _FALLBACK_LANGUAGE,
                "status": AgentStatus.SUCCESS.value,
            }

        supported = _supported_languages(state)
        filters: Dict[str, Any] = from_json(state.get("query_filters"), {}) or {}
        selected = filters.get("language")

        if isinstance(selected, str) and selected in supported:
            language = selected
        else:
            language = _detect_language(raw_question, supported)

        normalized = _normalize_query(raw_question, language)

        logger.info(
            "QueryNormalizeNode: language=%s normalized_length=%d",
            language,
            len(normalized),
        )

        emit_trace_event(
            "query_normalize_complete",
            {"language": language, "normalized_length": len(normalized)},
            state,
        )

        return {
            "normalized_query": normalized,
            "detected_language": language,
            "status": AgentStatus.SUCCESS.value,
        }
