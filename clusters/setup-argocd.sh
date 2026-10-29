#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

ACCESS_MODE=auto
CONTEXT="${KUBE_CONTEXT:-management}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --nodeport | --local)
      [[ "$ACCESS_MODE" == auto ]] || fail "Choose only one access mode."
      ACCESS_MODE="${1#--}"
      shift
      ;;
    --context)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "--context requires a context name."
      CONTEXT="$2"
      shift 2
      ;;
    *) fail "Usage: $0 [--context name] [--nodeport | --local]; see clusters/README.md." ;;
  esac
done

for tool in kubectl helm argocd base64 curl; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is required."
done

kube() {
  kubectl --context "$CONTEXT" "$@"
}
if [[ "$(kubectl config get-contexts "$CONTEXT" -o name)" != "$CONTEXT" ]]; then
  fail "Context $CONTEXT is missing. Run setup-mgmt.sh first."
fi

if [[ "$ACCESS_MODE" == auto ]]; then
  KUBELET_VERSIONS="$(kube get nodes -o 'jsonpath={.items[*].status.nodeInfo.kubeletVersion}')"
  [[ -n "$KUBELET_VERSIONS" ]] || fail "Cannot detect cluster distribution: no node versions returned."
  if [[ "$KUBELET_VERSIONS" == *+k3s* ]]; then
    ACCESS_MODE=nodeport
  else
    ACCESS_MODE=local
  fi
fi
NODEPORT=false
[[ "$ACCESS_MODE" != nodeport ]] || NODEPORT=true

SERVICE_ARGS=(--set server.service.type=ClusterIP)
ENDPOINT=localhost:8080
ARGOCD_FQDN="${ARGOCD_FQDN:-}"
if [[ "$NODEPORT" == true ]]; then
  PORT="${ARGOCD_HTTPS_NODE_PORT:-31443}"
  HTTP_PORT="${ARGOCD_HTTP_NODE_PORT:-31080}"
  [[ "$PORT" =~ ^[0-9]{1,5}$ ]] ||
    fail "Invalid ARGOCD_HTTPS_NODE_PORT: $PORT"
  PORT=$((10#$PORT))
  ((PORT >= 30000 && PORT <= 32767)) ||
    fail "ARGOCD_HTTPS_NODE_PORT must be in the default NodePort range (30000-32767)."
  [[ "$HTTP_PORT" =~ ^[0-9]{1,5}$ ]] || fail "Invalid ARGOCD_HTTP_NODE_PORT: $HTTP_PORT"
  HTTP_PORT=$((10#$HTTP_PORT))
  ((HTTP_PORT >= 30000 && HTTP_PORT <= 32767 && HTTP_PORT != PORT)) ||
    fail "ARGOCD_HTTP_NODE_PORT must be in 30000-32767 and differ from the HTTPS port."
  SERVER="$(kube config view --minify -o 'jsonpath={.clusters[0].cluster.server}')"
  [[ "$SERVER" =~ ^https://([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+/?$ ]] ||
    fail "--nodeport requires a management API endpoint with a LAN IPv4 address."
  ADDRESS="${SERVER#https://}"
  ADDRESS="${ADDRESS%%:*}"
  IFS=. read -r -a OCTETS <<< "$ADDRESS"
  for octet in "${OCTETS[@]}"; do
    ((10#$octet <= 255)) || fail "Invalid management API IPv4 address: $ADDRESS"
  done
  ((10#${OCTETS[0]} > 0 && 10#${OCTETS[0]} < 224 &&
    10#${OCTETS[0]} != 127)) ||
    fail "--nodeport requires a unicast LAN address, not loopback or multicast."
  ENDPOINT="${ADDRESS}:${PORT}"
  SERVICE_ARGS=(
    --set server.service.type=NodePort
    --set "server.service.nodePortHttps=$PORT"
    --set "server.service.nodePortHttp=$HTTP_PORT"
  )
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  source "$SCRIPT_DIR/lan-access.sh"
  ARGOCD_FQDN="$(select_lan_fqdn "$ARGOCD_FQDN" "$ADDRESS")"
  PUBLIC_ENDPOINT="$ENDPOINT"
  if [[ -n "$ARGOCD_FQDN" ]]; then
    PUBLIC_ENDPOINT="${ARGOCD_FQDN}:${PORT}"
  fi
  SERVICE_ARGS+=(
    --set-string "global.domain=${ARGOCD_FQDN:-$ADDRESS}"
    --set-string "configs.cm.url=https://${PUBLIC_ENDPOINT}"
  )
  echo "Allow Argo CD TCP port $PORT only from trusted LAN clients. No firewall rules will be changed."
elif [[ -n "$ARGOCD_FQDN" ]]; then
  fail "ARGOCD_FQDN requires LAN access; use --nodeport instead of --local."
fi

echo "Installing Argo CD in context $CONTEXT..."
helm repo add argo https://argoproj.github.io/argo-helm --force-update
helm repo update argo
CHART_ARGS=()
if [[ -n "${ARGOCD_CHART_VERSION:-}" ]]; then
  CHART_ARGS=(--version "$ARGOCD_CHART_VERSION")
fi
APPLICATION_HEALTH='
hs = {}
hs.status = "Progressing"
hs.message = "Waiting for child Application sync and health"
if obj.status ~= nil then
  if obj.status.operationState ~= nil and obj.status.operationState.phase == "Running" then
    return hs
  end
  if obj.status.health ~= nil then
    if obj.status.health.status == "Degraded" then
      hs.status = "Degraded"
    elseif obj.status.sync ~= nil and obj.status.sync.status == "Synced" then
      hs.status = obj.status.health.status
    end
  end
end
return hs'
helm upgrade --install argocd argo/argo-cd \
  --kube-context "$CONTEXT" \
  --namespace argocd \
  --create-namespace \
  "${SERVICE_ARGS[@]}" \
  "${CHART_ARGS[@]}" \
  --set-string "configs.cm.resource\\.customizations\\.health\\.argoproj\\.io_Application=$APPLICATION_HEALTH" \
  --wait --timeout 5m
kube wait \
  --for=condition=available \
  --timeout=180s \
  deployment/argocd-server deployment/argocd-repo-server -n argocd

PORTFWD_PID=""
ACCESS_LOG="$(mktemp)"
SETUP_COMPLETE=false
cleanup() {
  if [[ "$SETUP_COMPLETE" == false && -n "$PORTFWD_PID" ]]; then
    if kill -0 "$PORTFWD_PID" 2>/dev/null; then
      kill "$PORTFWD_PID"
    fi
    wait "$PORTFWD_PID" 2>/dev/null || true
  fi
  rm -f "$ACCESS_LOG" "${ACCESS_LOG}.error"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "$NODEPORT" == false ]]; then
  kubectl --context "$CONTEXT" port-forward \
    --address 127.0.0.1 \
    svc/argocd-server -n argocd 8080:443 > "$ACCESS_LOG" 2>&1 &
  PORTFWD_PID=$!
fi

wait_for_endpoint() {
  local endpoint="$1" attempt
  for ((attempt = 0; attempt < 30; attempt++)); do
    if [[ -n "$PORTFWD_PID" ]]; then
      if ! kill -0 "$PORTFWD_PID" 2>/dev/null; then
        cat "$ACCESS_LOG" >&2
        fail "Argo CD port-forward exited before becoming ready."
      fi
      if ! grep -q 'Forwarding from 127.0.0.1:8080' "$ACCESS_LOG"; then
        sleep 1
        continue
      fi
    fi
    if curl \
      --fail --silent --show-error --insecure --max-time 2 \
      "https://${endpoint}/healthz" --output /dev/null 2> "${ACCESS_LOG}.error"; then
      return
    fi
    sleep 1
  done
  cat "$ACCESS_LOG" >&2
  if [[ -f "${ACCESS_LOG}.error" ]]; then
    cat "${ACCESS_LOG}.error" >&2
  fi
  fail "Argo CD is not reachable at https://${endpoint}."
}
wait_for_endpoint "$ENDPOINT"
if [[ "$NODEPORT" == true && -n "$ARGOCD_FQDN" ]]; then
  wait_for_endpoint "${ARGOCD_FQDN}:${PORT}"
fi
rm -f "${ACCESS_LOG}.error"

ARGOCD_PASSWORD="$(
  kube get secret argocd-initial-admin-secret -n argocd \
    -o 'jsonpath={.data.password}' | base64 -d
)"
[[ -n "$ARGOCD_PASSWORD" ]] || fail "Argo CD initial admin password is empty."
argocd login "$ENDPOINT" \
  --username admin \
  --password "$ARGOCD_PASSWORD" \
  --insecure \
  --grpc-web
unset ARGOCD_PASSWORD
kube get svc argocd-server -n argocd

if [[ -n "$PORTFWD_PID" ]] && ! kill -0 "$PORTFWD_PID" 2>/dev/null; then
  cat "$ACCESS_LOG" >&2
  fail "Argo CD port-forward exited during setup."
fi
SETUP_COMPLETE=true
echo "Argo CD setup complete. URL: https://${ENDPOINT} (self-signed certificate)"
if [[ "$NODEPORT" == true && -n "$ARGOCD_FQDN" ]]; then
  echo "Argo CD DNS URL: https://${ARGOCD_FQDN}:${PORT} (self-signed certificate)"
fi
if [[ -n "$PORTFWD_PID" ]]; then
  echo "Local port-forward running (PID: $PORTFWD_PID)."
  echo "Stop it with: kill $PORTFWD_PID"
fi
echo "Change the initial password with: argocd account update-password"
