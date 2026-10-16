#!/usr/bin/env bash
# Create the internal Docker bridge that replay sandboxes join (idempotent).
# Same spec as ratchet.replay.sandbox.docker.network_create_argv: no route off the host
# (--internal), inter-container traffic off, gateway 172.30.0.1 where the worker binds its
# proxy and mock-tool sidecars. Override with RATCHET_SANDBOX_NETWORK / _SUBNET / _GATEWAY.
set -euo pipefail

name="${RATCHET_SANDBOX_NETWORK:-ratchet-sandbox}"
subnet="${RATCHET_SANDBOX_SUBNET:-172.30.0.0/24}"
gateway="${RATCHET_SANDBOX_GATEWAY:-172.30.0.1}"

log() { printf '%s sandbox-network: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

if docker network inspect "$name" >/dev/null 2>&1; then
  internal="$(docker network inspect -f '{{.Internal}}' "$name")"
  if [[ "$internal" != "true" ]]; then
    log "ERROR: network $name exists but is not --internal; remove it and re-run"
    exit 1
  fi
  log "network $name already exists (internal=true)"
  exit 0
fi

log "creating internal network $name ($subnet, gateway $gateway, icc off)"
docker network create --driver bridge --internal \
  --subnet "$subnet" --gateway "$gateway" \
  --opt com.docker.network.bridge.enable_icc=false \
  "$name" >/dev/null
log "done"

# --- Linux hardening (docs/research/docker-sandbox-s1.md) ----------------------------------
# Docker's inter-container DROP rule only takes effect when bridge traffic goes through
# iptables (br_netfilter); and `--internal` does not stop sandboxes reaching listeners on
# the host itself, so host services other than the sidecars are dropped on this bridge.
if [[ "$(uname -s)" == "Linux" ]]; then
  if command -v modprobe >/dev/null 2>&1; then
    if modprobe br_netfilter 2>/dev/null; then
      log "br_netfilter loaded"
    else
      log "WARN: could not load br_netfilter (inter-container isolation unverified)"
    fi
    sysctl -q -w net.bridge.bridge-nf-call-iptables=1 2>/dev/null || true
    printf 'br_netfilter\n' > /etc/modules-load.d/ratchet-sandbox.conf 2>/dev/null || true
  fi
  if command -v iptables >/dev/null 2>&1; then
    net_id="$(docker network inspect -f '{{.Id}}' "$name")"
    bridge="br-${net_id:0:12}"
    # sandboxes may reach only the sidecars (ephemeral ports) on the gateway; never ssh, http(s),
    # or the dashboard/proxy/mock-tools ports the control plane binds on the host.
    for port in 22 80 443 8080 8081 8082; do
      iptables -C INPUT -i "$bridge" -p tcp --dport "$port" -j DROP 2>/dev/null \
        || iptables -I INPUT -i "$bridge" -p tcp --dport "$port" -j DROP
    done
    log "host services dropped on $bridge for ports 22,80,443,8080-8082"
  fi
fi
