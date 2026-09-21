#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${1:-kind}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but not installed"
  exit 1
fi

RAW_SUBNETS="$(docker network inspect "${NETWORK_NAME}" -f '{{range .IPAM.Config}}{{.Subnet}}{{"\n"}}{{end}}')"
SUBNET=""

while IFS= read -r candidate; do
  if [[ "${candidate}" == *.*/* ]]; then
    SUBNET="${candidate}"
    break
  fi
done <<< "${RAW_SUBNETS}"

if [[ -z "${SUBNET}" ]]; then
  echo "No IPv4 subnet found for docker network '${NETWORK_NAME}'"
  exit 1
fi

BASE_IP="${SUBNET%/*}"
PREFIX="${SUBNET#*/}"

IFS='.' read -r O1 O2 O3 O4 <<< "${BASE_IP}"
if [[ -z "${O1:-}" || -z "${O2:-}" || -z "${O3:-}" || -z "${O4:-}" ]]; then
  echo "Unsupported subnet format: ${SUBNET}"
  exit 1
fi

if (( PREFIX <= 16 )); then
  BASE_PREFIX="${O1}.${O2}.255"
elif (( PREFIX <= 24 )); then
  BASE_PREFIX="${O1}.${O2}.${O3}"
else
  echo "Subnet ${SUBNET} is too small. Need at least a /24-equivalent address space for four pools."
  exit 1
fi

write_pool() {
  local env="$1"
  local start="$2"
  local end="$3"
  local path="${SCRIPT_DIR}/${env}/pool.yaml"

  cat > "${path}" <<EOF
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: ingress-pool
  namespace: metallb-system
spec:
  addresses:
    - ${BASE_PREFIX}.${start}-${BASE_PREFIX}.${end}
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: ingress-l2
  namespace: metallb-system
spec:
  ipAddressPools:
    - ingress-pool
EOF

  echo "${env}: ${BASE_PREFIX}.${start}-${BASE_PREFIX}.${end} -> ${path}"
}

echo "Using docker network '${NETWORK_NAME}' with subnet ${SUBNET}"
write_pool management 10 19
write_pool dev 20 29
write_pool staging 30 39
write_pool prod 40 49

echo "Done. Commit and push these pool.yaml changes so Argo CD can reconcile them."
