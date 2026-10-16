# Reference

Lookup material for Ratchet v0.1. The pages marked *generated* come from the code or from
the original documents in the repository, so they match the code they describe.

| Page | What it holds | Source |
| --- | --- | --- |
| [CLI](../cli.md) | every `ratchet` command and option | generated from the Typer app |
| [Configuration](../configuration.md) | every setting and its environment variable, budget caps, public mode, the runner/sandbox/harness matrix | settings tables generated from `ratchet/config/settings.py` |
| [Dashboard API](../api.md) | `/api/v1` endpoints, SSE, the error envelope and every error code | endpoints generated from `schemas/openapi.json` |
| [API models](api-models.md) | every request and response model, field by field | generated from `schemas/openapi.json` |
| [Decision records](adr/index.md) | the ADRs: what was decided, why, and what would reverse it | generated copies of `docs/adr/` |
| [Verified against live APIs](verified.md) | every assumption about an external API, with its evidence (researched, live, open or deferred) | generated copy of `docs/VERIFIED.md` |
| [Implementation contracts](contracts.md) | module boundaries, ports, interfaces and the error-code registry | generated copy of `docs/CONTRACTS.md` |
| [Feedback memo](feedback.md) | what worked and what didn't on Token Factory, Serverless Jobs, NemoClaw/OpenShell, Hermes and Tavily | generated copy of `FEEDBACK.md` |

Behavior is specified by the [decision records](adr/index.md), the design notes in
`docs/design/` and the tests. `docs/CONTRACTS.md` is the source of truth for module
boundaries and Python names.
