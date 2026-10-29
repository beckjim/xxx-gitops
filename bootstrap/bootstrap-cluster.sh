#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <cluster-id> [--context name] [--yes]" >&2
  exit 1
fi

CLUSTER_NAME="$1"
shift
[[ ${#CLUSTER_NAME} -le 63 && "$CLUSTER_NAME" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] ||
  { echo "ERROR: Invalid cluster ID: $CLUSTER_NAME" >&2; exit 1; }
KUBE_CONTEXT="$CLUSTER_NAME"
case "$CLUSTER_NAME" in
  dev | staging | prod) KUBE_CONTEXT="kind-$CLUSTER_NAME" ;;
esac
AUTO_APPROVE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) AUTO_APPROVE=--yes; shift ;;
    --context)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] ||
        { echo "ERROR: --context requires a name." >&2; exit 1; }
      KUBE_CONTEXT="$2"; shift 2 ;;
    *) echo "ERROR: Invalid option: $1" >&2; exit 1 ;;
  esac
done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is required but not installed"
  exit 1
fi

BOOTSTRAP_APPS_PATH="bootstrap/apps/${CLUSTER_NAME}"
[[ -d "${REPO_ROOT}/${BOOTSTRAP_APPS_PATH}" ]] ||
  { echo "ERROR: No bootstrap source for cluster $CLUSTER_NAME." >&2; exit 1; }
PROFILE=""
SETTINGS="$REPO_ROOT/clusters/$CLUSTER_NAME/settings.json"
if [[ -f "$SETTINGS" ]]; then
  command -v python3 >/dev/null 2>&1 ||
    { echo "ERROR: python3 is required for profile validation." >&2; exit 1; }
  python3 "$REPO_ROOT/clusters/validate-profile.py" "$CLUSTER_NAME"
  PROFILE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["profile"])' "$SETTINGS")"
  [[ "$KUBE_CONTEXT" == "$CLUSTER_NAME" ]] ||
    { echo "ERROR: Context must match the configured cluster ID." >&2; exit 1; }
fi

# Add a guardrail for production-like contexts.
if [[ "${KUBE_CONTEXT,,}" == *"prod"* || "${KUBE_CONTEXT,,}" == *"production"* ]]; then
  if [[ "${AUTO_APPROVE}" != "--yes" ]]; then
    echo "You are targeting a production-like context: ${KUBE_CONTEXT}"
    read -r -p "Type 'prod' to continue: " CONFIRM
    if [[ "${CONFIRM}" != "prod" ]]; then
      echo "Aborted. Confirmation did not match."
      exit 1
    fi
  fi
fi

echo "Using context: ${KUBE_CONTEXT}"
kube() {
  kubectl --context "$KUBE_CONTEXT" "$@"
}
[[ "$(kubectl config get-contexts "$KUBE_CONTEXT" -o name)" == "$KUBE_CONTEXT" ]] ||
  { echo "ERROR: Context $KUBE_CONTEXT does not exist." >&2; exit 1; }
if [[ -n "$PROFILE" ]]; then
  kube get nodes -o json | python3 -c '
import json
import sys
nodes = json.load(sys.stdin)["items"]
settings = json.load(open(sys.argv[1]))
if not nodes:
    sys.exit("ERROR: Target cluster has no nodes.")
if settings["profile"] == "k3s-lan":
    if any("+k3s" not in node["status"]["nodeInfo"]["kubeletVersion"] for node in nodes):
        sys.exit("ERROR: The k3s-lan profile requires K3s nodes.")
    addresses = {address["address"] for node in nodes for address in node["status"].get("addresses", [])
                 if address["type"] == "InternalIP"}
    if settings["nodeAddress"] not in addresses:
        sys.exit("ERROR: Target node IP does not match the configured cluster.")
elif settings["profile"] == "kind":
    if any(not node.get("spec", {}).get("providerID", "").startswith("kind://") for node in nodes):
        sys.exit("ERROR: The kind profile requires kind nodes.")
' "$SETTINGS"
fi
if [[ "$PROFILE" == k3s-lan ]]; then
  kube get secret grafana-admin -n monitoring -o json | python3 -c '
import base64
import json
import sys
data = json.load(sys.stdin).get("data", {})
if not data.get("admin-user") or not data.get("admin-password"):
    sys.exit("ERROR: grafana-admin must contain admin-user and admin-password.")
if base64.b64decode(data["admin-password"]) == b"admin":
    sys.exit("ERROR: Replace the default Grafana password before exposing it on the LAN.")
'
  kube get addons.k3s.cattle.io -n kube-system -o json | python3 -c '
import json
import sys
conflicts = {"traefik", "traefik-crds", "servicelb", "metrics-server"}
found = [item["metadata"]["name"] for item in json.load(sys.stdin)["items"]
         if item["metadata"]["name"] in conflicts]
if found:
    sys.exit("ERROR: Disable overlapping K3s packaged components before bootstrap: " + ", ".join(found))
'
  kube get daemonsets -n kube-system -o json | python3 -c '
import json
import sys
found = [item["metadata"]["name"] for item in json.load(sys.stdin)["items"]
         if item["metadata"]["name"].startswith("svclb-")]
if found:
    sys.exit("ERROR: ServiceLB DaemonSets remain; disable ServiceLB before bootstrap: " + ", ".join(found))
'
fi

echo "Applying AppProjects from ${REPO_ROOT}/projects"
kube apply -k "${REPO_ROOT}/projects"

echo "Applying root application (source path: ${BOOTSTRAP_APPS_PATH})"
cat <<EOF | kube apply -f -
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: bootstrap-root
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: platform-admins
  source:
    repoURL: https://github.com/beckjim/xxx-gitops
    targetRevision: main
    path: ${BOOTSTRAP_APPS_PATH}
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
EOF

echo
echo "Bootstrap apply complete. Quick verification:"
kube -n argocd get appproject
kube -n argocd get applications
