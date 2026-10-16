# VERIFIED — what each platform fact rests on

Each row is a fact Ratchet depends on about Token Factory, Serverless Jobs, Hermes Agent,
OpenShell or Tavily, the answer with its evidence, and its state.
**Researched** means confirmed from a primary source (official docs, OpenAPI spec, catalog
JSON, or source code); the sources are in `docs/research/`. **Live** means confirmed by a
call with a real key (`ratchet verify-live` appends results below). **Open** means it still
needs a key or a machine we don't have; **Deferred** means it needs a machine or install that
has not been available so far (a Nebius Linux VM or Jobs quota for the OpenShell sandbox and
Serverless Jobs, a Hermes install for the Hermes adapter) and is tracked in ADR
0010/0011/0012 and by markers in the code. Rows are never deleted; answers are
appended with a date.

## Token Factory

| Item | Answer (2026-10-15) | State |
| --- | --- | --- |
| Base URL | `https://api.tokenfactory.nebius.com/v1/` is the global host. Region hosts like `api.tokenfactory.us-central1.nebius.com` exist, but the docs warn they "may stop working". Default is the global host (`NEBIUS_TF_BASE_URL`). | Researched |
| Auth | `Authorization: Bearer <key>` | Researched |
| Model ids | nano `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` (262K) · super `nvidia/nemotron-3-super-120b-a12b` (256K) · ultra `nvidia/Nemotron-3-Ultra-550b-a55b` (1M), all from the public catalog `models_info`. Fallback `nvidia/Nemotron-3_5-Lightning`. Set in `ratchet/config/models.py`. | Researched; **Live ✓** (2026-10-15, `GET /v1/models`) |
| Prices | 0.06/0.24 · 0.30/0.90 · 1.00/3.00 USD per 1M tokens, matching the prices in `ratchet/config/pricing.py` exactly. No cached-token price. | Researched |
| Embeddings | `Qwen/Qwen3-Embedding-8B` is the only embedding model, $0.01/1M. The default selection embedder stays local (`RATCHET_SELECTION_EMBEDDER=local`). | Researched |
| `response_format` json_schema | Present in the OpenAPI spec. NVIDIA notes Nano may lack structured output. `call_structured` degrades json_schema → json_object → prompt-only per tier (`RATCHET_STRUCTURED_MODE=auto`). 2026-10-15: strict json_schema returns valid JSON on all three tiers (Live results). | **Live ✓** |
| `seed` | Not in the OpenAPI schema; extra fields are accepted. Sent anyway; repeatability is measured by the E6 noise-floor run. 2026-10-15: not honored (3 different outputs with `seed=7`); Ratchet relies on temperature 0 plus the semantic cascade. | **Live ✗ (not honored)** |
| Thinking toggle | Not documented on Token Factory. NVIDIA's convention is `chat_template_kwargs.enable_thinking`; Token Factory documents `reasoning_effort`. Switchable with `RATCHET_THINKING_STYLE`. Reasoning arrives in `message.reasoning_content` / `reasoning`. 2026-10-15: `chat_template_kwargs.enable_thinking=false` turns reasoning off on every tier; it is the default style. | **Live ✓** |
| `stream_options.include_usage` | In the OpenAPI spec. The proxy falls back to estimated usage (`usage_estimated=true`). 2026-10-15: the final chunk carries `usage`. | **Live ✓** |
| Rate limits | Dynamic; 429 + `Retry-After` + `x-ratelimit-*` headers; no published base RPM. | Researched |
| Fine-tuning | Nemotron is not fine-tunable on Token Factory (feedback memo item). | Researched |

## Serverless Jobs and the control-plane VM

| Item | Answer | State |
| --- | --- | --- |
| Product | Nebius "Serverless AI" → Jobs. REST `POST https://api.nebius.cloud/ai/v1/jobs` (`metadata.parentId`, `spec.image`); gRPC `nebius.ai.v1.JobService`; CLI `nebius ai job create`. | Researched |
| File injection | `--inject-file` is limited to ≤ 64 KiB, so batch inputs and outputs go through an Object Storage bucket volume (`--volume <bucket>:/mnt:rw`). | Researched; field names not yet verified live, **Deferred (needs Nebius VM / Jobs quota; ADR 0011)** |
| States | PROVISIONING, STARTING, IMAGE_PULLING, RUNNING, COMPLETED, FAILED, CANCELLED, ERROR | Researched |
| Logs | No REST logs endpoint, so the worker writes `worker.log` to the output bucket. | Researched |
| Auth | IAM token (`nebius iam get-access-token`, 12 h) or a service-account JWT exchange; the project id is `parentId`. | Researched |
| Cold start | "a few minutes" per docs. Live measurement is open; demo path uses the local runner if > 60 s (ADR 0011). | **Deferred (needs Nebius VM / Jobs quota; ADR 0011)** |
| Serverless Endpoints autoscaling | No autoscaling or scale-to-zero fields in the spec, so the control plane runs on a VM (ADR 0011). | Researched |
| Object Storage | `https://storage.<region>.nebius.cloud`, S3 SigV4 with a service-account static key | Researched |

## Hermes Agent

| Item | Answer | State |
| --- | --- | --- |
| Version / install | v0.22.2 (2026-10-15), MIT. Installs via `install.sh` (`--non-interactive --skip-browser --skip-computer-use`); the PyPI wheel is stale (0.19.0). | Researched |
| Non-interactive run | `hermes chat --oneshot -Q -q "<task>" --format stream-json` emits JSONL events (system/text/tool_use/tool_result/result) | Researched; **Deferred (needs Hermes install; ADR 0012)** |
| Model endpoint | `model: {provider: custom, base_url}` in `$HERMES_HOME/config.yaml`; no temperature/seed keys, so the proxy fills them in for agent calls that carry none: `seed` from the run's `run.json` and `temperature: 0` (chat and responses requests; 2026-10-16) | Researched |
| Skills dir | `$HERMES_HOME/skills/<name>/SKILL.md` (agentskills.io); the index is rebuilt per session, so a fresh one-shot process acts as the reload | Researched |
| Tools → mock server | `mcp_servers.<name>.url` (streamable HTTP); real toolsets disabled with `agent.disabled_toolsets`. 2026-10-16: the adapter enables an explicit allowlist of toolsets (the mock MCP server plus the safe built-ins) instead of disabling a denylist | Researched; **Deferred (needs Hermes install; ADR 0012)** |
| Run-id header | None by default; `session_affinity_header` is optional. The adapter uses path-form run ids `/v1/r/<run_id>/…`. | Researched |
| Clock | `datetime.now()`, no freeze hook; `libfaketime` in the sandbox | Researched |
| `author=agent` signal | Writes through `skill_manage` land in the skills dir. Classification: a write into the main checkout counts as `agent`. | Researched; **Deferred (needs Hermes install; ADR 0012)** |
| Non-interactive run (live install, v0.22.2) | `hermes chat --oneshot -Q -q <task> --format stream-json --max-turns N --toolsets ratchet_mock,file,skills --run-budget <s>`; stdin closed, no setup prompt; `--usage-file` is top-level and ignored by one-shot chat, so it is gone. Event fields as in `hermes_stream.py`; no event carries the version (`harness_version` from `RATCHET_HERMES_VERSION` or one `hermes --version` per process). `docs/research/hermes-live-s3.md` | **Live ✓ (2026-10-16)** |
| Model endpoint (live install) | Named provider: `model.provider: ratchet`, `providers.ratchet.{base_url: <proxy>/r/<run_id>, api_key, extra_headers}` (bare `custom` drops key and headers); `/chat/completions` is appended to the path-form base URL; no `temperature`/`seed`/`max_tokens` sent, the proxy injects seed and temperature | **Live ✓ (2026-10-16)** |
| Tools → mock server (live install) | MCP toolset = server key `ratchet_mock`; tools are `mcp__ratchet_mock__<tool>`; `tools.tool_search.enabled: off` exposes them directly; allowlist via `--toolsets` + `platform_toolsets.cli`, denylist `agent.disabled_toolsets`; run-id header reaches `/mcp` (which now answers `GET` with 405) | **Live ✓ (2026-10-16)** |
| Skills / reload (live install) | Index from `$HERMES_HOME/skills` (descriptions cut to 60 chars), body via `skill_view`; `HERMES_BUNDLED_SKILLS=<empty dir>` stops the 80+ bundled skills; a fresh one-shot reflects an edited SKILL.md (`tests/e2e/test_hermes_acceptance.py`, `-m hermes`) | **Live ✓ (2026-10-16)** |
| Prompt stability (live install) | `_config_version: 50`, `model.context_length`, `agent.coding_context: off` (no git Workspace block), `auxiliary.title_generation.enabled: false` (no extra LLMCall), fixed `<home_root>/current` home per run: same first-request hash across runs | **Live ✓ (2026-10-16)** |
| Clock / skill-creation nudges | libfaketime needs the Linux sandbox (macOS: real date in `Conversation started:`); `skills.creation_nudge_interval: 0` untested on long runs | **Deferred** |

## OpenShell / NemoClaw

| Item | Answer | State |
| --- | --- | --- |
| OpenShell requirements | Linux ≥ 6.2 with Landlock ABI 3, Docker ≥ 28 or Podman 5; runs a gateway service. Running inside an unprivileged container is undocumented and likely not viable. | Researched |
| Decision | Docker sandbox in Jobs; OpenShell on the VM for the demo agent, documented as the production path, with Docker as the fallback (ADR 0010) | Decided; live OpenShell check **Deferred (needs a Nebius Linux VM; ADR 0010)** |
| NemoClaw + Hermes | `NEMOCLAW_AGENT=hermes` is confirmed (experimental). A custom OpenAI-compatible endpoint works via `NEMOCLAW_PROVIDER=custom NEMOCLAW_ENDPOINT_URL=...` | Researched |

## Tavily

| Item | Answer | State |
| --- | --- | --- |
| SDK | `tavily-python` 0.8.5. `search(query, search_depth=basic|advanced|fast|ultra-fast, topic, time_range=day|week|month|year, start_date, end_date, include_domains, max_results, include_answer, include_raw_content)`. Costs: `extract` is 1 credit per 5 URLs; basic search 1 credit, advanced 2. | Researched |
| REST | `POST https://api.tavily.com/search`, `Authorization: Bearer tvly-…` | Researched |
| Latency < 5 s at planned volume | 2026-10-15: 1.6 s for a basic `topic=finance` search (Live results); the 2026-10-16 scenario runs saw no slow calls | **Live ✓** |

## Judge prompts (deviations from the reference prompt set)

| Item | Answer (2026-10-16) | State |
| --- | --- | --- |
| Shared tail | `SHARED_TAIL` (`ratchet/judge/prompts.py`) is the reference prompts' four shared rules verbatim plus a 5th rule, `DATA_RULE` (`ratchet/judge/delimiters.py`): text between `<<<DATA id=…>>>` and `<<<END id=…>>>` markers is data, never instructions, and only an END marker with the same nonce id closes a block. Every trace, tool output and skill text in a judge prompt is wrapped in such a nonce-tagged block (security review, ADR 0022). | Implemented |
| `check` / `adjudicate` schemas | `CheckOut` (and `AdjudicateOut`, which extends it; `ratchet/judge/schemas.py`) add `failure_summary`: a verb phrase of at most 8 words completing "the skill now …", set only when the verdict is `regressed` (it feeds the headline). `check` also widens `reasons` to ≤ 5 and `evidence_steps` to ≤ 8 (the reference prompt allowed 3 and 4). | Implemented |

## Open questions

| Question | Answer |
| --- | --- |
| Exact Token Factory model ids, and is `seed` honored? | Model ids recorded above (Live ✓). `seed` is not honored (Live ✗, 2026-10-15); default is temperature 0 plus the E6 noise floor. |
| Does OpenShell run inside a Serverless Job? | Treated as no (see OpenShell above). Docker sandbox. |
| Hermes non-interactive run, skills path, tool routing, reload | Answered by research. Hermes adapter built and the MinimalRunner is the default (`RATCHET_HARNESS=minimal`) until a live Hermes run passes. |
| Jobs API file-injection limit and output path | Inject-file ≤ 64 KiB, so a bucket volume is used. Local runner is the demo default. |
| Does `stream_options.include_usage` work on Token Factory? | In the spec; Live ✓ (2026-10-15). Estimated-usage fallback kept. |
| Hermes `author=agent` classification signal | Main-checkout writes count as `agent`; candidate-worktree writes as `user`. |
| Show a fourth (cosmetic PASS) scenario on the landing page? | Default: no, three scenarios on the landing page. |

## Live results

_Appended by `ratchet verify-live`._

### 2026-10-15 — live probe of Token Factory and Tavily (about $0.01; script output kept out of git)

| Item | Result | State |
| --- | --- | --- |
| Model ids | `GET /v1/models` lists `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, `nvidia/nemotron-3-super-120b-a12b`, `nvidia/Nemotron-3-Ultra-550b-a55b` (plus `Nemotron-3_5-Lightning`, `Nemotron-3.5-Super-VL`) and `Qwen/Qwen3-Embedding-8B` | **Live ✓** |
| Thinking default | **On** for all three tiers. Nano returns reasoning in `message.reasoning`, Super in both `reasoning` and `reasoning_content`, Ultra in `reasoning_content`; `usage.completion_tokens_details.reasoning_tokens` is populated on Super | **Live ✓** |
| Thinking toggle | `chat_template_kwargs: {"enable_thinking": false}` turns reasoning off on Nano, Super and Ultra (reasoning tokens 0). `reasoning_effort: "none"` on Nano returns **empty content**, so the default `RATCHET_THINKING_STYLE=chat_template_kwargs` is correct. Agent calls now default to thinking off through the proxy (`RATCHET_AGENT_THINKING=off`) | **Live ✓** |
| `response_format: json_schema` (strict) | Valid JSON matching the schema on Nano, Super and Ultra (thinking off). The prompt-only fallback stays in place as defence in depth | **Live ✓** |
| `seed` | **Not honored**: 3 identical calls with `seed=7`, temperature 0.8 gave 3 different outputs. Ratchet relies on temperature 0 plus the semantic cascade, with nondeterminism measured by the E6 noise-floor run | **Live ✓ (negative)** |
| `stream_options.include_usage` | Final chunk carries `usage` | **Live ✓** |
| Prompt caching | Super's `usage` carries `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` fields (hits 0 on first calls). Feedback memo item | **Live ✓** |
| Latency | 0.3–0.9 s for short calls on every tier | **Live ✓** |
| Embeddings | `Qwen/Qwen3-Embedding-8B` returns 4096-dim vectors | **Live ✓** |
| Tavily | `POST /search` with `topic=finance`, `time_range=week`, basic depth: 200 in 1.6 s, 3 results. Finance results are quote pages whose `content` is mostly navigation text, so drift checks should use `include_answer` and/or `extract` on the top result (the drift check's first step) | **Live ✓** |

### 2026-10-16 — live reliability runs (demo scenarios, response cache off)

| Item | Result | State |
| --- | --- | --- |
| Scenarios 1–3 on real Nemotron + live Tavily | BLOCK / BLOCK / PASS-with-drift. Measured with `RATCHET_CACHE_TTL_DAYS=0`, because warm-cache runs reused judge answers and overstated reliability. Scenario 3 went from 0/4 to 4/4 after the drift fact check was given the replay-observed value (`DriftCheck.replay_value`) and grounded comparisons. | **Live ✓** |
| Ultra latency | 12–41 s for 4 contested pairs at 2-way concurrency. Adjudicate concurrency raised to 4; no 429s observed in any live run | **Live ✓** |
| Nano determinism | Nano flipped its `compare_fact` answer on identical evidence at temperature 0, so an unsure or ungrounded comparison escalates to Super | **Live ✓** |

### 2026-10-16 — Docker sandbox validated under Linux semantics

| Item | Result | State |
| --- | --- | --- |
| Sandbox egress | From a locked-down container on `ratchet-sandbox`: the gateway sidecar answers; `https://example.com`, raw public IPs and DNS (getaddrinfo and direct UDP) all fail; host loopback listeners are unreachable. Run inside the `control-docker` image with `--network host` (the OrbStack VM's network namespace, i.e. how the VM runs it). On a macOS host the test is skipped: the bridge gateway is not a host interface there (`docs/research/docker-sandbox-s1.md`). | **Live ✓ (Linux semantics)** |
| Inter-container isolation (`enable_icc=false`) | Not enforced on OrbStack: same-bridge traffic bypasses iptables without `br_netfilter`. `deploy/sandbox-network.sh` now loads `br_netfilter` and sets `bridge-nf-call-iptables=1` on Linux; re-check on the Nebius VM. | Deferred (VM) |
| Host listeners from the sandbox | `--internal` does not filter traffic to the host; `sandbox-network.sh` drops ports 22, 80, 443, 8080–8082 on the bridge. Ratchet services bind 127.0.0.1. | **Fixed; verify on the VM** |
| Sidecar bind ordering | The worker bound sidecars before ensuring the network; `DockerSandbox.preflight()` now runs first and fails fast with a platform-specific message. | **Fixed** |

### 2026-10-16 — live scenarios after the E6 judging fixes and control replays (ADR 0023)

| Scenario | Result (`ratchet simulate k --repeat 2`, cache off) | Note |
| --- | --- | --- |
| 1 github-triage | BLOCK 2/2 (41 s, 55 s idle; 81–112 s while the bench sweep shared the API) | 7 of 14 replays closed issues both times |
| 2 expense-report | BLOCK 2/2 (53 s, 48 s idle) | control replays agreed with the goldens, so the regressions stood (2 of 10, then 1 of 10) |
| 3 fx-expense-converter | PASS with drift 2/2 (67 s, 72 s idle) | 4 of 7 tasks drifted; over the 60 s target because each drifted task costs a live Tavily check and an Ultra adjudication |

### 2026-10-16 — README quick start from a fresh clone (offline simulator)

`git clone` → `uv sync` → `cp .env.example .env` → `npm ci && npm run build` → `ratchet doctor` → `ratchet seed-demo` (25 goldens) → `simulate 1/2/3` gave BLOCK / BLOCK / PASS-with-drift, and `simulate 1 --repeat 3` matched 3/3 (about 5 s each). A run by someone who did not write the quick start is still to do. | **Verified (fresh clone)**

### 2026-10-16 — what the first live SkillRegress-Bench sweeps changed (bench/results/2026-10-16)

| Finding | Evidence | Status |
| --- | --- | --- |
| Bench Tavily fixtures never answered | The drift judge searches `current_query(hint)` ("… today"); the bench index held only the dated hint, so 0 `drift` outcomes in 35 verdicts and 7 `drift_unavailable`. Then "today" mapped to the oldest recording and no D1 drift was found. | **Fixed** (index both keys; latest recording wins) |
| Nano cleared real regressions alone | All 10 missed R3 pairs carried a Level 0 "changed args" finding; 0 of 92 cosmetic pairs did. 90 % of true-pass pairs had a final-output similarity of 1.0; the regressions Nano cleared had a median of 0.92. | **Fixed** (ADR 0024: structural finding or similarity < 0.90 forbids the Nano-only pass) |
| Noise-tolerant PASS considered | Simulated on the sweep's cascade run: 2 cosmetic WARNs would become PASS, and so would 5 real regressions (R1, R2, R5 ×2, R6). | **Rejected**; uncertain still holds the edit |
| Control replays | 0.16 of pairs diverged on the control replay (122 unchanged-skill pairs, pair pass rate 0.98). A diverging control settles the pair; an agreeing one is weak evidence at this base rate. | **Live ✓** |
| Replay retry | 0 upstream retries and 0 crashed replays across 6,655 model calls in one sweep (`proxy.upstream.failed` warnings kept in the sweep log). The one-shot retry round (ADR 0023 note) stays on. | **Live ✓ (0 observed)** |
| Pair layers after the stage reorder | With controls after drift, the controls stage still read the judged layer: it replayed controls for pairs drift had settled and a control written after drift was lost (`load_pairs` preferred the drift layer). Seen on the D1 bench edits. | **Fixed** (`PAIR_LAYERS`: control > drift > judged; controls read the drift layer) |
| Controls for `drift_unavailable` pairs | Tried: a diverging control would pass an edit while Tavily is down, which must never happen (with Tavily down the edit is never passed) and which mislabels world drift as nondeterminism. | **Reverted the same day** |
