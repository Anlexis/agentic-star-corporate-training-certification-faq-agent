# Corporate Training & Certification FAQ Agent

AI agent for answering corporate training and certification questions, built with Agentic Star.

> **Category**: Cat 2 (domain-specific multi-step workflow — retrieval and answer assembly)
> **Industry**: Education
> **Template ID**: EDU-C2-014

## Overview

Answers employee questions about internal training programs, certifications and
reskilling pathways, grounded in a training catalog. Employees ask in natural language —
"which leadership programs can a team lead with three years of service apply for?" — and
get back the matching catalog programs, an eligibility verdict for their own situation,
the application procedure and the deadline, each quoted from the catalog record rather
than generated. Callers may pass structured parameters (`category`, `top_k`,
`min_score`, `language`, `employee_profile`) alongside the question; every one of them is
validated fail-closed before it reaches the pipeline.

The shipped build is deterministic and network-free over a seeded JSON catalog: keyword
retrieval, rule-based eligibility evaluation and template-assembled answers, with a
documented seam for upgrading to a live catalog service or generated synthesis. Answers
render in Japanese, Vietnamese or English. When the catalog holds nothing relevant, the
agent says so instead of inventing a program.

The employee attributes it accepts are organisational only — department, role, grade and
years of service. No personnel record is read, stored or rendered, and a direct
identifier pasted into a question is redacted before it reaches any node.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at
graph compile / start-up preflight rather than starting in a partially working state. This
is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Calling the agent

`POST /invoke` takes the question as `input` and the optional structured parameters as
`input_context`:

```json
{
  "input": "which leadership programs can a team lead with three years of service apply for?",
  "input_context": {
    "category": "leadership",
    "top_k": 3,
    "min_score": 0.25,
    "language": "en",
    "employee_profile": {
      "department": "engineering",
      "role": "manager",
      "grade": "g4",
      "years_of_service": 4
    }
  }
}
```

Every field is optional; every supplied field is validated against explicit bounds and the
request is rejected — not silently corrected — if one is out of contract. A successful
response carries the rendered answer as `output` and the programs it rests on as
`programs`.

## Project Structure

```
src/          agent implementation (nodes, graphs, services, schemas)
tests/        unit and boundary tests
config/       agent manifest, runtime parameters, and the seeded catalog
docs/         design and test documentation
```

- `config/agent.yaml` — the static manifest (identity, entry point, trust level).
- `config/config.yaml` — runtime parameters and the retrieval / answer tuning blocks.
- `config/kb/training_catalog_kb.json` — the seeded training catalog.

See `docs/02_design.md` for the architecture and `docs/03_test_spec.md` for the test
specification.

## Customising

1. Replace `config/kb/training_catalog_kb.json` with your own catalog, keeping the record
   shape documented in `docs/02_design.md`.
2. Adjust the fixed category slugs in `src/nodes/input_validate.py` to match your catalog.
3. Tune retrieval and answer rendering in `config/config.yaml`.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
