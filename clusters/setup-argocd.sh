#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

NODEPORT=false
if [[ $# -eq 1 && "$1" == --nodeport ]]; then
  NODEPORT=true
elif [[ $# -ne 0 ]]; then
  fail "Usage: $0 [--nodeport]; see clusters/README.md."
fi

for tool in kubectl helm argocd base64 curl; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is required."
done

CONTEXT=management
kube() {
  kubectl --context "$CONTEXT" "$@"
}
[[ "$(kubectl config get-contexts "$CONTEXT" -o name)" == "$CONTEXT" ]] ||
  fail "Context management is missing. Run setup-mgmt.sh first."

SERVICE_ARGS=(--set server.service.type=ClusterIP)
ENDPOINT=localhost:8080
if [[ "$NODEPORT" == true ]]; then
  PORT="${ARGOCD_HTTPS_NODE_PORT:-30443}"
  [[ "$PORT" =~ ^[0-9]{1,5}$ ]] || fail "Invalid ARGOCD_HTTPS_NODE_PORT: $PORT"
  PORT=$((10#$PORT))
  ((PORT >= 30000 && PORT <= 32767)) ||
    fail "ARGOCD_HTTPS_NODE_PORT must be in the default NodePort range (30000-32767)."
  SERVER="$(kube config view --minify -o 'jsonpath={.clusters[0].cluster.server}')"
  [[ "$SERVER" =~ ^https://([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+/?$ ]] ||
    fail "--nodeport requires a management API endpoint with a LAN IPv4 address."
  ADDRESS="${SERVER#https://}"
  ADDRESS="${ADDRESS%%:*}"
  IFS=. read -r -a OCTETS <<< "$ADDRESS"
  for octet in "${OCTETS[@]}"; do
    ((10#$octet <= 255)) || fail "Invalid management API IPv4 address: $ADDRESS"
  done
  ((10#${OCTETS[0]} > 0 && 10#${OCTETS[0]} < 224 && 10#${OCTETS[0]} != 127)) ||
    fail "--nodeport requires a unicast LAN address, not loopback or multicast."
  ENDPOINT="${ADDRESS}:${PORT}"
  SERVICE_ARGS=(--set server.service.type=NodePort --set "server.service.nodePortHttps=$PORT")
fi

echo "Installing Argo CD in context $CONTEXT..."
helm repo add argo https://argoproj.github.io/argo-helm --force-update
helm repo update argo
CHART_ARGS=()
if [[ -n "${ARGOCD_CHART_VERSION:-}" ]]; then
  CHART_ARGS=(--version "$ARGOCD_CHART_VERSION")
fi
helm upgrade --install argocd argo/argo-cd \
  --kube-context "$CONTEXT" \
  --namespace argocd \
  --create-namespace \
  "${SERVICE_ARGS[@]}" \
  "${CHART_ARGS[@]}" \
  --wait --timeout 5m
kube wait --for=condition=available --timeout=180s \
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
  kubectl --context "$CONTEXT" port-forward --address 127.0.0.1 svc/argocd-server -n argocd 8080:443 > "$ACCESS_LOG" 2>&1 &
  PORTFWD_PID=$!
fi

READY=false
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
  if curl --fail --silent --show-error --insecure --max-time 2 \
    "https://${ENDPOINT}/healthz" --output /dev/null 2> "${ACCESS_LOG}.error"; then
    READY=true
    break
  fi
  sleep 1
done
if [[ "$READY" == false ]]; then
  cat "$ACCESS_LOG" >&2
  if [[ -f "${ACCESS_LOG}.error" ]]; then
    cat "${ACCESS_LOG}.error" >&2
  fi
  rm -f "${ACCESS_LOG}.error"
  fail "Argo CD is not reachable at https://${ENDPOINT}."
fi
rm -f "${ACCESS_LOG}.error"

ARGOCD_PASSWORD="$(kube get secret argocd-initial-admin-secret -n argocd -o 'jsonpath={.data.password}' | base64 -d)"
[[ -n "$ARGOCD_PASSWORD" ]] || fail "Argo CD initial admin password is empty."
argocd login "$ENDPOINT" --username admin --password "$ARGOCD_PASSWORD" --insecure --grpc-web
unset ARGOCD_PASSWORD
kube get svc argocd-server -n argocd

if [[ -n "$PORTFWD_PID" ]] && ! kill -0 "$PORTFWD_PID" 2>/dev/null; then
  cat "$ACCESS_LOG" >&2
  fail "Argo CD port-forward exited during setup."
fi
SETUP_COMPLETE=true
echo "Argo CD setup complete. URL: https://${ENDPOINT} (self-signed certificate)"
if [[ -n "$PORTFWD_PID" ]]; then
  echo "Local port-forward running (PID: $PORTFWD_PID). Stop it with: kill $PORTFWD_PID"
fi
echo "Change the initial password with: argocd account update-password"
