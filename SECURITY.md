# Security policy

Ratchet runs next to an agent's keys, traces and tool results, and it decides what reaches
the agent's skills. We treat reports about it seriously and answer them quickly.

## Reporting a vulnerability

**Do not open a public issue, discussion or pull request for a security problem.**

Report it privately through GitHub's private vulnerability reporting:

1. Go to the repository's **Security** tab: <https://github.com/lloydjf/ratchet/security>.
2. Choose **Report a vulnerability** and fill in the advisory form.

Please include:

- the version or commit you tested (`uv run python -c "import ratchet; print(ratchet.__version__)"`
  and `git rev-parse HEAD`);
- how Ratchet was running: local, `docker compose`, or a VM install; the sandbox (process,
  Docker or OpenShell), the harness (MinimalRunner or Hermes), and whether it used the
  offline simulator or live models;
- the steps to reproduce, and what an attacker gains;
- any proof of concept. Redact real keys, traces and personal data before you attach
  anything.

If you can't use GitHub's form, open an issue that says only "security contact requested",
with no details, and a maintainer will reach out.

## What to expect

| Step | Target |
| --- | --- |
| Acknowledgement of your report | within 24 hours |
| First assessment (confirmed or not, severity, affected versions) | within 3 days |
| Fix | a `0.1.x` patch release; the timeline depends on severity, and we keep you informed |
| Disclosure | a published GitHub security advisory, after the fix is released |

We credit reporters in the advisory and the [changelog](CHANGELOG.md) unless you ask us not
to. Please give us the chance to ship the fix before you disclose publicly.

## Supported versions

| Version | Supported |
| --- | --- |
| `0.1.x` | yes |
| older | no |

## Scope

### What Ratchet holds, and where

Ratchet is local-first. Everything it records stays on the machine that runs it:

- **Traces** (task inputs, model calls, tool results, outputs) in `data/traces/`.
- **Cassettes** (recorded tool responses) in `data/cassettes/`.
- **The PII token map** in `data/pii.map.enc`, encrypted, with its key kept outside the data
  directory. Emails, phone numbers and secrets are tokenized before a trace or cassette is
  written.
- **The skills repo** and **verdicts**.

Model calls go to Token Factory and drift checks to Tavily, with your keys, from the capture
proxy. Keys are never written to traces, cassettes, logs or git, and the sandbox only ever
sees a dummy key. Ratchet sends no telemetry from a local install.

### The public demo host

The public demo host runs the seeded demo agent only. It **accepts no keys**: visitors
never enter a Token Factory or Tavily key, and there is no way to upload one. Its org,
repos and mailbox are fictional, its cassettes are synthesized, and bless and record are
turned off. It records an anonymous first-party funnel; see
[Security and privacy](docs/user/security.md#demo-analytics).

### In scope

- A replay taking a real side effect, or reaching the network beyond the proxy and the mock
  tool server.
- A key or other secret reaching a sandbox, trace, cassette, log, image or git.
- PII persisted without tokenization, or the token map readable without its key.
- A sandbox escape from a skill or a replayed agent (Docker or OpenShell sandbox).
- Prompt injection that makes a judge pass a regression the agreement rules should hold or
  block.
- Bypassing the public demo host's limits: action tokens, rate limits, Simulate queue caps,
  body limits, or another session's data.
- A git action that ships held or reverted work to `main`, or rewrites history.

### Out of scope

These are documented limitations of v0.1, not vulnerabilities (see
[Security and privacy](docs/user/security.md#what-ratchet-does-not-protect)):

- The `process` sandbox, which has no isolation by design and is for development only.
- Anyone who can reach a local install's dashboard port: there is no authentication. Keep
  it bound to `127.0.0.1` (the default) or behind your own authenticating proxy.
- Malicious skills in general. Ratchet catches regressions, not malware.
- Vulnerabilities in Hermes Agent, OpenShell, Token Factory or Tavily themselves. Report
  those upstream; tell us too if Ratchet makes them worse.
- Spend within the caps you configured.
