# Research: Hermes Agent, NVIDIA NemoClaw, NVIDIA OpenShell, Agent Skills, OpenClaw, MCP Python SDK

Researched online 2026-10-15 for Ratchet (regression CI for self-improving agents).
Every fact is tagged **CONFIRMED** (seen in a primary source: official docs, repo source, PyPI) or
**UNCONFIRMED** (secondary source, or inferred). Source URLs are given per item.

---

## Recommended adapter design (HermesAdapter)

Concrete plan, derived from the CONFIRMED facts below. Items marked (!) rely on something UNCONFIRMED.

**Install (Linux/macOS/WSL2, non-interactive):**
```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive --skip-browser --skip-computer-use
source ~/.bashrc   # launcher is ~/.local/bin/hermes; source checkout at ~/.hermes/hermes-agent/
```
Hermes provisions its own Python 3.14 via uv. No PyPI-based install is documented (PyPI `hermes-agent` exists but
lags: 0.19.0 vs repo v0.22.2). Pin to a tag is undocumented (!) — record `hermes --version` output per run instead.

**Isolate state per run:** set `HERMES_HOME=<run_dir>` (CONFIRMED env var; default `~/.hermes`). Everything —
`config.yaml`, `.env`, `skills/`, sessions `state.db`, logs — lives under it. This lets Ratchet own the skills dir
as a git repo and swap it per replay without touching the user's real `~/.hermes`.

**Config to write (`$HERMES_HOME/config.yaml`):**
```yaml
model:
  default: "<model-id>"
  provider: "custom"                 # any OpenAI-compatible endpoint
  base_url: "http://127.0.0.1:<ratchet-proxy-port>/v1"
providers:
  custom:
    api_key: "${RATCHET_PROXY_KEY}"  # or key_env: RATCHET_PROXY_KEY
    # extra_headers: { X-Ratchet-Run: "${RATCHET_RUN_ID}" }   # per-provider static headers (CONFIRMED key)
    # session_affinity_header: x-ratchet-session               # sends Hermes session id in this header (CONFIRMED key)
agent:
  max_turns: 200
  disabled_toolsets: [web, browser, cronjob, delegation, messaging, discord, x_search, image_gen, tts, vision, video, memory]  # tune
skills:
  creation_nudge_interval: 15        # self-improvement nudge cadence (CONFIRMED key)
  # external_dirs: [...]             # read-only extra skill dirs (CONFIRMED key)
mcp_servers:
  ratchet_mock:
    url: "http://127.0.0.1:<mock-port>/mcp"    # streamable-HTTP MCP server (CONFIRMED format)
    # or: command: "python", args: ["-m", "ratchet.mock_server"]   # stdio
    timeout: 120
```
Alternatively pass the endpoint per run on the CLI: `hermes chat --provider custom --base-url ... --api-key ... -m <model>`
(CONFIRMED flags; `--api-key` is held in memory only, never written to disk).

**Run one task non-interactively (preferred, machine-readable):**
```bash
HERMES_HOME=$RUN_DIR hermes chat --oneshot -Q --format stream-json -q "<task>" --usage-file $RUN_DIR/usage.json \
    [--ignore-user-config] [--max-turns N] [-t "terminal,file,skills,mcp-ratchet_mock"] [-s <skill>]
```
stdout = one JSON object per line with events `system`(init: model, session id), `text`, `tool_use`(tool name + input),
`tool_result`(name, output ≤5000 chars, duration, error flag), `result`(session id, exit_code, final text, token counts).
Process exit code mirrors `result.exit_code` (0 ok, 1 fail, 130 interrupt). For plain final text only: `hermes -z "<task>"`.
Ratchet can detect skill usage from `tool_use` events named `skills_list` / `skill_view` / `skill_manage`.

**Mock tool server as MCP:** register under `mcp_servers` (above). Tools appear as `mcp_<server>_<tool>` in toolset
`mcp-<server>`; use `tools.include`/`tools.exclude` globs to limit. MCP tools do NOT replace built-ins — disable the real
side-effecting toolsets via `agent.disabled_toolsets` and/or `-t` so the model only sees the mock ones.

**Skills path & reload:** `$HERMES_HOME/skills/<name>/SKILL.md` (agentskills.io-compatible; Hermes extras under
`metadata.hermes`). Skills index is baked into the system prompt at session start, so for replay: edit skill on disk,
then start a fresh `hermes chat --oneshot` process — no hot-reload needed. (`/reload-skills` exists only for interactive
slash commands.) Agent-created skills land in `$HERMES_HOME/skills/` (or `skills.create_dir`), so `git diff` that dir
after each run to capture self-improvement.

**Determinism caveats:** Hermes does not expose `temperature`/`seed` in config (only `max_tokens`, `reasoning_effort`);
Ratchet's proxy should inject/override them. System prompt contains the current date/time from `hermes_time.now()`
(server local or `HERMES_TIMEZONE`); no clock-freeze hook exists — proxy-side normalisation needed for cache/replay matching.

**NemoClaw/OpenShell:** optional "hardened" mode. OpenShell gives default-deny egress + Landlock FS; Ratchet's proxy and
mock MCP would be the only allowed hosts. Requires Linux kernel ≥ 6.2 (Landlock ABI 3) + Docker 28 / Podman 5 — not
viable inside an unprivileged CI container; run on a VM.

---

## A. Hermes Agent (Nous Research)

### A1. Repo, version, install, Python
- Repo: https://github.com/NousResearch/hermes-agent — MIT, default branch `main`. **CONFIRMED** (GitHub API: https://api.github.com/repos/NousResearch/hermes-agent)
- Latest release: **v0.22.2 (2026-10-15)**; v0.22.1 (10-14, wrong commit), v0.22.0 "The Threshold Release" (10-13/14). **CONFIRMED** https://github.com/NousResearch/hermes-agent/releases
- Install (Linux/macOS/WSL2): `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`; Windows: `iex (irm https://hermes-agent.nousresearch.com/install.ps1)`. Installer flags: `--non-interactive`, `--skip-browser`, `--skip-computer-use`, `--channel main`, `--verbose`, `--dir` (checkout dir), `--include-desktop`. Scripts "clone the source, bootstrap uv" and use a package manager ("PM") for pinned Python 3.14, Node, ripgrep, FFmpeg. Source lands at `~/.hermes/hermes-agent/`, launcher at `~/.local/bin/hermes`. **CONFIRMED** https://hermes-agent.nousresearch.com/docs/getting-started/installation
- Pinning a version/tag via the installer: not documented. **UNCONFIRMED** (same page)
- Termux: `pkg install hermes-agent` from a signed APT repo. **CONFIRMED** (same page)
- `pyproject.toml`: `name = "hermes-agent"`, `version = "0.0.0"` (placeholder), `requires-python = ">=3.11,<3.15"` (3.14 is "the only version it fully supports"); console scripts `hermes = hermes_cli.main:main`, `hermes-agent = agent.legacy_cli:main`, `hermes-acp = acp_adapter.entry:main`; deps pinned `openai==2.24.0`, `mcp==2.0.0` (in `mcp` extra). **CONFIRMED** https://raw.githubusercontent.com/NousResearch/hermes-agent/main/pyproject.toml
- PyPI `hermes-agent` 0.19.0 (Jul 20 2026, author Nous Research, links to the repo) — exists but is 3 minors behind; docs never mention pip. **CONFIRMED** https://pypi.org/project/hermes-agent/ ; its suitability as an install path is **UNCONFIRMED**.

### A2. Non-interactive / one-shot run
Source: https://hermes-agent.nousresearch.com/docs/reference/cli-commands — all **CONFIRMED** unless noted.
```bash
hermes chat --oneshot -q "Summarize the latest PRs"   # answer and exit
hermes -z "What's the capital of France?"             # final text only, nothing else on stdout/stderr
hermes chat -Q -q "Return only JSON"                  # quiet programmatic mode
hermes chat --oneshot --query-file - < task.txt       # prompt from file/stdin
hermes chat --oneshot -q "..." --format stream-json   # JSONL events (implies --quiet)
```
- `--oneshot` is implied on non-TTY stdio and by `-Q`.
- Exit codes: `hermes -z`: 0 ok / 2 failed-or-partial / 1 no text / 130 interrupted. `chat --oneshot`/`-Q`: 0 ok / 1 failed-partial-or-budget / 130.
- `--format stream-json` events: `system`(subtype `init`: model, session ID), `text`, `tool_use`, `tool_result`, `result` (session ID, `exit_code`, final text, input/output/total/cache tokens, duration, `error`). Every event has a `timestamp` (epoch ms). Diagnostics go to stderr.
- `--usage-file PATH` writes a JSON usage/cost report "even when the run fails".
- Other `chat` flags: `-m/--model`, `--provider`, `--base-url`, `--api-key`, `-t/--toolsets` ("Enable a comma-separated set of toolsets"), `-s/--skills` ("Preload one or more skills"; does not restrict), `--max-turns` (default 500 or `agent.max_turns`), `--ignore-user-config` (ignore config.yaml, still load `.env`), `--ignore-rules` (skip AGENTS.md/SOUL.md/.cursorrules/memory/preloaded skills), `-r/--resume`, `-c/--continue`, `--worktree`, `--yolo`, `--safe-mode`, `--image`, `--source`.
- Legacy runner: `hermes-agent --query "..."` with `--save-trajectories`. **CONFIRMED** (same page)
- Batch: repo root `batch_runner.py` — JSONL `{"prompt": ...}` input, `python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=my_run [--model --base_url --max_turns --num_workers --resume]`, writes `data/<run>/trajectories.jsonl`, `statistics.json`, `checkpoint.json`. **CONFIRMED** https://raw.githubusercontent.com/NousResearch/hermes-agent/main/batch_runner.py
- Also `hermes-acp` (ACP adapter) and `hermes mcp serve` (Hermes as an MCP server). **CONFIRMED** (cli-commands, mcp docs)

### A3. Model endpoint config
- Home dir: `HERMES_HOME` env var → else `~/.hermes` (POSIX) / `%LOCALAPPDATA%\hermes` (Windows). Config `$HERMES_HOME/config.yaml`, secrets `$HERMES_HOME/.env`, skills `$HERMES_HOME/skills`, marker `state.db`. Also `HERMES_DATA_DIR_SUFFIX`, `HERMES_BUNDLED_SKILLS`, `HERMES_OPTIONAL_SKILLS`, `HERMES_OPTIONAL_MCPS`, `HERMES_CONTAINER`. **CONFIRMED** https://raw.githubusercontent.com/NousResearch/hermes-agent/main/hermes_constants.py
- Precedence: CLI args > `config.yaml` > `.env` > defaults. `${VAR}` / `${env:VAR}` substitution in config.yaml. `hermes config set model <provider>/<model>`, `hermes config set terminal.backend docker`; UPPER_SNAKE keys go to `.env`. **CONFIRMED** https://hermes-agent.nousresearch.com/docs/user-guide/configuration
- Example config (repo `cli-config.yaml.example`): `model: {default: "<provider>/<model>", provider: "auto", base_url: "https://openrouter.ai/api/v1"}`; `providers:` entries with `base_url`, `key_env` (or `api_key: ${VAR}`), `extra_headers:`, `session_affinity_header: x-litellm-session-id`; comment says the `custom` provider "accepts any OpenAI-compatible endpoint"; "Output-token limits are provider-owned, not user configuration". `agent: {max_turns: 500, reasoning_effort: "medium"}`. **CONFIRMED** https://raw.githubusercontent.com/NousResearch/hermes-agent/main/cli-config.yaml.example
- Docs: named custom providers accept `api`, `key_env`, `default_model`, `extra_body` (merged into every chat-completions request); `providers.<id>.request_timeout_seconds`, `stale_timeout_seconds`; `model.streaming: false` forces non-streaming; `agent.api_max_retries` (3), `agent.reasoning_effort`, `agent.service_tier`. Env: `HERMES_API_TIMEOUT`, `HERMES_STREAM_READ_TIMEOUT`. **CONFIRMED** (configuration page)
- CLI: `--base-url` = "Explicit OpenAI-compatible endpoint and launch-only key for `--provider custom`"; `--api-key` kept in memory only. **CONFIRMED** (cli-commands)
- `.env` does NOT use `OPENAI_BASE_URL`; `LLM_MODEL` "is no longer read from .env". Provider-specific `*_BASE_URL` vars exist (e.g. `NVIDIA_BASE_URL`, `OLLAMA_BASE_URL`). **CONFIRMED** https://raw.githubusercontent.com/NousResearch/hermes-agent/main/.env.example
- Sampling params: agent init passthrough has `max_tokens`, `reasoning_config`, `service_tier` ("None = provider/model default"); no `temperature`/`seed`/`top_p` config keys found. Request builder passes `model, messages, tools, base_url, timeout, max_tokens, request_overrides, session_id, cache_scope_id, provider_preferences` to a transport layer; temperature goes through `_fixed_temperature_for_model` (legacy path `fixed_temperature`/`omit_temperature`). **CONFIRMED** keys seen in https://raw.githubusercontent.com/NousResearch/hermes-agent/main/agent/agent_init_params.py and https://raw.githubusercontent.com/NousResearch/hermes-agent/main/agent/chat_completion_helpers.py ; the default temperature value actually sent is **UNCONFIRMED** (not located).

### A4. Skills
Source: https://hermes-agent.nousresearch.com/docs/user-guide/features/skills — **CONFIRMED** unless noted.
- Directory: `~/.hermes/skills/` is "the primary directory and source of truth". Bundled skills come from repo `skills/` and are synced in (tracked in `~/.hermes/skills/.bundled_manifest`). Hub-installed and agent-created skills also go there. OpenClaw imports → `~/.hermes/skills/openclaw-imports/`.
- Format: `SKILL.md` with YAML frontmatter: `name`, `description`, `version`, `platforms`, and a `metadata.hermes` block (`tags`, `category`, `fallback_for_toolsets`, `requires_toolsets`, `config`). "Compatible with the agentskills.io open standard." Hermes is listed on agentskills.io's client showcase.
- Loading: progressive disclosure. Skill list (name/description/category) is part of the system prompt; `skills_list()` returns the index, `skill_view(name)` returns full content, `skill_view(name, path)` returns a reference file. "The agent only loads the full skill content when it actually needs it."
- Precedence: project → local → `create_dir` → `external_dirs`; duplicates: "the hidden copy is logged as shadowed". `external_dirs` are read-only; nonexistent paths silently skipped; `~` and `${VAR}` expanded.
- Reload: no rescan interval stated. Mid-conversation changes: "your next message to the agent carries a short note naming the new skills"; system prompt updates "at the next compaction"; `/reload-skills` refreshes slash commands. Config keys: `skills.auto_load`, `skills.guard_agent_created`, `skills.write_approval`, `skills.create_dir`, `skills.external_dirs`, `skills.creation_nudge_interval` (default 15 tool-calling iterations). (configuration page + cli-config example)
- Logging: no dedicated "skill loaded" log documented. Observable via `tool_use` events (`skills_list`/`skill_view`/`skill_manage`) in `--format stream-json`, and via session trajectories ("Session trajectories are automatically saved to logs/ directory"; gateway-only `~/.hermes/logs/tool_calls.log`). Hub installs write `.hub/audit.log`. Exact trajectory log path for CLI runs: **UNCONFIRMED**.
- CLI: `hermes skills browse|search|inspect|install|list|check|update|audit|uninstall|reset|publish|snapshot export|tap add/list/remove`, `hermes skills opt-out/opt-in`, `hermes bundles`.
- `--ignore-rules` skips preloaded skills; `-s/--skills` preloads skills. (cli-commands)

### A5. Self-improvement (skill creation)
- Tool `skill_manage` (toolset `skills`): actions `create` (name, content, optional category), `patch` (name, old_string, new_string; targeted, preferred), `patch` with `content` (full rewrite; `edit` legacy alias), `delete`, `write_file` (name, file_path, file_content), `remove_file`. New skills written to `~/.hermes/skills/` unless `skills.create_dir`; existing skills edited in place (incl. `external_dirs`). **CONFIRMED** (skills page; tool names in https://hermes-agent.nousresearch.com/docs/reference/tools-reference)
- Trigger: "The system prompt asks the agent to record a non-trivial workflow with `skill_manage` for future reuse"; `skills.creation_nudge_interval: 15` nudges every 15 tool-calling iterations; a background review (`agent/background_review.py` exists in repo) "can also suggest or stage skill changes after a session". `skills.guard_agent_created` / `skills.write_approval` gate writes. On-demand: ask the agent in the prompt to create/patch a skill (it calls `skill_manage`). **CONFIRMED** (skills page, cli-config example, repo listing); the exact background-review trigger/config is **UNCONFIRMED**.
- README: "Creates skills after complex tasks, and improves them during use"; also agent-curated memory, FTS5 session search, Honcho user modeling. **CONFIRMED** https://github.com/NousResearch/hermes-agent

### A6. Tools, MCP, restricting toolsets
- Built-in tools by toolset (tools-reference page, **CONFIRMED**): `terminal`: `terminal`, `process_manage`; `file`: `read_file`, `write_file`, `patch`, `search_files`; `skills`: `skills_list`, `skill_view`, `skill_manage`; `memory`: `memory`; `session_search`; `web`: `web_search`, `web_extract`; `x_search`; `browser`: `browser_navigate/click/type/scroll/press/back/snapshot/vision/console/get_images` (+`browser_cdp`, `browser_dialog`); `vision`: `vision_analyze`; `video`; `video_gen`; `image_gen`: `image_generate`; `tts`: `text_to_speech`; `todo`: `todo_list`; `clarify`; `code_execution`: `execute_code`; `delegation`: `delegate_task`; `cronjob`: `cronjob_manage`; `connections`; `catalog`; `kanban_*`; `computer_use`; `desktop_ui`; `discord`, `discord_admin`; feishu/yuanbao platform tools. Platform presets `hermes-cli`, `hermes-telegram`, etc. https://hermes-agent.nousresearch.com/docs/reference/tools-reference
- Terminal backends: `local` (default), `docker`, `ssh`, `singularity`, `modal`, `daytona`, `vercel_sandbox` (`terminal.backend`). **CONFIRMED** https://hermes-agent.nousresearch.com/docs/user-guide/features/tools
- Disable: `agent.disabled_toolsets: [...]` (list of toolsets, applied after per-platform settings); per-platform `platform_toolsets: {cli: [hermes-cli], ...}`; top-level `toolsets` key deprecated; `hermes tools` interactive / `hermes tools --summary`; per-run `hermes chat --toolsets "web,terminal"`. Per-individual-tool disable key: not documented. **CONFIRMED** (configuration, tools-reference, cli-config example)
- MCP client config (`mcp_servers` in config.yaml) **CONFIRMED** https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp :
  ```yaml
  mcp_servers:
    github:                      # stdio
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-github"]
      env: { GITHUB_PERSONAL_ACCESS_TOKEN: "***" }   # only explicit env + safe baseline passed
    remote_api:                  # HTTP (streamable-http by default)
      url: "https://mcp.example.com/mcp"
      headers: { Authorization: "Bearer ***" }
      # transport: sse           # only for SSE-only endpoints (mentioned in troubleshooting)
      timeout: 120               # tool call timeout (default 120s)
      connect_timeout: 60        # default 60s
      enabled: true
      tools: { include: ["*"], exclude: [], prompts: false, resources: false }   # globs; include wins
  ```
  Tools registered as `mcp_<server>_<tool>` (hyphens/dots → underscores) in toolset `mcp-<server>`. Startup log line in `agent.log`: "MCP: registered N tool(s) from M server(s)". `/reload-mcp` in session. CLI: `hermes mcp add|remove|list|test|configure|login|install|catalog|serve`. MCP tools do not replace built-ins (prefix exists "so they do not collide with built-in names").
- MCP dependency: `mcp==2.0.0` in the `mcp` extra. **CONFIRMED** (pyproject)

### A7. Session id / headers / clock
- Agent carries `session_id`; passed to the transport kwargs builder and to the relay layer, and used for Qwen `sessionId` metadata; `prompt_cache_key` is set by the kwargs builder. Only a *configured* `providers.<id>.session_affinity_header` (e.g. `x-litellm-session-id`) sends the session id as an HTTP header; `merge_session_affinity_headers` docstring also mentions OpenCode's `x-opencode-session`. No default `X-Title`/`HTTP-Referer`/`User-Agent` override found. `api_request_hooks.py` adds no headers (only plugin hook payloads). **CONFIRMED** from https://raw.githubusercontent.com/NousResearch/hermes-agent/main/agent/chat_completion_helpers.py and `agent/api_request_hooks.py`; whether the session id appears in the request body by default is **UNCONFIRMED** (not seen).
- Clock: `hermes_time.now()` → `datetime.now()` in `HERMES_TIMEZONE` env or `timezone` config key, else server local; output feeds the system prompt (comments say it must be "byte-stable so the prompt stays cache-friendly"). No `HERMES_FAKE_TIME`-style override. **CONFIRMED** https://raw.githubusercontent.com/NousResearch/hermes-agent/main/hermes_time.py ; exact prompt injection site **UNCONFIRMED**.

### A8. Stars
- **254,142 stars, 55,189 forks** (GitHub API, 2026-10-16 UTC). **CONFIRMED** https://api.github.com/repos/NousResearch/hermes-agent

---

## B. NVIDIA NemoClaw

- What: "open source reference stack for running supported and qualified AI agents more safely inside NVIDIA OpenShell sandboxes" — guided onboarding, managed inference, network policy, managed integrations, lifecycle ops. Repo https://github.com/NVIDIA/NemoClaw (Apache-2.0, ~22.8k stars; description "Run agents like Hermes, LangChain Deep Agents, and OpenClaw more securely inside NVIDIA OpenShell with managed inference"). Docs: https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/home ; index https://docs.nvidia.com/nemoclaw/latest/llms.txt **CONFIRMED**
- Install: `curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash` (default agent OpenClaw). Hermes: `curl -fsSL https://www.nvidia.com/nemoclaw.sh | NEMOCLAW_AGENT=hermes NEMOCLAW_SANDBOX_NAME=my-hermes bash`, or `... | NEMOCLAW_AGENT=hermes bash -s -- --defer-onboarding` then `nemohermes onboard`. `nemohermes` = `nemoclaw` with Hermes preselected. Hermes is labelled **experimental**. Scripted: `NEMOCLAW_AGENT=hermes NEMOCLAW_NON_INTERACTIVE=1 NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 NEMOCLAW_SANDBOX_NAME=my-hermes NVIDIA_INFERENCE_API_KEY=<key>`. Other agent: `NEMOCLAW_AGENT=langchain-deepagents-code`. **CONFIRMED** https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/get-started/quickstart.md
- Commands: `nemohermes onboard [--non-interactive|--resume|--fresh|--name|--from <Dockerfile>|--agent|--tool-disclosure progressive|direct|--control-ui-port|-y]`, `nemohermes <name> status|connect|logs --follow|dashboard-url|rebuild|recover|destroy`, `nemohermes launch <name>`, `nemohermes inference set --model <m> --provider <p>`, `nemoclaw list`, `nemoclaw-acp --sandbox <name>` (ACP over stdio). **CONFIRMED** quickstart + https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/reference/commands.md
- Inference providers: `NEMOCLAW_PROVIDER=build` (NVIDIA endpoints), `routed` (Model Router), `openrouter`, `openai`, `anthropic`, `gemini`, `hermes-provider` (Hermes-only), `custom` (OpenAI-compatible), `anthropicCompatible`, `ollama`, vLLM. **CONFIRMED** (home page)
- **Arbitrary OpenAI-compatible endpoint (e.g. Nebius Token Factory): YES.** 
  ```bash
  NEMOCLAW_TRUSTED_PRIVATE_HOSTS=llm.corp.example NEMOCLAW_PROVIDER=custom \
    NEMOCLAW_ENDPOINT_URL=https://llm.corp.example/v1 NEMOCLAW_MODEL=your-model \
    COMPATIBLE_API_KEY="$COMPATIBLE_API_KEY" nemohermes onboard --non-interactive
  # no-auth loopback: NEMOCLAW_ENDPOINT_URL=http://localhost:8000/v1 NEMOCLAW_COMPATIBLE_AUTH_MODE=none (ports 8000/11434 only; 11435 reserved)
  ```
  Private (RFC1918) hosts blocked unless listed in `NEMOCLAW_TRUSTED_PRIVATE_HOSTS`; sandbox-side alias `host.openshell.internal`. **CONFIRMED** https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/inference/custom-endpoints/set-up-openai-compatible-endpoint.md
- What it sets up: installs OpenShell; onboarding registers inference, builds/starts the sandbox, "writes Hermes configuration into `/sandbox/.hermes`", starts the Hermes gateway inside the sandbox, applies a network-policy tier (Balanced/Restricted/Open). Model traffic: sandbox → `inference.local` → OpenShell gateway L7 proxy (substitutes the real key at egress) → provider; "NemoClaw never gives the sandbox a raw provider key". Hermes baseline policy from `agents/hermes/policy-additions.yaml` (Nous endpoints, PyPI, NVIDIA inference, messaging APIs). Hermes exposes an OpenAI-compatible API on port 8642 (forward it; token via `nemohermes <name> gateway-token --quiet`). **CONFIRMED** quickstart + https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/reference/architecture.md
- Prereqs: Ubuntu 24.04 validated (others best-effort); macOS Apple Silicon via Docker Desktop/Colima; Windows via WSL2; Docker (default) or rootless Podman (`NEMOCLAW_GATEWAY_RUNTIME=podman`); Node.js ≥ 22.19; 4 vCPU/8 GB RAM/20 GB disk min; sandbox image ~2.4 GB. **CONFIRMED** https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/get-started/prerequisites.md
- No documented way to run a single non-interactive Hermes query via `nemohermes`; use `openshell sandbox exec -n <name> -- hermes -z "..."` (composition of CONFIRMED pieces; untested → **UNCONFIRMED**).

---

## C. NVIDIA OpenShell

- What: "the safe, private runtime for autonomous AI agents" — gateway (control plane) + sandboxes with filesystem/network/process policies, credential custody (agents see placeholder tokens; the proxy substitutes real credentials only for approved endpoints), formal policy verification (prover). Repo https://github.com/NVIDIA/OpenShell (Apache-2.0, ~16.4k stars). Docs https://docs.nvidia.com/openshell/latest/ (index: https://docs.nvidia.com/openshell/latest/llms.txt). Current release line 0.1.x; docs default `OPENSHELL_VERSION=v0.1.4`. SDKs: Python `uv add openshell`, TS `@nvidia/openshell-sdk`, Go, Rust. **CONFIRMED**
- Install: `curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh` (pin with `OPENSHELL_VERSION`). macOS: Homebrew service, gateway `https://localhost:17670`; Linux: deb/rpm/snap, gateway as **systemd user service** at `https://127.0.0.1:17670`, config `~/.config/openshell/gateway.toml`; `systemctl --user restart openshell-gateway`; verify `openshell status`. Also "Running the Gateway as a Container" and Helm. **CONFIRMED** https://docs.nvidia.com/openshell/latest/about/installation.md
- Requirements: Linux x86_64/aarch64 (Debian/Ubuntu) supported; macOS Apple Silicon (Docker Desktop); Windows WSL2 experimental. Kernel: **Landlock ABI 3+, Linux ≥ 6.2, Landlock enabled** — applies "including when it runs inside a container or microVM"; seccomp ops must be usable "without added capabilities" or launch "fails closed". Docker Engine/Desktop ≥ 28.0 (Docker Desktop needs host networking, no Enhanced Container Isolation); Podman 5.x rootless (cgroups v2, user socket); MicroVM via KVM/Hypervisor.framework; Kubernetes ≥ 1.29 (CNI must enforce NetworkPolicy); glibc ≥ 2.28. Root: not stated; rootless Podman path exists. Docker-in-Docker/unprivileged-container operation: **not documented → UNCONFIRMED (likely not: needs a container runtime socket + Landlock/seccomp)**. **CONFIRMED** https://docs.nvidia.com/openshell/latest/about/support-matrix.md , https://docs.nvidia.com/openshell/latest/how-it-works/sandboxes/runtimes.md
- Policy format (`version: 1`): static sections `filesystem_policy` (`include_workdir`, `read_only[]`, `read_write[]`; absolute paths, ≤256), `landlock.compatibility: best_effort|hard_requirement`, `process.run_as_user/run_as_group` (non-root) — "locked at sandbox creation. Changing them requires destroying and recreating the sandbox". Dynamic sections `network_policies` and `network_middlewares` — updatable live via `openshell policy update` (merge) or `openshell policy set` (replace). Unknown fields rejected. Each network rule: `endpoints[]` (`host`, `port|ports`, `path`, `allowed_ips`, `protocol: rest|websocket|graphql|mcp|json-rpc|tcp`, `tls: skip`, `enforcement: audit(default)|enforce`, `access: read-only|read-write|full`, `rules`/`deny_rules` with `method`/`path`/`query`) + `binaries[]` (`path` glob; hash-pinned; empty list allows nothing; rule applies to listed binaries and their children). Default: "denies every outbound connection ... unless a rule allows it"; default policy has no network rules → all egress denied; FS: "Any path not listed in either list is inaccessible". Egress enforced by the sandbox network proxy; DNS for allowed hosts answered with placeholder IPs (TTL ≤ 30 s). Default FS baseline: RO `/bin /usr /lib /proc /dev/urandom /etc /var/log`, RW workdir, `/tmp`, `/dev/null`. **CONFIRMED** https://docs.nvidia.com/openshell/latest/how-it-works/policies/schema.md , .../policies/overview.md , .../policies/network-rules.md , .../policies/default-policy.md , https://docs.nvidia.com/openshell/v0.0.116/sandboxes/policies.md
- Exact CLI (all **CONFIRMED** from https://docs.nvidia.com/openshell/latest/how-it-works/sandboxes/overview.md and .../policies/manage-policies.md):
  ```bash
  # policy.yaml — deny-all except one host, only curl/python may reach it
  cat > policy.yaml <<'EOF'
  version: 1
  filesystem_policy: { include_workdir: true, read_only: [/usr, /lib, /bin, /etc], read_write: [/tmp] }
  landlock: { compatibility: best_effort }
  network_policies:
    ratchet_proxy:
      endpoints:
        - host: api.example.com
          port: 443
          protocol: rest
          enforcement: enforce
          access: full
      binaries:
        - path: /usr/bin/curl
        - path: /usr/bin/python3*
  EOF
  openshell sandbox create --name demo --policy ./policy.yaml --from <image> --env FOO=bar -- sleep infinity
  # (or export OPENSHELL_SANDBOX_POLICY=$PWD/policy.yaml)
  openshell sandbox exec -n demo --timeout 600 -- hermes -z "task"      # one command, non-interactive; stdin piped
  echo hi | openshell sandbox exec -n demo -- cat
  openshell sandbox get demo --policy-only ; openshell policy get demo --full
  openshell policy set demo --policy ./policy.yaml --wait                 # replace dynamic sections (exit 0/1/124)
  openshell policy update demo --rule-name x --binary /usr/bin/curl --add-endpoint api.github.com:443:read-only:rest:enforce --wait
  openshell logs demo --tail ; openshell forward start 8000 demo
  openshell sandbox stop demo ; openshell sandbox delete demo             # (no "destroy")
  ```
  Other create flags: `--detach`, `--label`, `--forward <port>`, `--upload src:dst`, `--no-keep`, `--provider <name>`. Providers: `openshell profile import --url <yaml>`; `openshell provider create --name x --type <profile> --credential KEY=val|--from-existing`; "Provider creation rejects profileless types" — an arbitrary OpenAI-compatible host needs its own imported profile YAML. Access requests: `openshell rule get|approve|reject|approve-all`. **CONFIRMED** https://docs.nvidia.com/openshell/latest/how-it-works/providers/overview.md , .../about/run-your-first-agent.md
- Whether plain HTTP (port 80) endpoints are allowed: **UNCONFIRMED** (all examples use 443/TLS).

---

## D. agentskills.io / SKILL.md spec

Source: https://agentskills.io/specification — **CONFIRMED**.
- Layout: `skill-name/SKILL.md` (required) + optional `scripts/`, `references/`, `assets/`, any other files.
- Frontmatter: required `name` (1–64 chars, `a-z0-9-`, no leading/trailing/consecutive hyphens, **must match the parent directory name**), `description` (1–1024 chars, what + when). Optional: `license`, `compatibility` (≤500 chars), `metadata` (string→string map; clients stash extras here, e.g. Hermes `metadata.hermes`, OpenClaw `metadata.openclaw`), `allowed-tools` (space-separated, experimental). No `version` field in the spec (put it under `metadata`).
- Body: free Markdown; keep SKILL.md < 500 lines; reference files one level deep via relative paths.
- Progressive disclosure: (1) name+description (~100 tokens) loaded at startup for all skills; (2) full SKILL.md body (<5000 tokens recommended) on activation; (3) resources on demand.
- Validator: `skills-ref validate ./my-skill` (https://github.com/agentskills/agentskills/tree/main/skills-ref).
- Clients listed on https://agentskills.io/clients.md include: **Hermes Agent, OpenClaw, ChatGPT & Codex, Cursor, Gemini CLI, GitHub Copilot, VS Code, OpenCode, OpenHands, Goose, Amp, Letta, Roo Code, Kiro, Factory, pi, Junie, ZeroClaw, nanobot, fast-agent, Mistral Vibe, Spring AI, Snowflake Cortex Code, Databricks Genie Code, Tabnine**, and others. **CONFIRMED**

---

## E. OpenClaw

OpenClaw (https://github.com/openclaw/openclaw, TypeScript, MIT, ~392k stars; "The AI that really does things") is an open-source personal AI assistant that runs locally and connects to messaging platforms; Hermes positions itself as its successor and ships `hermes claw migrate`. OpenClaw "follows the AgentSkills spec": each skill is a directory with `SKILL.md` (required `name`, `description`; optional `homepage`, `user-invocable`, `disable-model-invocation`, `command-dispatch`, `command-tool`, `command-arg-mode`; gating under `metadata.openclaw` with `requires.bins/anyBins/env/config`, `os`, `always`, `install`). Skill dirs, highest precedence first: `<workspace>/skills`, `<workspace>/.agents/skills`, `~/.agents/skills`, `~/.openclaw/skills` (managed; `openclaw skills install --global`), `<state-dir>/agents/<id>/agent/workshop-skills`, bundled, `skills.load.extraDirs`, plugins. Registry: ClawHub (`openclaw skills install @owner/<slug>`). Core SKILL.md files are interchangeable with Hermes (same spec; Hermes imports them into `~/.hermes/skills/openclaw-imports/`). **CONFIRMED** https://docs.openclaw.ai/tools/skills , https://api.github.com/repos/openclaw/openclaw , Hermes README.

---

## F. MCP Python SDK

- PyPI `mcp` **2.3.0** (released 2026-10-02), Python ≥ 3.10. Install `pip install "mcp[cli]"` / `uv add "mcp[cli]"`. **CONFIRMED** https://pypi.org/project/mcp/ , https://github.com/modelcontextprotocol/python-sdk
- **v2 renamed `FastMCP` → `MCPServer`** (`mcp.server.fastmcp.*` → `mcp.server.mcpserver.*`; old import raises `ModuleNotFoundError`). Transport params moved from constructor to `run()`. Hermes pins `mcp==2.0.0`, so write against v2. **CONFIRMED** https://py.sdk.modelcontextprotocol.io/migration/
- Minimal server (official docs, **CONFIRMED** https://py.sdk.modelcontextprotocol.io/ and .../run/):
  ```python
  from mcp.server import MCPServer

  mcp = MCPServer("ratchet-mock")


  @mcp.tool()
  def send_email(to: str, subject: str, body: str) -> dict:
      """Mock: records the call and returns a canned result."""
      return {"ok": True, "id": "mock-1"}


  if __name__ == "__main__":
      import sys

      if "--http" in sys.argv:
          mcp.run(
              transport="streamable-http", host="127.0.0.1", port=3001
          )  # endpoint http://127.0.0.1:3001/mcp
          # also: streamable_http_path="/mcp", json_response=True, stateless_http=True
      else:
          mcp.run()  # stdio (default transport); blocks for the life of the server
  ```
  CLI alternatives: `uv run mcp run server.py --transport streamable-http`, `uv run mcp dev server.py` (Inspector).
- Mount in an existing ASGI app (**CONFIRMED** https://py.sdk.modelcontextprotocol.io/run/asgi/): `app = Starlette(routes=[Mount("/", app=mcp.streamable_http_app())], lifespan=lifespan)` where `lifespan` does `async with mcp.session_manager.run(): yield`; run `uvicorn server:app` → `/mcp`.
- Client: `from mcp import Client; async with Client("http://localhost:8000/mcp") as c: r = await c.call_tool("add", {"a":1,"b":2}); r.structured_content`. Tool errors raise `MCPError` in v2. **CONFIRMED** (README, migration guide)

---

## Still unverified — must test on a machine with Hermes installed

1. Whether `hermes chat --oneshot --format stream-json` works with `--provider custom --base-url http://127.0.0.1:PORT/v1` pointing at a non-TLS local proxy, and which request fields (temperature, seed, `prompt_cache_key`, `session_id`, `user`) actually arrive at the proxy. Default temperature value not located in source.
2. Whether `-t/--toolsets` *replaces* or *adds to* the default toolset, and whether `mcp-<server>` can be named in `-t`.
3. Exact on-disk path/format of CLI session trajectories ("logs/ directory") and `agent.log` under `$HERMES_HOME`; whether the "MCP: registered N tool(s)" line is emitted in one-shot mode.
4. Whether skills edited on disk between two `--oneshot` processes are picked up with no cache (expected yes: index rebuilt at session start; `.bundled_manifest` sync behaviour on a custom `HERMES_HOME` is untested).
5. Whether `HERMES_HOME` fully redirects `skills/`, `state.db`, logs, and the bundled-skills sync on first run (fresh home may trigger setup prompts — test with `--non-interactive`/`--ignore-user-config`).
6. Whether the automatic skill-creation nudge fires reliably within `--max-turns` for short tasks, and whether `skills.write_approval` blocks `skill_manage` in non-interactive mode (may need `--yolo` or `skills.write_approval: false`).
7. Pinning Hermes to a tag via `install.sh` (undocumented); fallback is `git -C ~/.hermes/hermes-agent checkout v0.22.2` + re-sync — untested.
8. Whether the PyPI `hermes-agent` wheel is a usable install path at all (3 minors stale).
9. OpenShell: running the gateway + Docker sandbox inside an unprivileged CI container / Docker-in-Docker (docs silent); plain-HTTP (port 80) egress rules; `openshell sandbox exec` exit-code propagation and stdout capture limits.
10. NemoClaw: whether `inference.local` accepts an OpenAI-compatible URL with a non-standard path (e.g. Nebius Token Factory `https://api.studio.nebius.com/v1`), and how `NEMOCLAW_TRUSTED_PRIVATE_HOSTS` interacts with a Ratchet proxy on the host.
11. Hermes behaviour when the model returns tool calls for disabled toolsets, and whether `agent.disabled_toolsets` is honoured by `chat --oneshot` with `--ignore-user-config` (which drops config.yaml entirely — so pass config via `HERMES_HOME` instead).
