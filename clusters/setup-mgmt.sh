#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

DISTRO="${1:-}"
if [[ $# -lt 1 || $# -gt 2 || ( "$DISTRO" != kind && "$DISTRO" != k3s ) ]]; then
  fail "Usage: $0 kind|k3s [cluster-id]; see clusters/README.md."
fi

CLUSTER_ID="${2:-${CLUSTER_ID:-management-$DISTRO}}"
[[ ${#CLUSTER_ID} -le 63 && "$CLUSTER_ID" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] ||
  fail "Invalid cluster ID: $CLUSTER_ID"
CONTEXT="$CLUSTER_ID"
SCRIPT_DIR="$(cd "${BASH_SOURCE[0]%/*}" && pwd)"
if [[ -f "$SCRIPT_DIR/$CLUSTER_ID/settings.json" ]]; then
  command -v python3 >/dev/null 2>&1 || fail "python3 is required to validate the cluster profile."
  PROFILE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["profile"])' \
    "$SCRIPT_DIR/$CLUSTER_ID/settings.json")"
  [[ "$PROFILE" == "$DISTRO" || ( "$DISTRO" == k3s && "$PROFILE" == k3s-lan ) ]] ||
    fail "Cluster $CLUSTER_ID uses profile $PROFILE, not $DISTRO."
fi
USER_KUBECONFIG="${HOME}/.kube/config"
KUBECONFIG_FILE="${KUBECONFIG_FILE:-${HOME}/.kube/${CLUSTER_ID}.yaml}"
if [[ "$DISTRO" == kind ]]; then
  for tool in kind kubectl python3; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is required."
  done
  CONTEXTS="$(kubectl --kubeconfig "$USER_KUBECONFIG" config get-contexts -o name)"
  while IFS= read -r context; do
    [[ "$context" != "$CONTEXT" && "$context" != "kind-$CLUSTER_ID" ]] ||
      fail "Context $context already exists. Refusing to overwrite an existing management cluster."
  done <<< "$CONTEXTS"
  if [[ -e "$USER_KUBECONFIG" ]]; then
    kubectl --kubeconfig "$USER_KUBECONFIG" config view --raw -o json |
      python3 -c '
import json
import sys
config = json.load(sys.stdin)
names = set(sys.argv[1:])
if any(entry["name"] in names for section in ("clusters", "users", "contexts")
       for entry in (config.get(section) or [])):
    sys.exit("ERROR: Cluster identity already exists; refusing to overwrite it.")
' "$CONTEXT" "kind-$CLUSTER_ID"
  fi
  [[ "$KUBECONFIG_FILE" == /* && "$KUBECONFIG_FILE" != "$USER_KUBECONFIG" ]] ||
    fail "KUBECONFIG_FILE must be an absolute path different from the user kubeconfig."
  [[ ! -e "$KUBECONFIG_FILE" && ! -L "$KUBECONFIG_FILE" ]] ||
    fail "Refusing to overwrite $KUBECONFIG_FILE."
  mkdir -p "$(dirname "$KUBECONFIG_FILE")"
  kind create cluster --name "$CLUSTER_ID" \
    --config "$SCRIPT_DIR/management/cluster.yaml" \
    --kubeconfig "$KUBECONFIG_FILE" --wait 60s
  kubectl --kubeconfig "$KUBECONFIG_FILE" config rename-context "kind-$CLUSTER_ID" "$CONTEXT"
  MERGED_CONFIG="$(mktemp "$(dirname "$USER_KUBECONFIG")/.config.XXXXXX")"
  trap 'rm -f "$MERGED_CONFIG"' EXIT
  KUBECONFIG="$USER_KUBECONFIG:$KUBECONFIG_FILE" \
    kubectl config view --raw --flatten -o json > "$MERGED_CONFIG"
  chmod 600 "$MERGED_CONFIG" "$KUBECONFIG_FILE"
  mv "$MERGED_CONFIG" "$USER_KUBECONFIG"
  kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" \
    wait --for=condition=Ready nodes --all --timeout=180s
  echo "Management cluster ready. Context: $CONTEXT"
  exit 0
fi

[[ "$(uname -s)" == Linux ]] ||
  fail "Native K3s requires Linux. On macOS, use a Linux VM or k3d instead."
[[ -d /run/systemd/system || -x /sbin/openrc-run ]] ||
  fail "K3s requires a running systemd or OpenRC host (enable systemd when using WSL2)."

for tool in curl ip python3; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is required."
done

SUDO=()
if [[ "$(id -u)" -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || fail "sudo is required to install the K3s system service."
  SUDO=(sudo)
fi

API_SERVER_ADDRESS="${API_SERVER_ADDRESS:-}"
API_SERVER_PORT="${API_SERVER_PORT:-6443}"
K3S_VERSION="${K3S_VERSION:-}"

if [[ -z "$API_SERVER_ADDRESS" ]]; then
  ROUTE="$(ip -4 route get 1.1.1.1)" ||
    fail "Cannot detect a LAN IP. Set API_SERVER_ADDRESS to this host's LAN IPv4 address."
  read -r -a ROUTE_FIELDS <<< "$ROUTE"
  for ((i = 0; i + 1 < ${#ROUTE_FIELDS[@]}; i++)); do
    if [[ "${ROUTE_FIELDS[i]}" == src ]]; then
      API_SERVER_ADDRESS="${ROUTE_FIELDS[i + 1]}"
      break
    fi
  done
fi

[[ "$API_SERVER_ADDRESS" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] ||
  fail "API_SERVER_ADDRESS must be this host's LAN IPv4 address."
IFS=. read -r -a OCTETS <<< "$API_SERVER_ADDRESS"
for octet in "${OCTETS[@]}"; do
  ((10#$octet <= 255)) || fail "Invalid IPv4 address: $API_SERVER_ADDRESS"
done
((10#${OCTETS[0]} > 0 && 10#${OCTETS[0]} < 224 && 10#${OCTETS[0]} != 127)) ||
  fail "Use a unicast LAN address, not a wildcard, loopback, or multicast address."
LOCAL_ADDRESSES="$(ip -4 -o address show)"
ADDRESS_FOUND=false
while read -r _ _ _ address _; do
  if [[ "${address%/*}" == "$API_SERVER_ADDRESS" ]]; then
    ADDRESS_FOUND=true
    break
  fi
done <<< "$LOCAL_ADDRESSES"
[[ "$ADDRESS_FOUND" == true ]] ||
  fail "$API_SERVER_ADDRESS is not assigned to a local interface."

source "$SCRIPT_DIR/lan-access.sh"
API_SERVER_FQDN="$(select_lan_fqdn "${API_SERVER_FQDN:-}" "$API_SERVER_ADDRESS")"
TLS_ARGS=(--tls-san "$API_SERVER_ADDRESS")
if [[ -n "$API_SERVER_FQDN" ]]; then
  TLS_ARGS+=(--tls-san "$API_SERVER_FQDN")
fi

[[ "$API_SERVER_PORT" =~ ^[0-9]{1,5}$ ]] ||
  fail "Invalid port: $API_SERVER_PORT"
((10#$API_SERVER_PORT >= 1 && 10#$API_SERVER_PORT <= 65535)) ||
  fail "Invalid port: $API_SERVER_PORT"
API_SERVER_PORT=$((10#$API_SERVER_PORT))
((API_SERVER_PORT < 30000 || API_SERVER_PORT > 32767)) ||
  fail "API_SERVER_PORT must be outside the reserved NodePort range (30000-32767)."
[[ "$KUBECONFIG_FILE" == /* ]] ||
  fail "KUBECONFIG_FILE must be an absolute path."
python3 -c '
import os
import sys
if os.path.realpath(sys.argv[1]) == os.path.realpath(sys.argv[2]):
    sys.exit("ERROR: KUBECONFIG_FILE must differ from the user kubeconfig.")
' "$KUBECONFIG_FILE" "$USER_KUBECONFIG"
[[ ! -L "$KUBECONFIG_FILE" ]] ||
  fail "Refusing symlink kubeconfig: $KUBECONFIG_FILE."
[[ ! -e "$KUBECONFIG_FILE" ]] ||
  fail "Refusing to overwrite existing kubeconfig: $KUBECONFIG_FILE."

if [[ ${#SUDO[@]} -gt 0 ]]; then
  "${SUDO[@]}" -v
fi
K3S_PATHS=(
  /etc/rancher/k3s
  /var/lib/rancher/k3s
  /etc/systemd/system/k3s.service
  /etc/systemd/system/k3s-agent.service
  /etc/init.d/k3s
  /etc/init.d/k3s-agent
)
for path in "${K3S_PATHS[@]}"; do
  if "${SUDO[@]}" test -e "$path"; then
    fail "Existing K3s installation found at $path."
  fi
done

echo "Setting up native K3s management cluster"
echo "Kubernetes API: https://${API_SERVER_ADDRESS}:${API_SERVER_PORT}"
if [[ -n "$API_SERVER_FQDN" ]]; then
  echo "Kubernetes API (DNS): https://${API_SERVER_FQDN}:${API_SERVER_PORT}"
fi
echo "Allow the API TCP port only from trusted LAN clients. No firewall rules will be changed."

INSTALLER=""
MERGED_CONFIG=""
cleanup() {
  [[ -z "$INSTALLER" ]] || rm -f "$INSTALLER"
  [[ -z "$MERGED_CONFIG" ]] || rm -f "$MERGED_CONFIG"
}
trap cleanup EXIT
INSTALLER="$(mktemp)"
curl --fail --silent --show-error --location https://get.k3s.io --output "$INSTALLER"
"${SUDO[@]}" env \
  INSTALL_K3S_VERSION="$K3S_VERSION" \
  INSTALL_K3S_CHANNEL=stable \
  INSTALL_K3S_SYMLINK=skip \
  INSTALL_K3S_BIN_DIR=/usr/local/bin \
  sh "$INSTALLER" server \
  --bind-address "$API_SERVER_ADDRESS" \
  --advertise-address "$API_SERVER_ADDRESS" \
  --node-ip "$API_SERVER_ADDRESS" \
  --node-name "$CLUSTER_ID" \
  --disable=traefik \
  --disable=servicelb \
  --disable=metrics-server \
  "${TLS_ARGS[@]}" \
  --https-listen-port "$API_SERVER_PORT" \
  --write-kubeconfig-mode 600

export PATH="${PATH}:/usr/local/bin"
command -v k3s >/dev/null 2>&1 ||
  fail "The installer did not provide the k3s binary."
mkdir -p "$(dirname "$KUBECONFIG_FILE")"
(set -o noclobber; "${SUDO[@]}" cat /etc/rancher/k3s/k3s.yaml > "$KUBECONFIG_FILE")
chmod 600 "$KUBECONFIG_FILE"

kube() {
  k3s kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" "$@"
}

# kubectl has no rename-cluster or rename-user command.
MERGED_CONFIG="$(mktemp "$(dirname "$KUBECONFIG_FILE")/.k3s-config.XXXXXX")"
kube config view --raw -o json | python3 -c '
import json
import sys

config = json.load(sys.stdin)
name, server = sys.argv[1:]
for section in ("clusters", "users", "contexts"):
    entries = config.get(section, [])
    if len(entries) != 1 or entries[0]["name"] not in ("default", name):
        sys.exit("ERROR: Expected a single K3s entry in " + section)
    entries[0]["name"] = name
config["clusters"][0]["cluster"]["server"] = server
config["contexts"][0]["context"].update(cluster=name, user=name)
config["current-context"] = name
json.dump(config, sys.stdout)
' "$CONTEXT" "https://${API_SERVER_ADDRESS}:${API_SERVER_PORT}" > "$MERGED_CONFIG"
chmod 600 "$MERGED_CONFIG"
mv "$MERGED_CONFIG" "$KUBECONFIG_FILE"
MERGED_CONFIG=""

mkdir -p "$(dirname "$USER_KUBECONFIG")"
MERGED_CONFIG="$(mktemp "$(dirname "$USER_KUBECONFIG")/.config.XXXXXX")"
if [[ -e "$USER_KUBECONFIG" ]]; then
  k3s kubectl --kubeconfig "$USER_KUBECONFIG" config view --raw -o json |
    python3 -c '
import json
import sys
config = json.load(sys.stdin)
name = sys.argv[1]
if any(entry["name"] == name for section in ("clusters", "users", "contexts")
       for entry in (config.get(section) or [])):
    sys.exit("ERROR: Cluster identity already exists: " + name)
' "$CONTEXT"
  KUBECONFIG="$USER_KUBECONFIG:$KUBECONFIG_FILE" k3s kubectl config view --raw --flatten -o json > "$MERGED_CONFIG"
else
  cp "$KUBECONFIG_FILE" "$MERGED_CONFIG"
fi
chmod 600 "$MERGED_CONFIG"
mv "$MERGED_CONFIG" "$USER_KUBECONFIG"
MERGED_CONFIG=""

kube wait --for=create "node/$CLUSTER_ID" --timeout=180s
kube wait --for=condition=Ready "node/$CLUSTER_ID" --timeout=180s

kube get --raw=/readyz --request-timeout=10s
if [[ -n "$API_SERVER_FQDN" ]]; then
  kube --server "https://${API_SERVER_FQDN}:${API_SERVER_PORT}" \
    get --raw=/readyz --request-timeout=10s
fi

kube get nodes -o wide
echo
echo "Management cluster ready. Kubeconfig merged into $USER_KUBECONFIG."
echo "Context: $CONTEXT"
echo "The kubeconfig grants cluster-admin access. Keep it private and refresh it from /etc/rancher/k3s/k3s.yaml before its client certificates expire."
