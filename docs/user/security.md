# Security and privacy

Ratchet's job is to keep a self-modifying agent from quietly getting worse, so it has to be
something a careful person would run next to their secrets. This page summarizes the threat
model, the hardening after the 2026-10-16 security review (ADR 0022),
and the limits that remain. It is scoped to v0.1: one person's agent, and one public demo
host.

## What Ratchet protects

| Asset | Where it lives | Sensitivity |
| --- | --- | --- |
| Token Factory and Tavily keys | the proxy's environment on the control plane; the sidecar proxy in workers | critical |
| Golden traces (task inputs, tool results, outputs) | `data/traces/` | high: may hold personal or business data |
| Cassettes | `data/cassettes/` | high: tool responses from real services |
| PII token map | `data/pii.map.enc`, with its key outside the data directory | critical |
| Skills repo | `skills/` (git) | medium: the agent's learned behavior |
| Verdicts and summaries | `data/verdicts/` | medium |
| Demo action token | page config on the public host | low; rotates daily |

## Threats and controls

| # | Threat | Control |
| --- | --- | --- |
| T1 | A replay performs a real side effect | Every side-effecting tool goes to the mock tool server. A hard-class call with no matching cassette is refused with `409 ratchet_blocked`. Sandbox egress is limited to the proxy and the mock tools |
| T2 | Keys leak out of the sandbox | The sandbox environment holds a dummy key. The real key exists only in the sidecar proxy, outside the sandbox. Ratchet refuses to start a sandbox whose environment contains a secret, and traces never store headers |
| T3 | Prompt injection in skills, tool results or traces steers a judge | Untrusted text is wrapped in nonce-tagged delimiters and treated as data; only one JSON object is parsed from a reply; judges cite step ids; the agreement rules need two judges to block; Level 0 can't be talked out of its answer |
| T4 | Prompt injection makes the replayed agent exfiltrate data | `external_send` calls are blocked by the mock tool server, and egress is denied. The attempt is recorded as an unexpected side effect and surfaces as a BLOCK |
| T5 | PII persists in traces or cassettes | A PII pass at record and bless time replaces emails, phone numbers and secrets with tokens. The token map is encrypted (Fernet), with the key outside the data directory |
| T6 | A malicious skill escapes the sandbox | OpenShell's Landlock policy (production path) or Docker with no privileges: `--cap-drop ALL`, read-only root, no Docker socket. `shell.run` is a read-only reimplementation of six commands in Python and never starts a process |
| T7 | The public demo is abused | Per-session scenario copies, the daily action token, rate limits, Simulate queue caps, SSE stream caps, 64 KiB body limits, auto-revert, and no bless or record (`RATCHET_PUBLIC_MODE=true`) |
| T8 | Git actions corrupt the harness's skills | Worktree isolation, `--ff-only` merges, reverts as new commits (history is never rewritten), and the stacking guard (ADR 0021) |
| T9 | The budget drains | A daily cap, per-purpose caps and a bench hard cap, checked before every forwarded call. Workers get a dispatch-time allowance enforced by their sidecar |
| T10 | Supply chain | `uv.lock` is pinned, and images are built in CI from the lockfile |

### Hardening from the 2026-10-16 review (ADR 0022)

- **Read-only shell in Python.** `shell.run` supports `ls`, `cat`, `head`, `tail`, `wc` and
  `grep`, each with an allowlist of short flags. Every other argument must be a path inside
  the workspace. `sort`, `uniq`, `diff`, `find` and `cut` are gone, because option tricks such
  as `sort --compress-program=sh` could run code.
- **The sandbox is never trusted for policy.** The worker's sidecar proxy forces the `agent`
  purpose and the run's tier, enforces the spend allowance, and keeps its admin routes behind
  a per-golden token the sandbox never sees.
- **Hard side effects match cassettes exactly.** A cosmetic rewording of a hard-class call,
  such as a `github.comment` body, now counts as an unexpected side effect. This is the safe
  direction.
- **Stacked merges can't ship reverted work.** A reverted ancestor counts as resolved only
  once its revert lands with the merge.
- **A bounded public surface.** Simulate has a queue cap and one active run per client IP. SSE
  has per-IP and global stream caps. Request bodies are capped at 64 KiB in Caddy and in the
  app. Public mode refuses the default action-token secret.

## Data handling and retention

- Traces and cassettes are kept until you delete them. `ratchet purge --before <date>`
  deletes live and replay traces older than the date, and goldens only with
  `--include-golden`.
- Logs never contain content, arguments or secrets. The `RATCHET_DEBUG` content log is a local
  file, kept out of git and out of the image.
- The public demo host holds only the seeded demo agent's data. Its org, repos and mailbox are
  fictional, and its cassettes are synthesized.
- Backups leave out the PII key and `/etc/ratchet/env`.

## What Ratchet does not protect

!!! warning "Out of scope for v0.1"
    - **Multi-tenant isolation**, **authentication and roles** for the dashboard. A local
      install trusts anyone who can reach port 8080; keep it bound to `127.0.0.1` (the
      default) or behind your own authenticating proxy.
    - **Encryption at rest** beyond the PII token map, and **signed verdicts**.
    - **A security review of Hermes** itself.
    - **Skill security scanning.** Ratchet catches regressions, not malicious skills (NVIDIA
      SkillSpector covers that).
    - **Rollback as a guarantee.** A rollback is a git revert. It does not undo anything an
      agent did on `main` before Ratchet saw the edit.

### Known, accepted limitations

| Area | Limitation | What limits it |
| --- | --- | --- |
| The `process` sandbox | No isolation at all (ADR 0017) | Refused in public mode and with the Hermes harness. Development only |
| Rate limits and stream caps | In memory, per `ratchet serve` process, so a restart resets them. Keyed by client IP, which is only correct behind Caddy with `RATCHET_FORWARDED_ALLOW_IPS` set | One process per host. The daily USD cap lives in SQLite and survives restarts |
| Sidecar `/tools/*/notify` | No admin token: it is the harness's own channel from inside the sandbox | Accepts only local read tools, and only for the one run that sidecar serves |
| Dispatch allowance | Edits dispatched at the same moment each see the same remaining allowance | At most `RATCHET_MAX_EDITS_IN_FLIGHT` (2) edits overlap, and the control-plane proxy still enforces the daily cap on every judge call |
| Secret redaction in streams | A secret split across separate SSE content deltas is not caught | Redaction also runs on the recorded step and the cache. The upstream never sees sandbox secrets |
| Public trace visibility | A new verdict's traces become visible within about 1 s, cached up to 30 s per session | Bounded to 512 sessions |
| `shell.run` | Basic regular expressions only in `grep` | Defence in depth; the sandbox is the boundary |
| Hermes toolsets | The toolset allowlist is not yet verified against a live Hermes with a live model | Hermes is off by default, and refused with the `process` sandbox |
| OpenShell | Not yet verified live (needs a Linux VM with Landlock) | Docker is the verified sandbox |

## Demo analytics

The public demo records an anonymous funnel, so the team can tell whether visitors reach a
verdict (ADR 0016). It is first-party: no analytics vendor and no third-party
scripts.

| Step | Event | Stored fields |
| --- | --- | --- |
| 1 | `demo.landing_view` | session hash, device class, referrer host (devpost / youtube / github / other / none) |
| 2 | `demo.simulate_click` | scenario |
| 3 | `demo.verdict_view` | scenario, verdict id, seconds since the click |
| 4 | `demo.evidence_open` | which: pair, diff, drift, cost or trail |
| 5 | `demo.action_click` | which: rollback, approve or rebless |
| 6 | `demo.repo_click`, `demo.video_click` | — |

The privacy rules, as implemented in `POST /api/v1/demo/events`:

- **No IP addresses and no user agents.** The client sends only a device class. The IP is
  used for a rate-limit bucket in memory and is never stored.
- **No cross-session identifier.** The session hash is a random value generated in the
  browser and kept in `sessionStorage`. It is not the cookie, and no cookie outlives the
  session key.
- **Only listed fields.** Any field not in the table above is rejected with `422`.
- **30-day retention.** Rows live in a separate `demo_events` table and are pruned after 30
  days.
- **Public host only.** On a local install the endpoint answers 404, and nothing is recorded.
- **Aggregates only** in any public write-up ("N judges reached a verdict"), never per-session
  data.

The footer says so in plain words: "This demo records anonymous page and button events to make
sure it works for judges. No personal data is collected."
