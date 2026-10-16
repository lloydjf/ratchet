# Research: Nebius Token Factory, Nebius Serverless AI, Tavily, NVIDIA NIM (Nemotron 3)

Researched 2026-10-15 for Ratchet (Nebius x NVIDIA Global AI Hackathon). Every fact is tagged
CONFIRMED (seen in a primary source: official docs, OpenAPI spec, official catalog JSON, official
GitHub repo, PyPI JSON, Devpost rules) or UNCONFIRMED (inferred, or from a secondary source).
Nothing here was tested with a live API key; see the final section.

---

## Recommended config values (copy into config)

```ini
# --- Nebius Token Factory (OpenAI-compatible) ---
TF_BASE_URL              = https://api.tokenfactory.nebius.com/v1/          # CONFIRMED (docs quickstart)
TF_BASE_URL_US_CENTRAL1  = https://api.tokenfactory.us-central1.nebius.com/v1/  # CONFIRMED exists (cookbook + models page); public endpoints are "Global", region hosts "may stop working"
TF_API_KEY_ENV           = NEBIUS_API_KEY                                    # CONFIRMED
TF_AUTH_HEADER           = Authorization: Bearer $NEBIUS_API_KEY             # CONFIRMED

TF_MODEL_NANO     = nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B   # CONFIRMED catalog; 262K ctx; fp8; eu-north1; $0.06 / $0.24
TF_MODEL_SUPER    = nvidia/nemotron-3-super-120b-a12b       # CONFIRMED catalog; 256K ctx; fp4; us-central1; $0.30 / $0.90
TF_MODEL_ULTRA    = nvidia/Nemotron-3-Ultra-550b-a55b       # CONFIRMED catalog; 1M ctx;  fp4; us-central1; $1.00 / $3.00
TF_MODEL_FALLBACK = nvidia/Nemotron-3_5-Lightning           # CONFIRMED catalog; 30B-A3B, 1M ctx; bf16; eu-north1; $0.06 / $0.24
TF_MODEL_EMBED    = Qwen/Qwen3-Embedding-8B                 # CONFIRMED catalog; only embedding model; 40,960 max len; $0.01 / 1M input

# Prices USD per 1M tokens (input / output) -- CONFIRMED in official catalog JSON, 2026-10-15
#   nano 0.06/0.24   super 0.30/0.90   ultra 1.00/3.00   lightning 0.06/0.24   embed 0.01/0
#   (spec assumptions were all correct; cached-input price field exists but is null for every model)

# Parameter names (chat completions)
TF_REASONING_PARAM      = reasoning_effort      # CONFIRMED in OpenAPI: none|minimal|low|medium|high|xhigh|max (effect on Nemotron UNCONFIRMED)
TF_REASONING_FIELD      = message.reasoning_content  (also `reasoning`)   # CONFIRMED in OpenAPI schema, non-stream + stream delta
TF_THINK_TOGGLE_GUESS   = extra_body={"chat_template_kwargs":{"enable_thinking": false}}  # UNCONFIRMED on TF (NVIDIA convention; schema allows extra fields)
TF_JSON_SCHEMA          = response_format={"type":"json_schema","json_schema":{"name":..,"schema":..,"strict":true}}  # CONFIRMED in OpenAPI + docs
TF_GUIDED_JSON          = extra_body={"guided_json": <schema>} + response_format={"type":"json_object"}  # CONFIRMED in official cookbook notebook
TF_STREAM_USAGE         = stream_options={"include_usage": true}   # CONFIRMED
TF_SEED                 = seed   # UNCONFIRMED: NOT in chat schema (only fine-tuning); request allows extra props

# --- Nebius AI Cloud (Serverless AI jobs) ---
NEBIUS_API_HOST     = https://api.nebius.cloud               # CONFIRMED
NEBIUS_JOBS_REST    = POST https://api.nebius.cloud/ai/v1/jobs ; GET /ai/v1/jobs/{id}  # CONFIRMED
NEBIUS_TOKEN_ENV    = NEBIUS_IAM_TOKEN   (pysdk default; `nebius iam get-access-token`, 12h)  # CONFIRMED
NEBIUS_PARENT_ID    = <project id>  (metadata.parentId / --parent-id / ~/.nebius/config.yaml)  # CONFIRMED
NEBIUS_CPU_PLATFORM = cpu-d3  preset 2vcpu-8gb   (cpu-e2 only in eu-north1)   # CONFIRMED
NEBIUS_S3_ENDPOINT  = https://storage.<region_ID>.nebius.cloud   e.g. https://storage.eu-north1.nebius.cloud  # CONFIRMED

# --- Tavily ---
TAVILY_PY_VERSION = 0.8.5 (2026-10-06)                        # CONFIRMED PyPI
TAVILY_REST       = POST https://api.tavily.com/search ; /extract ; Authorization: Bearer tvly-...  # CONFIRMED
TAVILY_API_KEY_ENV = TAVILY_API_KEY  (SDK reads it when api_key omitted)   # UNCONFIRMED exact env name (not in fetched ref page)

# --- NVIDIA build.nvidia.com (reference only) ---
NIM_BASE_URL = https://integrate.api.nvidia.com/v1 ; model e.g. nvidia/nemotron-3-super-120b-a12b  # CONFIRMED
```

---

## A. Nebius Token Factory inference API

### A1. Base URL, regions, auth
- Base URL `https://api.tokenfactory.nebius.com/v1/` (trailing slash in docs). Auth `Authorization: Bearer <key>`; env `NEBIUS_API_KEY`. CONFIRMED
  https://docs.tokenfactory.nebius.com/api-reference/introduction.md , https://docs.tokenfactory.nebius.com/quickstart.md
- Regional hosts listed on the official models page HTML: `api.tokenfactory.{us-central1,us-north1,eu-west1,eu-west2,me-west1,uk-south1,tf-us1,tf-us2,tf-ca1}.nebius.com/v1/`. All of these (plus `eu-north1`, and legacy `api.studio.nebius.com`, `api.studio.nebius.ai`) answered HTTP 401 "token is not present" to `GET /v1/models`, i.e. the hosts exist. CONFIRMED (curl, 2026-10-15; https://tokenfactory.nebius.com/models)
- Official cookbook Nemotron guides use `base_url="https://api.tokenfactory.us-central1.nebius.com/v1/"` with Super/Ultra (both catalogued in us-central1). CONFIRMED
  https://github.com/nebius/token-factory-cookbook/blob/main/models/nemotron/nemotron3-ultra-550b-a55b.md
- Caveat: "Public Serverless Endpoints" doc says public endpoints are Global, processing location can change, and "a region-specific base_url may stop working"; dedicated endpoints are the way to pin region. CONFIRMED https://docs.tokenfactory.nebius.com/public-serverless.md
- Recommendation: default to the global host; keep the us-central1 host as an optional override.
- Legacy "AI Studio" host `api.studio.nebius.com` still resolves and demands a token (likely still routed). UNCONFIRMED that it serves the same catalog.

### A2. Model ids (official public catalog, 27 entries, fetched 2026-10-15)
Source (authoritative, machine-readable): https://tokenfactory.nebius.com/api/public/models_info ; readable: https://tokenfactory.nebius.com/model-catalog.md ; index: https://tokenfactory.nebius.com/llms.txt . All CONFIRMED.

| model_id | type | ctx (max_model_len) | region | quant | tps | in / out $/1M | use_cases flags |
|---|---|---|---|---|---|---|---|
| `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | text2text | 262,144 | eu-north1 | fp8 | 60 | 0.06 / 0.24 | text, reasoning, function_calling, code, context_and_rag, responses_api |
| `nvidia/nemotron-3-super-120b-a12b` | text2text | 262,144 | us-central1 | fp4 | 127 | 0.30 / 0.90 | + dedicated-endpoint, summarization, complex_writing |
| `nvidia/Nemotron-3-Ultra-550b-a55b` | text2text | 1,048,576 | us-central1 | fp4 | 523 | 1.00 / 3.00 | same as Super |
| `nvidia/Nemotron-3_5-Lightning` | text2text | 1,048,576 | eu-north1 | bf16 | 314 | 0.06 / 0.24 | reasoning, function_calling, code, responses_api ("MTP") |
| `nvidia/Nemotron-3.5-Super-VL` | image2text | 1,048,576 | us-north1 | bf16 | 282 | 0.30 / 0.90 | image/video + function_calling, reasoning |
| `Qwen/Qwen3-Embedding-8B` | embedding | 40,960 | eu-north1 | - | - | 0.01 / 0 | retrieval, clustering, classification, reranking |

- Model ids are case-sensitive-looking and inconsistent across sources: the catalog says `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`; one cookbook page uses `nvidia/nvidia-nemotron-3-nano-30b-a3b` (https://github.com/nebius/token-factory-cookbook/blob/main/models/nemotron/nemotron3-nano-30b.md). Use the catalog spelling; test whether the API is case-insensitive. UNCONFIRMED
- Not in the current public catalog (so not usable as fallbacks today): `Llama-3.3-Nemotron-Super-49B`, `Nemotron Nano 9B v2`, `Nemotron-Nano-v2-12B`, `Nemotron-3-Nano-Omni`, `Llama-3.1-Nemotron-Ultra-253B`. The cookbook README lists Nano-Omni and Llama-3.1-Nemotron-Ultra under an HTML comment "Models not currently featured". Third-party aggregators still list some of them (stale). CONFIRMED absent from catalog JSON
- No other embedding model is public: `BAAI/bge-en-icl` appears only as the example in the embeddings API reference, not in the catalog. CONFIRMED https://docs.tokenfactory.nebius.com/api-reference/inference/create-embeddings.md
- Embeddings endpoint `POST /v1/embeddings` params: `model`, `input` (str | list[str] | tokens), `encoding_format` float|base64, `dimensions`, `user`, `service_tier`. Also `POST /v1/rerank` exists. CONFIRMED (OpenAPI https://api.tokenfactory.nebius.com/openapi.json)

### A3. Prices
- Catalog JSON prices (above) match the spec's assumptions exactly: nano 0.06/0.24, super 0.30/0.90, ultra 1.00/3.00. CONFIRMED
- Field `flavors[].input_cached_price_per_million_tokens` exists in the catalog schema but is `null` for every model today. CONFIRMED
- Trial credit on sign-up: "$1 in trial credit, valid for 30 days"; prepaid balance otherwise. CONFIRMED https://docs.tokenfactory.nebius.com/other-capabilities/billing-new.md

### A4. Feature support (from OpenAPI spec + docs)
OpenAPI: https://api.tokenfactory.nebius.com/openapi.json (276 KB; paths include /v1/chat/completions, /v1/completions, /v1/embeddings, /v1/rerank, /v1/responses, /v1/models, /v1/files, /v1/fine_tuning/jobs, /v1/datasets, /v1/operations; NO /v1/batches path).

- `ChatCompletionRequest` properties: extra_body, frequency_penalty, logit_bias, logprobs, max_completion_tokens, max_tokens (default 8192 if omitted), messages, model, n (1..128), presence_penalty, reasoning_effort, response_format, service_tier, stop, store, stream, stream_options, temperature, tool_choice, tools, top_logprobs, top_p, user. `additionalProperties: true`. CONFIRMED
- `response_format.type` enum: `text | json_object | json_schema`; `json_schema` object = `{name (req), schema (req), description, strict}`. The field's prose description still says "Only json_object or text is supported" (stale text). The Structured-output doc page shows a working `json_schema` example (Qwen3-235B) and says to look for the "JSON mode" tag per model; recommends also putting the schema in the prompt and handling `refusal`/invalid JSON. CONFIRMED https://docs.tokenfactory.nebius.com/ai-models-inference/json.md . Whether Nemotron 3 models honour `json_schema`: UNCONFIRMED (catalog has no "json" flag; NVIDIA's own Nano page says structured output "Not supported", Super page says supported).
- `guided_json`: not in the schema, but the official cookbook `guided_json.ipynb` sends `extra_body={"guided_json": schema}` together with `response_format={"type":"json_object"}`. CONFIRMED https://github.com/nebius/token-factory-cookbook/blob/main/guided_json.ipynb . `guided_regex/guided_choice/guided_grammar`: not mentioned anywhere. UNCONFIRMED
- `seed`: absent from ChatCompletionRequest (only in fine-tuning schemas). Request accepts unknown fields, so it may be passed through to the engine, but reproducibility is UNCONFIRMED.
- `stream_options: {"include_usage": true}` -> usage on last chunk. CONFIRMED
- `logprobs` (bool) + `top_logprobs` (int). CONFIRMED in schema; per-model support UNCONFIRMED.
- `tools` / `tool_choice` (`none|auto|required|{type:function,function:{name}}`). CONFIRMED https://docs.tokenfactory.nebius.com/ai-models-inference/function-calling.md . All Nemotron catalog rows carry `function_calling`. `parallel_tool_calls` is NOT in the chat schema (it is in the Responses schema). CONFIRMED
- Responses API: `POST /v1/responses` with `input`, `model`, `instructions`, `text.format` (text | json_schema | json_object), `reasoning.effort` (none..xhigh) + `summary`, `tools`, `tool_choice`, `store`, `previous_response_id`, `stream`, `parallel_tool_calls`, `prompt_cache_key`, `max_output_tokens`. All Nemotron rows carry `responses_api`. CONFIRMED https://docs.tokenfactory.nebius.com/api-reference/inference/create-a-response.md (the `reasoning` field's prose says "gpt-5 and o-series only", copied from OpenAI; actual behaviour UNCONFIRMED)
- Reasoning / thinking:
  - `reasoning_effort` is a top-level chat param: `none|minimal|low|medium|high|xhigh|max`. CONFIRMED in schema. No Token Factory doc page explains how it maps onto Nemotron's chat template. UNCONFIRMED effect.
  - Response: `ChatCompletionMessage.reasoning_content` and `.reasoning` (both "The reasoning content of the message"), also on `ChatCompletionStreamResponseDelta`; `usage.completion_tokens_details.reasoning_tokens`. CONFIRMED in schema. Note messages you SEND may also carry `reasoning_content`/`reasoning` (schema allows it).
  - `chat_template_kwargs`, `enable_thinking`, `reasoning_budget`, `thinking`: zero occurrences in the OpenAPI spec and no docs page. Since `extra_body` is accepted and the schema allows extra fields, `extra_body={"chat_template_kwargs":{"enable_thinking":False}}` (the NVIDIA/vLLM convention, see D) may pass through to the vLLM-style engine, but this is UNCONFIRMED and must be tested. The cookbook `run_nemotron.ipynb` sends no reasoning params at all.
  - Docs index (llms.txt) has no page on reasoning, thinking, seed, logprobs, prompt caching, or batch. CONFIRMED https://docs.tokenfactory.nebius.com/llms.txt

### A5. Rate limits, caching, logprobs, fine-tuning
- Rate limits: dynamic. Limit grows x1.2 for the next window when 15-min avg usage >= 80% of limit, shrinks /1.5 when <= 50%, up to 20x base allocation; Startup vs Enterprise tiers; HTTP 429 with `Retry-After`; headers `x-ratelimit-limit-requests/tokens`, `x-ratelimit-remaining-*`, `x-ratelimit-reset-*`, `x-ratelimit-dynamic-*`, `x-ratelimit-over-limit: yes`. Concrete base RPM/TPM numbers are NOT published on the page (60 RPM / 400k TPM appears only as an illustration). `service_tier` param: `auto|default|over-limit|flex|no-limit`. CONFIRMED https://docs.tokenfactory.nebius.com/ai-models-inference/rate-limits.md
- Prompt caching: `usage.prompt_tokens_details.cached_tokens` is in the response schema and the catalog has a cached-input price field, but every model's cached price is null and the public feature request "Support implicit/explicit prompt caching" (ideas.nebius.com, 32 votes, last comment Sep 2 "still no cache") has no official reply. Treat caching as NOT available. CONFIRMED-absent https://ideas.nebius.com/p/support-impliciteexplicite-prompt-caching
- Batch API: the rate-limits page says "the Batch API has significantly higher limits" and the cookbook `batch.ipynb` uses `client.files.create` + `client.batches.create(completion_window="24h")`, but `/v1/batches` is absent from the OpenAPI spec. UNCONFIRMED.
- Fine-tuning catalog (49 models, LoRA/Full): DeepSeek, Gemma-4, gpt-oss, Qwen2.5/3/3.5/3.6, Llama 3.x. NO Nemotron model is fine-tunable. CONFIRMED https://docs.tokenfactory.nebius.com/post-training/models.md
- Dedicated endpoints: user-selected fixed region, min/max replica autoscaling, billed per GPU/hour at per-minute granularity. CONFIRMED https://docs.tokenfactory.nebius.com/ai-models-inference/dedicated-endpoints/overview.md

### A6. Hackathon promo / credits
- Official rules (https://nebiusglobalaihackathon.devpost.com/rules, fetched 2026-10-15): "Entrants can join the Nebius Builder Program and get credits for Nebius Token Factory, Tavily, and Nebius Academy". No dollar amount in the rules. Deadline Oct 30, 2026 10:00am PDT. "Runs on Nebius Token Factory or Nebius AI Cloud" = runtime call to the Token Factory inference API, OR deployed/run on AI Cloud compute "(Serverless Jobs, Serverless Endpoints, or DevPods)". Bonus "Best Use of Tavily" $3,000 for a functional runtime Tavily API call. Track guidance: "Reach for Nemotron 3 Ultra when you need serious reasoning, and let Nano or Super handle the fast, everyday calls". CONFIRMED
- Secondary source claims $25 Token Factory credits via promo code `NEBIUS-DEVPOST-GLOBAL26` on the event resources page, plus $25 for joining the Builders Program. UNCONFIRMED https://tierones.io/opportunities/nebius-nvidia-ai-2026 (check the Devpost "Resources" tab when logged in).

---

## B. Nebius AI Cloud "Serverless AI" (Jobs / Endpoints)

### B1. Product and job submission
- Official name in docs: **Serverless AI**, with three workload types: **Devlabs** (docs) / "DevPods" (marketing + hackathon rules), **Jobs**, **Endpoints**. Available in all regions except private eu-north2. "usage-based, per-second billing". CONFIRMED https://docs.nebius.com/serverless , https://docs.nebius.com/serverless/overview.md
- Jobs = "container images as one-off or scheduled batch workloads"; run on a Compute container VM, billed only while running; "Creation usually takes a few minutes"; if no capacity, retries up to 30 min then ERROR `NotEnoughResources`. CONFIRMED https://docs.nebius.com/serverless/jobs/manage.md , https://docs.nebius.com/serverless/lifecycle.md
- Three submission paths, all CONFIRMED:
  1. CLI: `nebius ai job create --name X --image REG/PATH:TAG [--container-command bash --args "-c ..."] [--env K=V ...] [--env-secret K=SELECTOR] [--inject-file LOCAL:/abs/path] [--volume ...] --platform cpu-d3 --preset 2vcpu-8gb [--timeout 1h] [--disk-size 250Gi] [--parent-id project-...] [--subnet-id ...] [--working-dir /abs] [--preemptible --follows-spot-price] [--restart-policy never|on-failure] [--async] [--dry-run] [--format json|yaml|jsonpath]`.
     https://docs.nebius.com/cli/reference/ai/job/create.md . Interactive alternative: `nebius ai create --type job ...` (quickstart https://docs.nebius.com/serverless/quickstart/jobs.md).
  2. REST: `POST https://api.nebius.cloud/ai/v1/jobs` body `{ "metadata": {"parentId": "<project id>", "name": "..."}, "spec": { "image": "...", "containerCommand": "...", "args": [...], "workingDir": "...", "environmentVariables": [{"name":"K","value":"V"} | {"name":"K","mysteryboxSecret":{"secretId":..,"versionId":..}}], "injectedFiles": [{"containerPath":"/abs/file","content":"<bytes, 1B..64KiB>"}], "volumes": [{"source":"<bucket id | filesystem id | s3://bucket>", "containerPath":"/abs", "mode":"READ_WRITE|READ_ONLY", "sourcePath":"...", "s3Config":{"endpoint":..,"region":..,"credentials":{"accessKeyId":..,"secretAccessKey":..}}}], "platform":"cpu-d3", "preset":"2vcpu-8gb", "disk":{"sizeBytes":"<int64 str>","type":"NETWORK_SSD"}, "timeout":"3600s", "restartAttempts":"0", "preemptible":false, "publicIp":false, "subnetId":"...", "sshAuthorizedKeys":[...], "shmSizeBytes":"..." }, "dryRun": false }`. Only `spec.image` and `metadata.parentId` are required. Response = `nebius.common.v1.Operation` with `id`, `resourceId` (the job id), `status`. CONFIRMED https://docs.nebius.com/rest-api/ai/v1/jobs/create
  3. gRPC / SDK: proto `nebius/ai/v1/job_service.proto`, `service JobService { Get, GetByName, List, Create, Delete, Cancel, Restart }` (api_service_name "apps.msp"). CONFIRMED https://github.com/nebius/api/blob/main/nebius/ai/v1/job_service.proto
- Limits/defaults: timeout 1h..168h, default 24h; disk default 250Gi; `/dev/shm` 16Gi GPU / 0 CPU; injected files read-only, <= 64 KiB each; `--subnet-id` required only if the project has >1 subnet; platform default gpu-h100-sxm (eu-north1) / gpu-h200-sxm (elsewhere), preset default = smallest. CONFIRMED
- Input injection options (CONFIRMED): env vars (`--env`), secrets from MysteryBox/SecretStash (`--env-secret`), small files (`--inject-file`, <=64 KiB), mounted Object Storage bucket (`--volume 'bucket-id:/path:rw'` or `--volume 's3://bucket:/path:rw:PROFILE'`, profile from AWS creds or `PROFILE@SECRET_SELECTOR`), shared filesystem. Container image itself.
- Output retrieval (CONFIRMED): mount a bucket/filesystem read-write and write results there ("Buckets or shared filesystems can be mounted to store results and checkpoints"; container disk is deleted on completion). Volumes persist after job ends. Logs: ONLY via console or CLI (`nebius ai job logs <id> [--follow] [--since 1h] [--until] [--tail N] [--timestamps]`), NOT via REST. https://docs.nebius.com/cli/reference/ai/job/logs.md
- Status polling: `GET /ai/v1/jobs/{id}` -> `status.state` in `STATE_UNSPECIFIED, PROVISIONING, STARTING, IMAGE_PULLING, RUNNING, CANCELLING, DELETING, COMPLETED, FAILED, CANCELLED, ERROR`; `status.stateDetails.{code,message,serviceError}`, `startedAt`, `finishedAt`, `instances[].{computeInstanceId,state,privateIp,publicIp}`. No exit-code field. CLI: `nebius ai job get <id>`, `get-by-name`, `list --parent-id`, `cancel`, `delete`, `ssh` (needs `--ssh-key`). Cancel REST: `POST /ai/v1/jobs/cancel`. CONFIRMED https://docs.nebius.com/rest-api/ai/v1/jobs/get
- Cold start: no published number. Jobs doc: "Creation usually takes a few minutes"; endpoints doc: "approximately five minutes". Expect minutes, not seconds. CONFIRMED (wording)
- Pricing: Serverless AI has no own pricing; Compute VM + disk pricing applies, per-second billing. CPU platform "AMD Epyc Genoa" (= cpu-d3) $0.015/vCPU-h + $0.0045/GiB-h (from Oct 1 2026) -> 2vcpu-8gb ~ $0.066/h (illustrative); L40S $1.35/GPU-h; H100 $4.50, H200 $5.40 /GPU-h; network SSD $0.071/GiB per 730 h. CONFIRMED https://docs.nebius.com/serverless/pricing-quotas.md , https://docs.nebius.com/compute/resources/pricing.md
- CPU platforms/presets: `cpu-d3` presets `2vcpu-8gb, 4vcpu-16gb, 8vcpu-32gb, 16vcpu-64gb, ... 256vcpu-1024gb` in most regions (incl. us-central1, eu-north1); `cpu-e2` (2vcpu-8gb..80vcpu-320gb) only eu-north1. GPU: `gpu-l40s-a` `1gpu-8vcpu-32gb` (eu-north1), `gpu-h100-sxm`/`gpu-h200-sxm` `1gpu-16vcpu-200gb`, `gpu-b200-sxm` (us-central1). CONFIRMED https://docs.nebius.com/compute/virtual-machines/types.md

### B2. Auth, env vars, project id, regions
- REST host `https://api.nebius.cloud` (no regional hosts documented). Header `Authorization: Bearer <access_token>`. User token: `nebius iam get-access-token` (valid 12 h). Service account: RSA key + `nebius iam auth-public-key create`, sign a 5-min RS256 JWT, exchange at `https://auth.eu.nebius.com:443/oauth2/token/exchange` (grant_type token-exchange, subject_token_type jwt) -> token valid 43200 s. CONFIRMED https://docs.nebius.com/rest-api/authentication.md
- Python SDK env: `NEBIUS_IAM_TOKEN` (default), `NEBIUS_PROFILE` (CLI profile), or `credentials_file_name=...json` / service-account key file. There is NO `NEBIUS_API_KEY` for AI Cloud (that name belongs to Token Factory). CONFIRMED https://github.com/nebius/pysdk
- Project/parent id: `metadata.parentId` (REST) / `--parent-id` (CLI; defaults from `~/.nebius/config.yaml`) / `SDK(parent_id="project-...")` (pysdk auto-fills `metadata.parent_id`). CONFIRMED
- Regions: `eu-north1` (Finland), `eu-south1` (Madrid), `eu-west1`, `eu-west2` (France), `me-west1` (Israel), `us-central1` (Kansas City), `us-north1` (Woodbury MN), `uk-south1`, `uk-south2`; `eu-north2` private. Serverless AI available in all public ones. CONFIRMED https://docs.nebius.com/overview/regions.md

### B3. Serverless Endpoints: autoscaling / scale-to-zero
- `nebius ai endpoint create --name --image --container-port 8000/http [--auth token --token ...] --platform --preset [--disk-size]`; REST `POST /ai/v1/endpoints`; managed `https://` URL per HTTP port; optional Bearer token auth; creation "approximately five minutes"; endpoints can be stopped/started; "Computing resources of stopped endpoints aren't charged" (mounted volumes still billed). CONFIRMED https://docs.nebius.com/serverless/endpoints/manage.md
- `EndpointSpec` has NO `replicas`, `minReplicas`, `maxReplicas`, `autoscaling`, `scaleToZero` or `idleTimeout` fields; no autoscaling or scale-to-zero is documented anywhere for Serverless AI endpoints (only manual stop/start). CONFIRMED-absent https://docs.nebius.com/rest-api/ai/v1/endpoints/create . (Token Factory *dedicated endpoints* are a different product and do have min/max replicas.)

### B4. Python SDK (`nebius` on PyPI)
- `pip install nebius` -> 0.6.24 (released 2026-10-15), Python >= 3.10, gRPC-based, repo https://github.com/nebius/pysdk . CONFIRMED (PyPI JSON)
- Serverless AI module exists: `nebius.api.nebius.ai.v1` with `JobServiceClient` (methods `get, get_by_name, list, create, delete, cancel`), `EndpointServiceClient`, `JobSpec`, `JobSpec__EnvironmentVariable`, `JobSpec__VolumeMount` (+`__S3Config`), `JobSpec__DiskSpec`, `JobSpec__FileInjection`, `JobStatus`, `GetJobRequest`, and (per proto) `CreateJobRequest`, `ListJobsRequest`, `CancelJobRequest`. CONFIRMED by reading https://raw.githubusercontent.com/nebius/pysdk/main/src/nebius/api/nebius/ai/v1/__init__.pyi . No docs page shows a jobs example; pattern below is adapted from the official compute example and is UNCONFIRMED as written:

```python
# Pattern CONFIRMED from docs (compute example); ai.v1 field names from job.proto -- untested
import asyncio
from nebius.sdk import SDK
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.ai.v1 import JobServiceClient, CreateJobRequest, JobSpec, GetJobRequest


async def main():
    sdk = SDK(parent_id="project-...")  # reads NEBIUS_IAM_TOKEN
    svc = JobServiceClient(sdk)
    op = await svc.create(
        CreateJobRequest(
            metadata=ResourceMetadata(parent_id="project-...", name="ratchet-run"),
            spec=JobSpec(
                image="ghcr.io/...:tag",
                container_command="python",
                args=["run.py"],
                platform="cpu-d3",
                preset="2vcpu-8gb",
                environment_variables=[JobSpec.EnvironmentVariable(name="K", value="V")],
            ),
        )
    )
    await op.wait()
    job = await svc.get(GetJobRequest(id=op.resource_id))
    print(job.status.state)
    await sdk.close()


asyncio.run(main())
```
Official SDK docs: https://docs.nebius.com/sdk/python/install-auth.md , https://docs.nebius.com/sdk/python/send-request.md

### B5. Object Storage S3 endpoint
- Endpoint template `https://storage.<region_ID>.nebius.cloud` (e.g. `https://storage.eu-north1.nebius.cloud`); signing region = the region id (`aws configure set region eu-north1`); credentials = service-account access key (`.status.aws_access_key_id`, `.status.secret`). HTTPS only by default. `ListObjectsV2`, multipart supported; object ACLs, SSE-KMS, Object Lock unsupported. Path-style vs virtual-hosted and presigned URLs are not documented. CONFIRMED https://docs.nebius.com/object-storage/interfaces/aws-cli.md , https://docs.nebius.com/object-storage/interfaces/s3-api-compatibility.md
- Jobs can mount a bucket by bucket id (`--volume 'bucket-id:/output:rw'`) without S3 keys, or any S3 URI with `s3Config{endpoint,region,credentials}`. CONFIRMED

---

## C. Tavily

### C1. Python SDK
- `tavily-python` 0.8.5 (2026-10-06), Python >= 3.8, deps `requests`, `tiktoken>=0.5.1`, `httpx`; exports `TavilyClient`, `AsyncTavilyClient`, `TavilyHybridClient`, errors `InvalidAPIKeyError`, `UsageLimitExceededError`, `MissingAPIKeyError`, `BadRequestError`. CONFIRMED (PyPI JSON; https://github.com/tavily-ai/tavily-python)
- `TavilyClient(api_key, ...)`; constructor also takes `api_base_url` (default `https://api.tavily.com`), `project_id`, `session_id`, `human_id`, `proxies`; env `TAVILY_PROJECT`, `TAVILY_HTTP_PROXY`, `TAVILY_HTTPS_PROXY`. `AsyncTavilyClient(api_key)` or `AsyncTavilyClient(client=httpx.AsyncClient(...))`. CONFIRMED https://docs.tavily.com/sdk/python/reference.md
- `search()` signature from source (tavily/tavily.py, master): CONFIRMED
  `search(query, search_depth: "basic"|"advanced"|"fast"|"ultra-fast"=None, topic: "general"|"news"|"finance"=None, time_range: "day"|"week"|"month"|"year"=None, start_date: "YYYY-MM-DD"=None, end_date=None, days: int=None, max_age_hours: int=None, max_results: int=None (API default 10, 0..20), include_domains: list=None (max 300), exclude_domains=None (max 150), include_domains_mode: "restrict"|"prefer"=None, include_answer: bool|"basic"|"advanced"=None, include_raw_content: bool|"markdown"|"text"=None, include_images: bool=None, timeout: float=60, country=None, auto_parameters=None, include_favicon=None, include_usage=None, exact_match=None, fetch_timeout=None, cache_fallback=None, language=None, filter_by_language=None, **kwargs) -> dict`
  - `days` is still in the SDK signature, but the current REST reference lists NO `days` param ("recency filtering uses time_range, start_date, end_date"); treat `days` as deprecated/ignored. UNCONFIRMED behaviour. `time_range` short forms `d|w|m|y` accepted by REST.
  - `published_date` per result appears only when `include_published_date: true` (auto-enabled for `topic: news`, beta) or `filter_by_published_date: true`; pass via `**kwargs` from the SDK. Otherwise results have no `published_date`. CONFIRMED https://docs.tavily.com/documentation/api-reference/endpoint/search
- Search response: `{query, results[], response_time, request_id, answer? (include_answer), images? , auto_parameters?, usage?{credits}}`; each result `{title, url, content, score, raw_content? (include_raw_content), published_date? (see above), favicon?, images?, id}`. CONFIRMED
- `extract(urls: str|list (<=20), include_images=None, extract_depth: "basic"|"advanced"=None, format: "markdown"|"text"=None, timeout=30, include_favicon=None, include_usage=None, query=None, chunks_per_source=None (1..5, only with query), **kwargs) -> dict`. Response `{results:[{url, raw_content, images?, favicon?}], failed_results:[{url, error}], response_time, usage?, request_id}`; HTTP 200 may still have empty `results`, always check `failed_results`. CONFIRMED (source + https://docs.tavily.com/documentation/api-reference/endpoint/extract)
- Credits: search `basic`/`fast`/`ultra-fast` 1 credit, `advanced` 2; `auto_parameters` 2 (set `search_depth="basic"` explicitly to avoid); extract basic 1 credit per 5 successful URLs, advanced 2 per 5 (failures free); free tier 1,000 credits/month, no card. CONFIRMED https://docs.tavily.com/documentation/api-credits
- Rate limits: dev key 100 RPM, production key 1,000 RPM (general endpoints); 429 with optional `Retry-After`. CONFIRMED https://docs.tavily.com/documentation/rate-limits

### C2. Raw REST
- `POST https://api.tavily.com/search`, `POST https://api.tavily.com/extract`, JSON body, header `Authorization: Bearer tvly-YOUR_API_KEY`, `Content-Type: application/json`. SDK itself sends exactly this (source line: `{"Authorization": f"Bearer {self.api_key}"}`, `self.base_url + "/search"`). CONFIRMED
- Extra REST-only search params: `chunks_per_source` (1..3), `include_published_date`, `filter_by_published_date`, `include_image_descriptions`, `safe_search` (not for fast/ultra-fast), `include_usage`. CONFIRMED

---

## D. NVIDIA build.nvidia.com / NIM reference for Nemotron 3 reasoning

- Hosted endpoint: `base_url="https://integrate.api.nvidia.com/v1"`, `api_key=$NVIDIA_API_KEY`; curl model id `nvidia/nemotron-3-super-120b-a12b`; Ultra page slug `nemotron-3-ultra-550b-a55b`, Nano slug `nemotron-3-nano-30b-a3b` (HF `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16`). CONFIRMED https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b , https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b
- Thinking toggle (Nemotron 3 family, all three pages + HF card): request `extra_body={"chat_template_kwargs": {"enable_thinking": True|False}}`; default `enable_thinking=True`. Effort knobs inside the same dict: Super `{"enable_thinking": True, "low_effort": True}`; Ultra `{"enable_thinking": True, "medium_effort": True}`; Ultra also recommends `"force_nonempty_content": True` for coding agents. CONFIRMED
- Reading reasoning: `getattr(completion.choices[0].message, "reasoning_content", None)`; streaming: `chunk.choices[0].delta.reasoning_content`. CONFIRMED (Ultra page prototype code)
- `reasoning_budget` is NOT a native API field on these pages; it is an argument of a `ThinkingBudgetClient` helper that first calls with `max_tokens=reasoning_budget` (model tries to close `</think>` at next newline, hard cut at budget+500), then continues. The Nano-Omni page does show `"chat_template_kwargs": {"enable_thinking": True, "reasoning_budget": 16384}` plus `"thinking_token_budget"` in extra_body for a vLLM server. CONFIRMED (page-specific)
- System-prompt tokens: `/think` appears in system messages in examples ("You are a helpful assistant. /think"); `/no_think` is NOT documented for Nemotron 3 (it is the Nemotron 1.5 / Llama-3.3-Nemotron mechanism). CONFIRMED-absent
- Serving stack: vLLM `--reasoning-parser nano_v3` (Nano) / `super_v3` (Super) with `--tool-call-parser qwen3_coder --enable-auto-tool-choice`; recommended sampling temperature 1.0 / top_p 0.95 (Super), Nano: temp 1.0/top_p 1.0 for reasoning, 0.6/0.95 for tool calling. CONFIRMED (build.nvidia.com + HF card https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16)
- Capability flags on build.nvidia.com: Super: function calling, structured output, reasoning all "Supported"; Nano: function calling + reasoning supported, structured output "Not supported". CONFIRMED
- NIM generic reasoning doc (https://docs.nvidia.com/nim/large-language-models/latest/reasoning-model.html) rendered empty to the fetcher; its content ("enable_thinking defaults True") is known only via search snippets. UNCONFIRMED.
- Implication for Token Factory: Token Factory exposes `reasoning_effort` and returns `reasoning_content`, consistent with a vLLM-style backend, so `extra_body.chat_template_kwargs.enable_thinking` is the most likely passthrough toggle, but nothing official says so (see A4).

---

## Still unverified -- must test with a real key

Token Factory (NEBIUS_API_KEY):
1. `GET /v1/models` -- confirm the Nemotron ids above resolve and whether `nvidia/nvidia-nemotron-3-nano-30b-a3b` (lowercase) also works.
2. Does `extra_body={"chat_template_kwargs":{"enable_thinking":false}}` actually disable thinking on Nano/Super/Ultra? Does `reasoning_effort: "none"` do the same? Does `/think` in the system prompt matter?
3. Where does the trace land: `message.reasoning_content` vs `message.reasoning` vs inline `<think>` in `content` (non-stream and stream)? Is `usage.completion_tokens_details.reasoning_tokens` populated?
4. `response_format: json_schema` with Nemotron (NVIDIA says Nano lacks structured output); fallback `extra_body.guided_json` + `json_object`.
5. `seed` honoured? `logprobs` returned for Nemotron? `stream_options.include_usage` on Nemotron?
6. Tool calling round-trip on Nemotron (tool_calls parsing with thinking on/off).
7. Responses API `/v1/responses` with a Nemotron model id (reasoning.effort, text.format).
8. Whether the global host vs `api.tokenfactory.us-central1.nebius.com` differ in latency/availability for Super/Ultra (catalog region us-central1) and Nano (eu-north1).
9. Actual base rate limits (RPM/TPM) returned in `x-ratelimit-*` headers; `cached_tokens` always 0?
10. Trial credit ($1/30 days) and hackathon promo code `NEBIUS-DEVPOST-GLOBAL26` ($25, secondary source).

Nebius AI Cloud (IAM token):
11. `nebius ai job create` with `--platform cpu-d3 --preset 2vcpu-8gb` in your project's region (CPU quota, default subnet) and real cold-start latency (PROVISIONING->RUNNING).
12. pysdk `nebius.api.nebius.ai.v1` request/field names (`CreateJobRequest`, `JobSpec.container_command`, `timeout` as Duration) -- example above is untested.
13. Bucket mount by bucket id vs `s3://` + static key; whether a service-account static key needs `storage.editor` on the bucket.
14. No REST logs endpoint: plan for CLI `nebius ai job logs` or write logs to the mounted bucket.

Tavily (TAVILY_API_KEY):
15. Whether `days` is still accepted or rejected (400) by `/search`; `published_date` presence with `include_published_date=true` on `topic="general"`.
