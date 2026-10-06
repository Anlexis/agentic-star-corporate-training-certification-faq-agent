"""AgentCore Platform v1.0"""

# EDU-C2-014 — ResponseGenerateNode
# Domain node 4: assemble the answer from the retrieved catalog records and
# decide the eligibility verdict for the employee profile supplied by the
# caller.
#
# Node contract: extend FunctionNode; implement execute(self, state) -> dict —
# no `config` parameter. Return ONLY the fields this node changes (never full
# state). Never import from mediator/, api/, or other agents.
#
# Assembly rule (this template's output invariant): every rendered sentence
# about a program is quoted from a catalog record — program name,
# category, source, body excerpt, application procedure and deadline. Nothing
# is synthesised, and the caller's own question text is never echoed back into
# the answer, so no caller-controlled string can reach the rendered surface.
# The eligibility verdict is the one value this node computes, and it is
# computed only from the record's stated rules and the bounded profile
# attributes that cleared the caller-contract gate.
#
# The shipped build is deterministic: no model call, no network. Introducing
# generated prose here would have to keep the same invariant — quote the
# catalog, never the caller.

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

_DEFAULT_LANGUAGE = "en"

# Eligibility verdicts.
_YES = "yes"
_NO = "no"
_CONDITIONAL = "conditional"
_UNKNOWN = "unknown"

# Localised answer furniture. Vietnamese falls back to the English rendering
# for the body sections it has no dedicated wording for.
_LABELS: Dict[str, Dict[str, str]] = {
    "en": {
        "header": "# Corporate Training & Certification — Catalog Answer",
        "eligibility": "**Eligibility**",
        "procedure": "**How to apply**",
        "deadline": "**Deadline**",
        "programs": "## Matching programs",
        "sources": "## Sources",
        "no_coverage": (
            "The training catalog does not contain a program matching that question. "
            "Please contact the training team for anything outside the published catalog."
        ),
        "advisory": (
            "_Answers are assembled from the published training catalog. "
            "Confirm dates and conditions with the training team before you rely on them._"
        ),
        _YES: "Eligible",
        _NO: "Not eligible",
        _CONDITIONAL: "Conditionally eligible",
        _UNKNOWN: "Not assessed — no employee details were supplied",
    },
    "ja": {
        "header": "# 社内研修・資格取得 — カタログ回答",
        "eligibility": "**申請対象**",
        "procedure": "**申請方法**",
        "deadline": "**申請期限**",
        "programs": "## 該当プログラム",
        "sources": "## 出典",
        "no_coverage": (
            "ご質問に該当するプログラムは研修カタログに登録されていません。"
            "カタログ外の内容については研修担当までお問い合わせください。"
        ),
        "advisory": ("_本回答は公開されている研修カタログの記載に基づきます。日程と条件は研修担当にご確認ください。_"),
        _YES: "申請可能",
        _NO: "申請不可",
        _CONDITIONAL: "条件付きで申請可能",
        _UNKNOWN: "判定なし — 社員情報の指定がありません",
    },
    "vi": {
        "header": "# Đào tạo & Chứng chỉ nội bộ — Trả lời từ danh mục",
        "eligibility": "**Điều kiện tham gia**",
        "procedure": "**Cách đăng ký**",
        "deadline": "**Hạn đăng ký**",
        "programs": "## Chương trình phù hợp",
        "sources": "## Nguồn",
        "no_coverage": (
            "Danh mục đào tạo không có chương trình nào phù hợp với câu hỏi này. "
            "Vui lòng liên hệ bộ phận đào tạo cho các nội dung ngoài danh mục."
        ),
        "advisory": (
            "_Câu trả lời được tổng hợp từ danh mục đào tạo đã công bố. "
            "Vui lòng xác nhận thời hạn và điều kiện với bộ phận đào tạo._"
        ),
        _YES: "Đủ điều kiện",
        _NO: "Không đủ điều kiện",
        _CONDITIONAL: "Đủ điều kiện có điều kiện",
        _UNKNOWN: "Chưa đánh giá — không có thông tin nhân viên",
    },
}


def _labels(language: Optional[str]) -> Dict[str, str]:
    """Return the label set for *language*, defaulting to English."""
    if isinstance(language, str) and language in _LABELS:
        return _LABELS[language]
    return _LABELS[_DEFAULT_LANGUAGE]


def _profile_of(filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the validated employee profile, or None when none was supplied."""
    profile = filters.get("employee_profile")
    if isinstance(profile, dict) and any(value is not None for value in profile.values()):
        return profile
    return None


def _evaluate_eligibility(record: Dict[str, Any], profile: Optional[Dict[str, Any]]) -> str:
    """Decide the eligibility verdict for one catalog record.

    Only the record's own stated rules and the bounded profile attributes are
    consulted:

      unknown      — no employee profile was supplied, so nothing is claimed;
      no           — the profile contradicts at least one stated rule;
      conditional  — the record states a rule the profile does not answer;
      yes          — every stated rule is satisfied.

    A rule the record does not state is never invented: an empty department /
    role / grade list means the program is open on that dimension.
    """
    if profile is None:
        return _UNKNOWN

    rules = record.get("eligibility")
    if not isinstance(rules, dict):
        return _CONDITIONAL

    incomplete = False

    for field, key in (("department", "departments"), ("role", "roles"), ("grade", "grades")):
        allowed = rules.get(key)
        if not isinstance(allowed, list) or not allowed:
            continue  # open on this dimension
        value = profile.get(field)
        if value is None:
            incomplete = True
            continue
        if value not in [str(item).lower() for item in allowed if isinstance(item, str)]:
            return _NO

    min_years = rules.get("min_years_of_service")
    if isinstance(min_years, (int, float)) and not isinstance(min_years, bool) and min_years > 0:
        years = profile.get("years_of_service")
        if years is None:
            incomplete = True
        elif float(years) < float(min_years):
            return _NO

    return _CONDITIONAL if incomplete else _YES


def _compose_answer(
    records: List[Dict[str, Any]],
    verdict: str,
    procedure: str,
    deadline: str,
    language: str,
) -> str:
    """Render the answer body from the catalog records.

    Every line is quoted catalog content plus fixed localised furniture; the
    caller's question is deliberately not reproduced.
    """
    labels = _labels(language)
    sections: List[str] = [labels["header"], ""]
    sections.append(f"{labels['eligibility']}: {labels.get(verdict, labels[_UNKNOWN])}")
    if procedure:
        sections.append(f"{labels['procedure']}: {procedure}")
    if deadline:
        sections.append(f"{labels['deadline']}: {deadline}")

    sections.extend(["", labels["programs"], ""])
    for index, record in enumerate(records, 1):
        name = record.get("program_name", "")
        content = record.get("content", "")
        sections.append(f"- [{index}] **{name}** — {content}")

    sections.extend(["", labels["sources"], ""])
    for index, record in enumerate(records, 1):
        sections.append(f"- [{index}] {record.get('id', '')} — {record.get('source', '')}")

    sections.extend(["", labels["advisory"]])
    return "\n".join(sections)


class ResponseGenerateNode(FunctionNode):
    """Assemble the catalog answer and the eligibility verdict.

    Input state keys:
        retrieved_docs:    JSON list[dict] of scored catalog records
        query_filters:     JSON dict — validated employee profile
        detected_language: resolved answer language
        out_of_scope:      True when retrieval found nothing

    Output state keys (partial dict):
        answer:                rendered answer body
        result:                same text, for the outer post-process slot
        eligibility_result:    "yes" | "no" | "conditional" | "unknown"
        application_procedure: procedure quoted from the best match
        deadline_info:         deadline quoted from the best match
        programs:              JSON list[dict] of the rendered programs
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        language = state.get("detected_language") or _DEFAULT_LANGUAGE
        labels = _labels(language if isinstance(language, str) else _DEFAULT_LANGUAGE)
        records: List[Dict[str, Any]] = from_json(state.get("retrieved_docs"), []) or []
        records = [record for record in records if isinstance(record, dict)]

        # ------------------------------------------------------------------
        # No coverage: the catalog has nothing to quote, so nothing is
        # claimed. Still a SUCCESS — an honest "not in the catalog" is a valid
        # answer, and it is the only case where the body is fixed text.
        # ------------------------------------------------------------------
        if state.get("out_of_scope") or not records:
            body = f"{labels['header']}\n\n{labels['no_coverage']}\n\n{labels['advisory']}"
            emit_trace_event(
                "response_generate_complete",
                {"match_count": 0, "eligibility_result": _UNKNOWN, "language": language},
                state,
            )
            return {
                "answer": body,
                "result": body,
                "eligibility_result": _UNKNOWN,
                "application_procedure": "",
                "deadline_info": "",
                "programs": to_json([]),
                "status": AgentStatus.SUCCESS.value,
            }

        filters: Dict[str, Any] = from_json(state.get("query_filters"), {}) or {}
        profile = _profile_of(filters)

        best = records[0]
        verdict = _evaluate_eligibility(best, profile)
        procedure = str(best.get("application_procedure", "")).strip()
        deadline = str(best.get("deadline", "")).strip()

        body = _compose_answer(
            records=records,
            verdict=verdict,
            procedure=procedure,
            deadline=deadline,
            language=language if isinstance(language, str) else _DEFAULT_LANGUAGE,
        )

        programs = [
            {
                "id": record.get("id", ""),
                "program_name": record.get("program_name", ""),
                "category": record.get("category", ""),
                "source": record.get("source", ""),
                "eligibility": _evaluate_eligibility(record, profile),
            }
            for record in records
        ]

        logger.info(
            "ResponseGenerateNode: language=%s verdict=%s programs=%d answer_chars=%d",
            language,
            verdict,
            len(programs),
            len(body),
        )
        emit_trace_event(
            "response_generate_complete",
            {"match_count": len(records), "eligibility_result": verdict, "language": language},
            state,
        )

        return {
            "answer": body,
            "result": body,
            "eligibility_result": verdict,
            "application_procedure": procedure,
            "deadline_info": deadline,
            "programs": to_json(programs),
            "status": AgentStatus.SUCCESS.value,
        }
