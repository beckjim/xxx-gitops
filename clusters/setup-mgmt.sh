#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

DISTRO="${1:-}"
RESUME=false
if [[ $# -eq 2 && "$DISTRO" == k3s && "$2" == --resume ]]; then
  RESUME=true
elif [[ $# -ne 1 || ( "$DISTRO" != kind && "$DISTRO" != k3s ) ]]; then
  fail "Usage: $0 kind | k3s [--resume]; see clusters/README.md."
fi

CONTEXT=management
if [[ "$DISTRO" == kind ]]; then
  for tool in kind kubectl; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is required."
  done
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  CONTEXTS="$(kubectl config get-contexts -o name)"
  while IFS= read -r context; do
    [[ "$context" != "$CONTEXT" && "$context" != kind-management ]] ||
      fail "Context $context already exists. Refusing to overwrite an existing management cluster."
  done <<< "$CONTEXTS"
  kind create cluster --name management --config "$SCRIPT_DIR/management/cluster.yaml" --wait 60s
  kubectl config rename-context kind-management "$CONTEXT"
  kubectl config use-context "$CONTEXT"
  kubectl --context "$CONTEXT" wait --for=condition=Ready nodes --all --timeout=180s
  echo "Management cluster ready. Context: $CONTEXT"
  exit 0
fi

[[ "$(uname -s)" == Linux ]] || fail "Native K3s requires Linux. On macOS, use a Linux VM or k3d instead."
[[ -d /run/systemd/system || -x /sbin/openrc-run ]] || fail "K3s requires a running systemd or OpenRC host (enable systemd when using WSL2)."

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
KUBECONFIG_FILE="${KUBECONFIG_FILE:-${HOME}/.kube/k3s-management.yaml}"
USER_KUBECONFIG="${HOME}/.kube/config"
K3S_VERSION="${K3S_VERSION:-}"

if [[ -z "$API_SERVER_ADDRESS" ]]; then
  ROUTE="$(ip -4 route get 1.1.1.1)" || fail "Cannot detect a LAN IP. Set API_SERVER_ADDRESS to this host's LAN IPv4 address."
  read -r -a ROUTE_FIELDS <<< "$ROUTE"
  for ((i = 0; i + 1 < ${#ROUTE_FIELDS[@]}; i++)); do
    if [[ "${ROUTE_FIELDS[i]}" == src ]]; then
      API_SERVER_ADDRESS="${ROUTE_FIELDS[i + 1]}"
      break
    fi
  done
fi

[[ "$API_SERVER_ADDRESS" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail "API_SERVER_ADDRESS must be this host's LAN IPv4 address."
IFS=. read -r -a OCTETS <<< "$API_SERVER_ADDRESS"
for octet in "${OCTETS[@]}"; do
  ((10#$octet <= 255)) || fail "Invalid IPv4 address: $API_SERVER_ADDRESS"
done
((10#${OCTETS[0]} > 0 && 10#${OCTETS[0]} < 224 && 10#${OCTETS[0]} != 127)) || fail "Use a unicast LAN address, not a wildcard, loopback, or multicast address."
LOCAL_ADDRESSES="$(ip -4 -o address show)"
ADDRESS_FOUND=false
while read -r _ _ _ address _; do
  if [[ "${address%/*}" == "$API_SERVER_ADDRESS" ]]; then
    ADDRESS_FOUND=true
    break
  fi
done <<< "$LOCAL_ADDRESSES"
[[ "$ADDRESS_FOUND" == true ]] || fail "$API_SERVER_ADDRESS is not assigned to a local interface."

[[ "$API_SERVER_PORT" =~ ^[0-9]{1,5}$ ]] || fail "Invalid port: $API_SERVER_PORT"
((10#$API_SERVER_PORT >= 1 && 10#$API_SERVER_PORT <= 65535)) || fail "Invalid port: $API_SERVER_PORT"
API_SERVER_PORT=$((10#$API_SERVER_PORT))
((API_SERVER_PORT < 30000 || API_SERVER_PORT > 32767)) || fail "API_SERVER_PORT must be outside the reserved NodePort range (30000-32767)."
[[ "$KUBECONFIG_FILE" == /* ]] || fail "KUBECONFIG_FILE must be an absolute path."
python3 -c '
import os
import sys
if os.path.realpath(sys.argv[1]) == os.path.realpath(sys.argv[2]):
    sys.exit("ERROR: KUBECONFIG_FILE must differ from the user kubeconfig.")
' "$KUBECONFIG_FILE" "$USER_KUBECONFIG"
[[ ! -L "$KUBECONFIG_FILE" ]] || fail "Refusing symlink kubeconfig: $KUBECONFIG_FILE."
if [[ "$RESUME" == true ]]; then
  [[ -f "$KUBECONFIG_FILE" ]] || fail "--resume requires the kubeconfig created by the failed run: $KUBECONFIG_FILE."
else
  [[ ! -e "$KUBECONFIG_FILE" ]] || fail "Refusing to overwrite $KUBECONFIG_FILE. Use --resume to continue a failed setup."
fi

if [[ ${#SUDO[@]} -gt 0 ]]; then
  "${SUDO[@]}" -v
fi
if [[ "$RESUME" == false ]]; then
  for path in /etc/rancher/k3s /var/lib/rancher/k3s /etc/systemd/system/k3s.service /etc/systemd/system/k3s-agent.service /etc/init.d/k3s /etc/init.d/k3s-agent; do
    if "${SUDO[@]}" test -e "$path"; then
      fail "Existing K3s installation found at $path. Use --resume to continue a failed setup."
    fi
  done
fi

echo "Setting up native K3s management cluster"
echo "Kubernetes API: https://${API_SERVER_ADDRESS}:${API_SERVER_PORT}"
echo "Allow the API TCP port only from trusted LAN clients. No firewall rules will be changed."

INSTALLER=""
MERGED_CONFIG=""
cleanup() {
  [[ -z "$INSTALLER" ]] || rm -f "$INSTALLER"
  [[ -z "$MERGED_CONFIG" ]] || rm -f "$MERGED_CONFIG"
}
trap cleanup EXIT
if [[ "$RESUME" == false ]]; then
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
    --node-name management \
    --tls-san "$API_SERVER_ADDRESS" \
    --https-listen-port "$API_SERVER_PORT" \
    --write-kubeconfig-mode 600

  export PATH="${PATH}:/usr/local/bin"
  command -v k3s >/dev/null 2>&1 || fail "The installer did not provide the k3s binary."
  mkdir -p "$(dirname "$KUBECONFIG_FILE")"
  (set -o noclobber; "${SUDO[@]}" cat /etc/rancher/k3s/k3s.yaml > "$KUBECONFIG_FILE")
  chmod 600 "$KUBECONFIG_FILE"
else
  export PATH="${PATH}:/usr/local/bin"
  command -v k3s >/dev/null 2>&1 || fail "--resume requires an installed k3s binary."
  echo "Resuming setup without reinstalling or reconfiguring K3s."
fi

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
    if len(entries) != 1 or entries[0]["name"] not in ("default", "k3s-management", name):
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
  KUBECONFIG="$KUBECONFIG_FILE:$USER_KUBECONFIG" k3s kubectl config view --raw --flatten -o json > "$MERGED_CONFIG"
else
  cp "$KUBECONFIG_FILE" "$MERGED_CONFIG"
fi
chmod 600 "$MERGED_CONFIG"
mv "$MERGED_CONFIG" "$USER_KUBECONFIG"
MERGED_CONFIG=""

kube wait --for=create node/management --timeout=180s
kube wait --for=condition=Ready node/management --timeout=180s

kube get nodes -o wide
echo
echo "Management cluster ready. Kubeconfig merged into $USER_KUBECONFIG."
echo "Context: $CONTEXT"
echo "The kubeconfig grants cluster-admin access. Keep it private and refresh it from /etc/rancher/k3s/k3s.yaml before its client certificates expire."
