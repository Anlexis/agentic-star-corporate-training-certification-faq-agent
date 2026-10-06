# Test Specification — EDU-C2-014 CorporateTrainingFAQAgent

## Test strategy

The suite is deterministic and offline: retrieval runs against the seeded catalog shipped
in `config/kb/training_catalog_kb.json` and answers are template-assembled, so no external
service or model is involved. Every test below ships in this repository.

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

| Layer | Location | Focus |
|---|---|---|
| Unit | `tests/unit/` | one node at a time, called directly |
| Boundary | `tests/proof_of_boundary/` | properties that must hold through the whole agent |

Two of the suite's habits are deliberate and worth keeping in a fork:

- **Contract tests call `execute()` directly.** A refusal asserted only through the full
  stack proves the stack refused, not that this template does. The direct call is what
  makes the guarantee hold wherever the platform gate is absent or configured off.
- **Boundary assertions are behavioural.** They assert an error status, an empty output and
  an absent `programs` field — never a particular gate's wording, which changes with the
  framework.

## Framework compliance

| TC-ID | Test | File | Expected result |
|---|---|---|---|
| TC-01 | State is a flat TypedDict with no credential-shaped fields | `tests/proof_of_boundary/test_state_safety.py` | AST scan: 0 violations |
| TC-02 | The default input gate cannot be overridden by a domain node | `tests/unit/test_framework_compliance_tc06_tc07.py` | `TypeError` at class definition |
| TC-03 | The default output gate cannot be overridden by a domain node | `tests/unit/test_framework_compliance_tc06_tc07.py` | `TypeError` at class definition |
| TC-04 | Every shipped node declares its required trust level explicitly | `tests/unit/test_framework_compliance_tc06_tc07.py` | 7/7 declare one |
| TC-05 | Both outer gate slots require the manifest's declared caller level | `tests/unit/test_framework_compliance_tc06_tc07.py` | both `VERIFIED_EXTERNAL` |

## Caller-data contract

| TC-ID | Test | File | Expected result |
|---|---|---|---|
| TC-10 | A normal question is normalised, not rejected | `tests/unit/test_input_validate.py` | whitespace collapsed, no error |
| TC-11 | Too-short question rejected; empty question degrades | `tests/unit/test_input_validate.py` | error / note |
| TC-12 | Over-long question truncated to the cap | `tests/unit/test_input_validate.py` | 2000 chars, note recorded |
| TC-13 | `top_k` outside 1–20, non-integer, or boolean | `tests/unit/test_input_validate.py` | fail closed, field named |
| TC-14 | `min_score` non-finite (`NaN`, `±Infinity`) or out of range | `tests/unit/test_input_validate.py` | fail closed; filters never written |
| TC-15 | `years_of_service` non-finite or out of range | `tests/unit/test_input_validate.py` | fail closed, field named |
| TC-16 | `category` / `language` outside the fixed sets | `tests/unit/test_input_validate.py` | fail closed |
| TC-17 | Profile identifiers that are not inert | `tests/unit/test_input_validate.py` | fail closed |
| TC-18 | A rejected value is never echoed into the error | `tests/unit/test_input_validate.py` | error names the field only |
| TC-19 | A non-mapping `input_context` | `tests/unit/test_input_validate.py` | fail closed |
| TC-20 | Instruction-override payloads refused at the node itself | `tests/unit/test_input_validate.py` | error; no question, no filters |
| TC-21 | Ordinary questions containing the same words are unaffected | `tests/unit/test_input_validate.py` | answered normally |

## Domain behaviour

| TC-ID | Test | File | Expected result |
|---|---|---|---|
| TC-30 | Language detection (ja / vi / en) and caller override | `tests/unit/test_query_normalize.py` | resolved language |
| TC-31 | Normalization: whitespace collapse, decomposed-to-composed | `tests/unit/test_query_normalize.py` | one spelling |
| TC-32 | Retrieval returns catalog matches above the relevance floor | `tests/unit/test_kb_retrieve.py` | scored, ordered records |
| TC-33 | Category / `top_k` / `min_score` overrides applied | `tests/unit/test_kb_retrieve.py` | result set changes |
| TC-34 | A configured value reaches the node through seeded state | `tests/unit/test_kb_retrieve.py` | run honours it |
| TC-35 | A missing or malformed catalog degrades, never raises | `tests/unit/test_kb_retrieve.py` | no coverage + note |
| TC-36 | A nonsense configured value falls back to the default | `tests/unit/test_kb_retrieve.py` | run still answers |
| TC-37 | Eligibility verdict: yes / no / conditional / unknown | `tests/unit/test_response_generate.py` | verdict per stated rules |
| TC-38 | An open dimension is not turned into a condition | `tests/unit/test_response_generate.py` | `yes` |
| TC-39 | The answer quotes the catalog record | `tests/unit/test_response_generate.py` | name, body, id, source present |
| TC-40 | The answer never echoes the caller's question | `tests/unit/test_response_generate.py` | marker absent |
| TC-41 | Localised rendering (ja / vi / en fallback) | `tests/unit/test_response_generate.py` | localised furniture |

## Output boundary

| TC-ID | Test | File | Expected result |
|---|---|---|---|
| TC-50 | Every blocked form is caught in prose | `tests/unit/test_output_validate.py` | error, answer dropped |
| TC-51 | The error names the form, not the matched text | `tests/unit/test_output_validate.py` | form name only |
| TC-52 | A leak inside the structured `programs` list is caught | `tests/unit/test_output_validate.py` | error |
| TC-53 | The screen walks nested structures and dict keys | `tests/unit/test_output_validate.py` | violation reported |
| TC-54 | Domain identifiers pass through byte-identical | `tests/unit/test_output_validate.py` | no false positive |
| TC-55 | Length cap and advisory escalation flag | `tests/unit/test_output_validate.py` | reject / flag only |
| TC-56 | A blocked answer leaves no released text in the error envelope | `tests/proof_of_boundary/test_pb_output_gate.py` | withheld notice as `output`, no `programs` |
| TC-57 | The withheld notice is non-empty — an empty value would re-open the `result` fallback | `tests/proof_of_boundary/test_pb_output_gate.py` | truthy notice; empty value measured to release the answer |
| TC-58 | Every answer-bearing state field is cleared, and the clearing covers everything the main slot maps | `tests/proof_of_boundary/test_pb_output_gate.py` | all cleared; no unaccounted field |
| TC-59 | The error envelope carries no matched text, form name, traceback or source path | `tests/proof_of_boundary/test_pb_output_gate.py` | location only |
| TC-60 | Containment end to end through the compiled agent, with a clean-path control | `tests/proof_of_boundary/test_pb_output_gate.py` | blocked withheld / clean released |

## Proof-of-Boundary tests

| PB-ID | Boundary | File | Expected result |
|---|---|---|---|
| PB-1 | Import isolation — no platform-internal SDK import in `src/` | `tests/proof_of_boundary/test_import_isolation.py` | AST scan: 0 violations |
| PB-2 | State safety — no credential-shaped field, no prohibited type | `tests/proof_of_boundary/test_state_safety.py` | AST scan: 0 violations |
| PB-3 | Invoke order — the backbone runs in the fixed order and reaches success | `tests/proof_of_boundary/test_pb_invoke_order.py` | exact `node_history` |
| PB-4 | The output boundary withholds a failing answer in all three representations | `tests/proof_of_boundary/test_pb_output_gate.py` | error, nothing released |
| PB-5 | No coverage means no invention — and coverage still works | `tests/proof_of_boundary/test_pb_out_of_scope.py` | explicit no-coverage answer |
| PB-6 | The escalation flag is advisory and never suppresses the answer | `tests/proof_of_boundary/test_pb_escalation_flag.py` | flag raised, answer delivered |
| PB-7 | Cross-boundary interrupt propagation | `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | conditional skip — not enabled for this template |
| PB-8 | End-to-end through the real ASGI `/invoke` | `tests/proof_of_boundary/test_invoke_e2e.py` | see below |

PB-3 drives the same payload as `deploy/invoke_payload.json`, and asserts that equality, so
the deployment smoke-check and the invoke-order proof can never drift apart.

### PB-8 — end-to-end coverage through `/invoke`

| Case | Expected result |
|---|---|
| A domain question | grounded catalog answer with a non-empty `programs` list |
| A different domain question | a different program set — the answer is computed, not fixed |
| An out-of-domain question | the explicit no-coverage answer, `programs` empty |
| `top_k` / `category` / `min_score` / `language` in `input_context` | the outcome changes — proof the context bridge delivers |
| Employee profile supplied / withheld / incomplete | every eligibility verdict reachable |
| Malformed caller parameter (11 cases) | error status, empty output, no `programs` |
| `NaN` / `Infinity` / `-Infinity` through raw JSON | fail closed |
| Instruction-override question | refused, no answer |
| Missing / wrong bearer credential | HTTP 401 |
| Oversized `input_context` | HTTP 413 |
| An identifier pasted into the question | absent from the entire response |
| Catalog identifiers in the answer | byte-identical |
