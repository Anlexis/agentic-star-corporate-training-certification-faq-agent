# Template Design Specification — EDU-C2-014 CorporateTrainingFAQAgent

## Position in the framework architecture

- **Agent class**: `CorporateTrainingFAQAgent` (`src/graph/graph.py`)

| Position | Value |
|---|---|
| L1 Base (framework base class) | AgentBaseGraph — direct framework inheritance |

- **Three-layer separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: framework inheritance, `execute(self, state) -> dict` override only
  - Graph: composition (`register_nodes()` for node substitution)

The template is Cat 2: a two-layer nested graph. The fixed outer backbone is untouched,
and the whole domain workflow lives inside an inner graph reached through the `main` slot.

## Architecture overview

### Outer backbone (framework-fixed — `add_edges()` is never overridden)

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                    ↓ (retry, bounded by max_retry)
                                 pre_process
```

| Slot | Class | Responsibility |
|---|---|---|
| initialize | framework default | schema version, session id, caller trust level |
| pre_process | `PreProcessNode` | reject empty input; redact direct identifiers from the question; lock the caller's channel label to an inert identifier |
| main | `TrainingFAQGraphNode` (GraphNode) | forward the runtime config and the caller's `input_context` into the inner graph; map its result back |
| post_process | `PostProcessNode` | screen the answer that is about to become `output` |
| finalize | framework default | response metadata, total time |

### Inner domain workflow (`DomainWorkflowGraph`, `src/graph/domain_workflow_graph.py`)

```
START → input_validate → query_normalize → kb_retrieve → response_generate
      → output_validate → END
```

| Node | Reads | Writes |
|---|---|---|
| `InputValidateNode` | `validated_input`, `input_context` | `question`, `query_filters`, `intake_notes` |
| `QueryNormalizeNode` | `question`, `query_filters`, `answer_config` | `normalized_query`, `detected_language` |
| `KBRetrieveNode` | `normalized_query`, `query_filters`, `retrieval_config` | `retrieved_docs`, `out_of_scope` |
| `ResponseGenerateNode` | `retrieved_docs`, `query_filters`, `detected_language` | `answer`, `result`, `eligibility_result`, `application_procedure`, `deadline_info`, `programs` |
| `OutputValidateNode` | `answer`, `programs`, `answer_config` | `is_valid_output`, `hr_escalation` |

The topology is linear. A node that sets an error status short-circuits the rest of the
pipeline, so nothing downstream ever runs on unvalidated data.

### How configuration reaches the nodes

Node `execute()` takes only `state` — the framework's node wrapper passes nothing else. A
value declared in `config/config.yaml` therefore reaches a node only if it is seeded into
state:

```
config/config.yaml
  → CorporateTrainingFAQAgent(config=runtime_config())        (src/api/server.py)
  → TrainingFAQGraphNode._parent_config()                     (retrieval + answer blocks)
  → DomainWorkflowGraph(config=...)
  → DomainWorkflowGraph._extra_initial_state()                (retrieval_config, answer_config)
  → the consuming node reads the state field
```

Each consuming node also carries module defaults mirroring the shipped `config.yaml`, so an
unreadable or partial config degrades to a documented value instead of silently becoming
`{}`.

### The `input_context` bridge

The framework's `GraphNode.execute()` invokes the inner graph without forwarding
`input_context`, so a plain inner read of `state["input_context"]` would always see `{}`.
`src/graph/context_bridge.py` closes the gap with a ContextVar:
`TrainingFAQGraphNode.extract_input()` stashes the caller's context immediately before the
inner invoke, and `DomainWorkflowGraph._extra_initial_state()` reads it back. A ContextVar
keeps the hand-off correct per thread/task, so concurrent invocations cannot see each
other's context.

## Caller-data contract

`POST /invoke` accepts the question as `input` and the structured parameters as
`input_context`. `InputValidateNode` is the contract gate — fail-closed, and a rejected
value is never echoed into an error, a log or the answer.

| Field | Type | Rule |
|---|---|---|
| `category` | string | one of `leadership`, `compliance`, `technical_skills`, `certification`, `reskilling` |
| `language` | string | one of `ja`, `vi`, `en` |
| `top_k` | integer | 1–20; booleans rejected explicitly (`isinstance(True, int)` is `True` in Python) |
| `min_score` | number | finite, 0.0–1.0 |
| `employee_profile.department` / `.role` / `.grade` | string | inert identifier, `[a-z0-9_]{1,32}` |
| `employee_profile.years_of_service` | number | finite, 0–60 |

**Finite is load-bearing.** `NaN` and `±Infinity` survive `float()`, and Python's `json`
accepts them as bare literals in a request body. Every comparison against `NaN` is `False`,
so an unchecked non-finite `min_score` would disable the relevance floor and an unchecked
`years_of_service` would skip the tenure rule — the exact decisions this template exists to
make. Both go through a finite-and-bounded parser.

**Inert is load-bearing.** Every caller string that selects behaviour reaches a filter and
an audit event, so free text there would be log and output injection. Each is locked to an
inert identifier before it is used.

The adapter additionally caps the serialized `input_context` at 256 KB.

Instruction-override payloads (text addressed to the model rather than the catalog) are
refused by `InputValidateNode` itself. The platform input gate refuses them too, but the
template does not depend on that: where that gate is absent or configured off, an unchecked
payload would reach the answer path and return success. The refusal is asserted in the test
suite by calling `execute()` directly, with no wrapper in front, and it is stated in terms
of behaviour — error status, no answer, no programs — never a gate's wording.

## Output invariant

> No released representation of an answer carries a credential-shaped token or a direct
> personal identifier.

"Every representation" is the load-bearing part. An answer leaves this agent in three
forms, and the same screen (`screen_text` / `screen_value` in
`src/nodes/output_validate.py`) runs on all three:

1. the rendered answer body — `OutputValidateNode`;
2. the outer `result` / `output` envelope field — `PostProcessNode`;
3. the structured `programs` list — `CorporateTrainingFAQAgent.get_output()`.

A screen that guards only the prose lets the structured field through, so there is one
implementation and three call sites. `programs` is attached only on a success status, and
is re-screened even then.

**Refusing is not containing.** The envelope resolves the released text as
`formatted_output or result`, without consulting the status. By the time the outer gate
runs, the main slot has already mapped the rendered answer into `result`, so an error
status that leaves `result` populated publishes the very answer the gate just refused —
the refusal becomes a label on a delivered answer rather than a withholding. Emptying
`formatted_output` does not help either: an empty string is falsy, so it re-opens the
fallback it appears to close.

`PostProcessNode` therefore returns an error **and** empties the response. Every state
field that can carry a representation of the answer — `result`, `training_faq_answer`,
`answer`, `eligibility_result`, `application_procedure`, `deadline_info`, `programs` — is
cleared in the same delta, and `formatted_output` is set to a fixed, non-empty withheld
notice that carries nothing from the refused answer. `hr_escalation` and `out_of_scope`
are advisory booleans with no answer content and are left alone.

The caller-visible error names the **location** that failed — the screened field — and
nothing else. That is load-bearing rather than tidy: the framework's own output scan reads
this delta on the way out, so a quoted match would make it raise, replace the node's return
with a bare error, and undo the clearing along with it. The blocked form's name goes to the
operator log and the `post_process_blocked` audit event instead, neither of which is a
caller surface.

**Blocked forms**: private-key block header, JWT, bearer token, vendor API-key prefix,
credential assignment, national-ID shape, e-mail address, long numeric ID run.

**Identifier guards.** Catalog and course identifiers (`TRN-LEAD-101`, `TRN-2026-0014-01`)
contain digit runs and hyphens, so every numeric pattern is wrapped in fixed-width
single-character guards — a match may not be immediately preceded or followed by
`[A-Za-z0-9-]`. Real identifiers stay byte-identical while standalone identifier-shaped
leaks are still caught. The test suite probes both directions: every blocked form is
caught, and the identifiers this domain renders every day pass through untouched.

**Amount rounding is deliberately not part of this invariant.** This template renders no
monetary aggregate at all: catalog records carry schedules, procedures and eligibility
rules, never figures, and the answer template has no numeric field. There is therefore no
numeric surface to round and no rounding grid to enforce. If a fork adds monetary content
to the catalog, that fork owns adding the corresponding numeric invariant.

## Answer assembly

Every rendered sentence about a program is quoted from a catalog record — name, category,
source, body excerpt, application procedure and deadline. Nothing is synthesised, and the
caller's own question is never echoed back into the answer, so no caller-controlled string
has a rendered surface at all.

The eligibility verdict is the one value the template computes, from the record's stated
rules and the bounded profile attributes only:

| Verdict | Meaning |
|---|---|
| `unknown` | no employee profile was supplied — nothing is claimed |
| `no` | the profile contradicts at least one stated rule |
| `conditional` | the record states a rule the profile does not answer |
| `yes` | every stated rule is satisfied |

A rule a record does not state is never invented: an empty department / role / grade list
means the program is open on that dimension.

When retrieval finds nothing above the relevance floor, the answer states that the catalog
holds no matching program and points the reader at the training team. That is a success
outcome, not an error, and it is screened by the same output rules as any other answer.

## Catalog record shape

`config/kb/training_catalog_kb.json` is a JSON array of records:

| Field | Type | Purpose |
|---|---|---|
| `id` | string | catalog identifier, rendered in the sources list |
| `program_name` | string | rendered program title |
| `category` | string | one of the fixed category slugs |
| `source` | string | provenance line rendered in the sources list |
| `tags` | list[string] | retrieval keywords |
| `content` | string | body text, excerpted into the answer |
| `eligibility.min_years_of_service` | number | tenure rule; 0 or absent means no rule |
| `eligibility.departments` / `.roles` / `.grades` | list[string] | allowed values; empty means open |
| `application_procedure` | string | quoted verbatim |
| `deadline` | string | quoted verbatim |

Retrieval is deterministic weighted keyword overlap (`src/services/catalog_service.py`):
program-name matches weigh most, then tags, then body text, normalised by the number of
distinct query tokens. A minimal singular-folding step keeps ordinary plural phrasings
("programs") matching singular catalog text ("program"). The contract is store-agnostic, so
replacing keyword scoring with a vector store changes that module only.

## State constraints (mandatory)

- Flat TypedDict only (primitives and JSON-serializable types).
- Structured fields travel as JSON **strings** (`to_json` / `from_json` in
  `src/schemas/state.py`) — a bare dict or list in a checkpointed field corrupts silently.
- No credentials or personal identifiers in State.
- Invocation context via the framework's context object, not State.
- No Pydantic models, dataclasses or arbitrary Python objects.

## Trust levels

The manifest declares `required_trust_level: "VERIFIED_EXTERNAL"`. Both outer backbone gate
slots (`PreProcessNode`, `PostProcessNode`) declare exactly that, so an anonymous caller
never reaches the domain workflow. The inner domain nodes run behind that gate and declare
`ANONYMOUS`; raising them would deny the very callers the manifest admits.

In a standalone deployment nothing upstream establishes a caller trust level, so
`src/api/server.py` is the entry-point auth boundary: with `INVOKE_AUTH_TOKEN` set, an
otherwise-anonymous caller must present it as a bearer token and then runs as
`VERIFIED_EXTERNAL`. Trust established by upstream middleware is never demoted.

## Import isolation

- The template imports `framework.*` and `shared.*` only.
- No platform-internal SDK import anywhere in `src/` — asserted by an AST scan in
  `tests/proof_of_boundary/test_import_isolation.py`.

## Design decision record

| Decision | Options | Chosen | Rationale |
|---|---|---|---|
| Base class | AgentBaseGraph / AutonomousBaseGraph | AgentBaseGraph | a fixed pipeline, not an autonomous loop |
| Composition | standalone / nested GraphNode / remote | nested GraphNode | a five-step domain workflow behind an unmodified backbone |
| Error propagation | propagate / handle | propagate | an inner failure must not surface as a partial answer |
| Retrieval | vector store / keyword scoring | keyword scoring | deterministic and network-free for the shipped build; the contract is store-agnostic |
| Answer synthesis | generated / template-assembled | template-assembled | quoting the catalog is what makes "no invention" enforceable |
| Config delivery | node constructor args / state seeding | state seeding | `execute()` receives only state, so a constructor value would be dead configuration |
| Output invariant | numeric rounding grid / identifier-and-credential screen | screen | no monetary aggregate is rendered, so a rounding grid would guard nothing |
