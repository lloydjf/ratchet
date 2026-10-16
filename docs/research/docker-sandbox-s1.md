# Docker sandbox egress under Linux semantics

Run on 2026-10-16 from a macOS dev host. Linux behaviour was reproduced inside the OrbStack
Linux VM by running the `control-docker` image with `--network host` and the Docker socket.
This is the same topology `RatchetLocalRunner` uses for the worker container
(`ratchet/replay/jobs/local.py`: `--network host` + socket; sandboxes are sibling containers).
No live model calls were made (`RATCHET_UPSTREAM=sim RATCHET_TAVILY_MODE=fixtures`), and every
container started for this check was removed afterwards.

**Result.** The sandbox egress test (`tests/security/test_sandbox_egress.py`) passes under Linux
semantics. Inside the sandbox, the gateway sidecar is
reachable, and there is no DNS and no route to any public IP. There are two caveats for the
Nebius VM run:

- ICC isolation is **not** enforced under OrbStack.
- Host services listening on `0.0.0.0` can be reached through the gateway.

On the macOS host itself, the egress test cannot pass, because `172.30.0.1` is not a host
interface there. The test now skips there with a clear reason, and the sandbox raises an
actionable error (see "Code changes" below).

## Environment

| Item | Value |
| --- | --- |
| Host | macOS 27.0.1, OrbStack 2.2.3 |
| Docker | client/server 29.4.0, linux/arm64. VM kernel `7.0.14-orbstack-00380-ga7e0a2dc9535`, cgroup v2 |
| Network | `ratchet-sandbox`: bridge, `Internal=true`, 172.30.0.0/24, gateway 172.30.0.1, `enable_icc=false`. Kernel device `br-aacf03334622` |
| Images | `ratchet-worker:dev` (afc4c125a488) and `ratchet-control-docker:dev` (ffbf3ec360e1, built from `deploy/control.Dockerfile --target control-docker`) |
| Test deps | `pytest 9.1.1`, `pytest-asyncio 1.4.0`, `respx 0.23.1`. Installed with `pip --target` into a scratch dir on `PYTHONPATH`, after removing packages the image venv already provides |

## Commands

```bash
docker build -f deploy/control.Dockerfile --target control-docker -t ratchet-control-docker:dev .

# Is the gateway bindable in the VM (host) network namespace?
docker run --rm --network host --entrypoint python ratchet-control-docker:dev \
  -c 'import socket; s=socket.socket(); s.bind(("172.30.0.1",0)); print(s.getsockname())'
# -> ('172.30.0.1', 42983)

# The egress test with Linux semantics. Repo and scratch dirs are mounted at the same absolute paths,
# so the --mount src=... paths the sandbox passes to the daemon resolve (as in the
# docker-runner compose). The socket is root:root 0660 in OrbStack, hence --group-add 0.
# zsh note: write "${R}:${R}:ro". In zsh, "$R:$R:ro" expands ":r" as a modifier and mounts
# ".../ratcheto".
R=<repo>; S=<scratch>
docker run --rm --name ratchet-s1-control --network host --group-add 0 \
  -v /var/run/docker.sock:/var/run/docker.sock -v "${R}:${R}:ro" -v "${S}:${S}" -w "$R" \
  -e PYTHONPATH=$S/deps -e TMPDIR=$S/tmp -e HOME=$S/tmp \
  -e RATCHET_UPSTREAM=sim -e RATCHET_TAVILY_MODE=fixtures \
  --entrypoint python ratchet-control-docker:dev \
  -m pytest -m docker tests/security/test_sandbox_egress.py -v -p no:cacheprovider \
  --basetemp=$S/tmp/pt -rA
```

Manual probes (check 2): a probe script starts four containers:

- a sidecar on `172.30.0.1:18080` (`--network host`)
- a `0.0.0.0:18081` host listener
- a `127.0.0.1:18082` host listener
- a peer sandbox on `ratchet-sandbox:8000`

It then runs `curlimages/curl:8.11.1` and `ratchet-worker:dev` on `ratchet-sandbox` with the
same flags as `run_argv`: `--read-only --cap-drop ALL --security-opt no-new-privileges
--pids-limit 256 --memory 2g --cpus 2 --user 10001:10001 --tmpfs /tmp:noexec…`. The worker
image has no `curl`, so the curl image stands in for curl. The worker image's Python is used
for the DNS checks.

## Results

### Check 1: the sandbox egress test

| Where | Result |
| --- | --- |
| macOS host, `uv run pytest -m docker tests/security/test_sandbox_egress.py` | **FAIL**: `OSError: [Errno 49] Can't assign requested address` → `SandboxError: could not bind the sidecar sidecar` |
| `control-docker`, `--network host`, current code | **PASS**: `tests/security/test_sandbox_egress.py::test_docker_sandbox_blocks_internet_but_reaches_sidecar PASSED`, `1 passed in 1.27s` |
| `control-docker`, `--network host`, after the code changes | **PASS**, `1 passed in 2.06s` |
| `control-docker` **without** `--network host`, after the code changes (negative case) | Fails fast, as intended: `SandboxError: the ratchet-sandbox bridge gateway 172.30.0.1 is not an interface in this network namespace; run the worker with --network host (deploy/docker-compose.docker-runner.yml)` |
| macOS host, after the code changes | **SKIPPED**, with this reason: `the sandbox egress test needs a Linux host: the ratchet-sandbox bridge gateway (172.30.0.1) must be a host interface, which it is not under Docker Desktop/OrbStack on macOS; run it in the control-docker container with --network host (docs/research/docker-sandbox-s1.md)` |

### Check 2: manual egress probes from a container on `ratchet-sandbox`

| Probe | Expected | Got | Verdict | Evidence |
| --- | --- | --- | --- | --- |
| `curl http://172.30.0.1:18080/` (gateway sidecar) | ok | 200 | PASS | |
| `curl https://example.com/` | fail | fail | PASS | `curl: (28) Resolving timed out` |
| `curl --resolve example.com:443:172.66.147.243 https://example.com/` (raw public IP) | fail | fail | PASS | `curl: (7) Could not connect`, 0 ms (no route) |
| `curl http://1.1.1.1/`, `curl http://8.8.8.8:53/` | fail | fail | PASS | `curl: (7)`, 0 ms |
| `curl http://192.168.215.1:18081/` (default-bridge gateway, i.e. the host on another interface) | fail | fail | PASS | `curl: (7)` |
| `curl http://172.30.0.1:18082/` (host listener on 127.0.0.1) | fail | fail | PASS | `curl: (7)` |
| `getaddrinfo("example.com")`, `getaddrinfo("pypi.org")` | fail | fail | PASS | `gaierror -3 Temporary failure in name resolution` |
| UDP DNS query to 1.1.1.1:53 and 8.8.8.8:53 | fail | fail | PASS | `ENETUNREACH`. The only route is `172.30.0.0/24 dev eth0`; there is no default route |
| UDP DNS query to 172.30.0.1:53 | fail | fail | PASS | timeout |
| UDP DNS query to 127.0.0.11 (Docker embedded DNS) | (answers, but no forwarding) | answers | note | Resolves container names only (`ratchet-s1-peer` → 172.30.0.2). External names fail |
| `curl http://172.30.0.2:8000/` (peer sandbox, ICC off) | fail | **200** | **FAIL** | see F1 |
| `curl http://ratchet-s1-peer:8000/` (peer by name) | fail | **200** | **FAIL** | see F1 |
| `curl http://172.30.0.1:18081/` (host listener on **0.0.0.0**) | fail | **200** | **FAIL** | see F2 |

### Findings

**F1. ICC off is not enforced under OrbStack.** Docker installs the isolation rule
`-A DOCKER-FORWARD -i br-aacf03334622 -o br-aacf03334622 -j DROP`, and
`net.bridge.bridge-nf-call-iptables = 1` is set. Even so, peer-to-peer traffic gets through,
and the DROP rule's counter stays at **0 packets**. Intra-bridge frames in the OrbStack kernel
never reach the `ip filter FORWARD` hook: `/sys/module/br_netfilter` is absent, and the
per-bridge `nf_call_iptables` is 0. Setting the per-bridge flag to 1 did not change the result,
and the flag was restored to 0 afterwards.

On stock Linux with `br_netfilter` loaded, Docker enforces this rule. That is still unverified:
**re-run the peer probe on the Nebius VM**. If it also passes there, run
`modprobe br_netfilter` from `deploy/sandbox-network.sh`/bootstrap. As a fallback, run one
sandbox network per replay.

**F2. The gateway is the host.** An `--internal` bridge blocks forwarding, but not INPUT to the
host's own address on that bridge. Any host service listening on `0.0.0.0` can therefore be
reached from the sandbox at `172.30.0.1:<port>`. On the VM, the Ratchet services already bind
127.0.0.1 (`deploy/vm/env.template`: dashboard 8080, proxy 8081, mock 8082), and the probe
confirmed 127.0.0.1 listeners are unreachable. sshd (22) and Caddy (80/443) usually bind
`0.0.0.0`, though.

Mitigation (sandbox escape and lateral movement):

1. Create the bridge with a stable name: `--opt com.docker.network.bridge.name=ratchet-sbx0`.
2. Add a host nft/iptables INPUT rule that drops everything arriving on `ratchet-sbx0`,
   except TCP to `172.30.0.1` in the ephemeral range the sidecars bind (`32768-60999`). An
   example: `iptables -I INPUT -i ratchet-sbx0 -p tcp --dport 1:32767 -j DROP` plus
   `-p udp -j DROP`.
3. Extend the sandbox egress test with a "gateway port 22 unreachable" probe.

**F3. Ordering bug in `replay_one._start`.** The sidecars bind `sandbox.bind_host` *before*
`sandbox.create()`, and `create()` is what calls `ensure_network()`. On a fresh Linux host where
`deploy/sandbox-network.sh` has not run, the bind fails before the self-healing network
creation can happen. The fix adds `DockerSandbox.preflight()` (ensure network, then check that
the gateway is bindable) and calls it before the sidecars bind.

**F4. Wording.** With the default name, the existing error message reads "could not bind the
sidecar sidecar".

## macOS dev-host behaviour

- **The egress test skips on non-Linux** (`skipif(sys.platform != "linux")`), with a reason that points
  here. On Linux, an unbindable gateway is a **failure**, not a skip: the test calls
  `sandbox.preflight()`. A misconfigured worker (no `--network host`) then fails loudly instead
  of silently passing CI.
- **`DockerSandbox.preflight()`** raises `SandboxError` with
  `"the docker sandbox needs a Linux host (the bridge gateway must be a host interface); on macOS use the docker-runner compose profile or RATCHET_SANDBOX=process for development"`
  and `context={"gateway", "network", "platform"}` when the platform is not Linux. On Linux it
  points to `--network host`.
- **`ServerThread`** takes an optional `hint=`. On `EADDRNOTAVAIL` it appends the hint, so the
  error reads: `could not bind sidecar 'proxy' on 172.30.0.1: Can't assign requested address; the docker sandbox needs a Linux host …`.
- **For Docker-sandbox work on a Mac**, run it inside `control-docker` with `--network host`
  (commands above, or the docker-runner compose profile). For day-to-day development, use
  `RATCHET_SANDBOX=process`.

Checks after the change:

- `ruff check`: clean
- `ruff format --check`: clean
- `mypy`: clean
- `tests/unit` + `tests/integration`: `1058 passed`
- the sandbox egress test: skipped on macOS, passed in `control-docker`

## Code changes

These changes are in the code:

- `ratchet/replay/sandbox/docker.py`: `gateway_bindable()`, `gateway_hint()` and
  `DockerSandbox.preflight()` (ensure the network, then check the gateway is bindable; raise
  `SandboxError` with `LINUX_HOST_REQUIRED` on non-Linux, or a `--network host` hint on Linux).
  `bindable=` is injectable, so the fake-runner unit tests in `tests/unit/test_sandbox.py` stay
  host-independent.
- `ratchet/replay/replay_one.py`: calls `preflight()` (via `getattr`, so the `Sandbox`
  protocol does not change and `ProcessSandbox`/`OpenShellSandbox` need none) before the
  sidecars bind, fixing F3, and passes the sandbox's `bind_hint` to both sidecars.
- `ratchet/replay/sidecars.py`: `ServerThread(..., hint=)`; on `EADDRNOTAVAIL` the error names
  the sidecar and host and appends the hint, fixing F4.
- `tests/security/test_sandbox_egress.py`: skips on non-Linux with the reason quoted above,
  and calls `sandbox.preflight()` so an unbindable gateway on Linux is a failure.

Still open:

- a `-m docker` test for ICC (F1)
- a test for gateway ports other than the sidecar's (F2)
- `modprobe br_netfilter` and the INPUT rule in `deploy/sandbox-network.sh`/VM bootstrap

## Summary

| Item | Finding | State |
| --- | --- | --- |
| Docker sandbox egress | From a `--read-only --cap-drop ALL` container on the internal `ratchet-sandbox` bridge: the gateway sidecar (172.30.0.1) is reachable; DNS, public IPs (1.1.1.1, 8.8.8.8, example.com by IP), the default-bridge gateway and host 127.0.0.1 listeners are not. The egress test passed in `ratchet-control-docker` with `--network host` + the Docker socket. On a macOS host it cannot bind 172.30.0.1 (the gateway lives in the Docker VM) and is skipped | **Verified (Linux semantics in the OrbStack VM)** 2026-10-16; re-run on the Nebius VM |
| ICC off between sandboxes | Docker installs the `-i br -o br -j DROP` rule, but under OrbStack intra-bridge traffic bypasses `ip filter FORWARD` (0 packet hits; no `br_netfilter` module): one sandbox reached a peer at 172.30.0.2:8000 | **Fails under OrbStack**; to be checked on the Nebius VM (needs `br_netfilter`) |
| Host services via the bridge gateway | `--internal` does not filter INPUT: a host listener on `0.0.0.0` was reachable at 172.30.0.1:<port>; 127.0.0.1 listeners were not. VM services bind 127.0.0.1; sshd/Caddy on 0.0.0.0 need a host INPUT drop on the sandbox bridge | Finding 2026-10-16; mitigation open |
| OpenShell sandbox | Not exercised (needs Linux ≥ 6.2 + Landlock ABI 3 on the VM) | Not yet verified live (ADR 0010) |
| Docker inside Nebius Serverless Jobs | Not exercised | Not yet verified live |
