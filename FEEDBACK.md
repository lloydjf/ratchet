# Feedback on Nebius and NVIDIA tools

To the Token Factory, Serverless AI, NemoClaw/OpenShell and Hermes teams, from the team
that built Ratchet for the Nebius × NVIDIA Global AI Hackathon (October 2026).

Ratchet is regression CI for self-improving agents. Every time an agent edits one of its
skills, it replays that agent's past successful tasks and judges the results with three
Nemotron tiers. That makes it an unusually demanding Token Factory customer. It needs
structured outputs from every tier, a reliable thinking toggle, repeatable sampling, cheap
repeated prefixes, and a job runner that can fan out replays.

Each item below follows the same shape: what we tried, what happened, what we did
instead, and what we would want.

Every number here comes from a live call or from `docs/VERIFIED.md`. An item we could not
measure is marked **not measured yet**.

## 1. Token Factory

**Model discovery.** We read model ids from the public catalog JSON
(`/api/public/models_info`), then confirmed them with `GET /v1/models`. All three tiers
were present under the documented ids:

- `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`
- `nvidia/nemotron-3-super-120b-a12b`
- `nvidia/Nemotron-3-Ultra-550b-a55b`

The ids use three different casing conventions, so code that guesses an id from a model
name fails. We'd like one canonical, case-insensitive id scheme, or a `/v1/models` field
for display names.

**Thinking control.** Nemotron 3 reasons by default, and Token Factory doesn't document
how to turn that off. We tested both candidates live.

- `chat_template_kwargs: {"enable_thinking": false}` worked on all three tiers. It brought
  reasoning tokens down to 0, and a two-word answer dropped from 48–74 completion tokens to 2.
- `reasoning_effort: "none"`, which *is* in the OpenAPI spec, returned **empty content** on
  Nano.
- Reasoning also lands in different fields per tier: Nano uses `message.reasoning`, Super
  uses both `reasoning` and `reasoning_content`, and Ultra uses `reasoning_content`.

We normalized all of this in the proxy and made thinking-off the default for agent and
screening calls. One documented, per-model-tested toggle and one reasoning field would
have saved us a day of experiments.

**Structured outputs.** `response_format: json_schema` with `strict: true` returned valid,
schema-matching JSON on Nano, Super and Ultra in our live checks. This was the most
pleasant surprise of the build. We kept a validate-and-retry fallback anyway. Measured on
the 2026-10-16 benchmark sweeps: **1 upstream retry in 15,201 model calls** (one 5xx or
transport failure, retried once, no verdict lost), and no replay crashed, so the
one-shot replay retry round never fired. One wrinkle: in a burst of twelve calls within
two seconds (12:48 UTC, the Nano-only ablation), Nano rejected the `json_schema`
`response_format` outright; the client fell back to `json_object` for that tier and every
reply still parsed. A transient format rejection is worth a note in the docs.

**Seed.** `seed` isn't in the OpenAPI schema, but the request was accepted. Three identical
requests with `seed=7` at temperature 0.8 gave three different outputs, so seed isn't
honored. Ratchet's honesty depends on replays being as repeatable as possible, so we fall
back to temperature 0 plus a semantic judge, and we report a measured noise floor (bench
E6). A honored `seed` would directly lower Ratchet's false-alarm rate. It's our top request.

**Prompt caching.** Super's `usage` block already carries `prompt_cache_hit_tokens` /
`prompt_cache_miss_tokens`, but there is no cached-input price in the catalog.

Ratchet's prompts are very cache-friendly. Every replay of a task repeats the same system
prompt and skill index, and every judge call shares a long fixed preamble. Yet the measured
hit share was **0 of 43.3 million prompt tokens** across the 2026-10-16 sweeps (15,201
calls), and two identical 924-token Super requests sent back to back both reported
`prompt_cache_hit_tokens: 0`; Nano's `usage` carries no `prompt_tokens_details` at all. So
either the prefix cache is not active on these models or the counter is not wired up.
Either way, a cached-input price would be moot until it reports hits. Discounted cache
pricing would cut the cost of a verdict roughly in proportion to the hit share.

**Streaming usage.** `stream_options.include_usage` works, and the final chunk carries
`usage`. Thank you.

**Latency.** Short calls took 0.3–0.9 s on every tier. That's fast enough for an
interactive "Simulate" button.

**Fine-tuning.** Nemotron is not in the Token Factory post-training catalog, which covers
Qwen, Llama, gpt-oss, Gemma and DeepSeek. Our use case would be distilling the Nano
screen from Ultra's adjudications (roadmap v0.5) to lower both cost and false blocks.

**Logprobs.** We didn't depend on them. A judge confidence derived from token logprobs
would beat the self-reported `confidence` field we use today.

## 2. Serverless AI (Jobs)

**What we built.** A `NebiusJobRunner` that does three things:
- creates one job per batch of up to 10 replays (`POST /ai/v1/jobs`);
- moves inputs and outputs through an Object Storage bucket volume;
- polls `status.state`.

**File injection.** `--inject-file` is limited to 64 KiB. A replay batch (goldens,
cassettes, a skills tarball, workspace snapshots) is far larger, so everything goes through
a bucket. A job-scoped scratch volume, or a larger inject limit, would remove the S3 setup
from the critical path.

**Logs.** There is no REST endpoint for job logs; the CLI and console are the only way to
read them. Our worker writes `worker.log` to the output bucket, so the dashboard can show
it. A logs endpoint, even a tail, would make job debugging possible from inside a product.

**Cold start.** The docs say "a few minutes". Our measured cold start is **not measured
yet**. We planned around it: the demo path uses a warm local runner, and
Jobs carry the bench sweep. A job template or image pre-pull would make Jobs viable for
interactive paths.

## 3. Serverless Endpoints vs a VM

The control plane is a long-running FastAPI process serving a dashboard and SSE. Endpoints
have no autoscaling and no scale-to-zero fields in the spec, so we chose a VM. A
single-replica, always-on Endpoint with a health check would have been our first choice.

## 4. NemoClaw / OpenShell

OpenShell requires Linux ≥ 6.2 with Landlock ABI 3, and running it inside an unprivileged
container is undocumented. So replays in Jobs use a locked-down Docker sandbox, and
OpenShell is the production path on a VM. The filesystem and process policy is locked when
the sandbox is created. That suits Ratchet, since every replay gets a fresh sandbox.

A "policy from a rehearsal run" feature would remove most of our policy authoring. It would
record the hosts and paths a blessed run touched, then propose the minimal policy. We'd
also want the audit trail as machine-readable events, so a denied egress could become
Level 0 evidence directly.

## 5. Hermes Agent

Hermes v0.22.2 has a first-class one-shot mode (`hermes chat --oneshot --format
stream-json`), `HERMES_HOME` isolation, and MCP over streamable HTTP. That covered most of
the adapter surface we needed.

What was missing:
- a `seed` / `temperature` configuration key, so we inject both in the proxy;
- a per-run id header (we use path-form base URLs);
- a clock hook for frozen time (we use libfaketime);
- a structured "skill invoked" event (we infer it from `skill_view` tool calls).

## 6. Tavily

Drift checks search the fact Ratchet extracted when the user blessed the task, filtered
dynamically by the claim's source domain and observation date (`include_domains`,
`time_range` or `start_date`). Search worked in 1.6 s.

The page `content` for finance quote pages is mostly navigation text. Tavily's
`include_answer` gave a clean "The EUR to USD exchange rate today is 1.1212 – 1.1223", so
Ratchet now leads its evidence with the answer.

A "has this fact changed since \<date\>?" primitive would make Ratchet's drift check one
call instead of two. It would take a claim and a date, and return changed/unchanged with
the old and new value and a source.

## 7. Three requests, ranked

1. **Honor `seed` on Token Factory.** It directly lowers Ratchet's false-alarm floor and
   makes every replay-based evaluation tool more trustworthy.
2. **Document one thinking toggle and one reasoning field for Nemotron 3.** It removes a
   class of silent cost and behavior differences between tiers.
3. **Larger file injection, or job-scoped scratch volumes, for Serverless Jobs.** It makes
   fan-out replay possible without bucket plumbing, and unlocks the Serverless demo path.
