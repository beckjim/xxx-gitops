#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <kube-context> [--yes]"
  exit 1
fi

KUBE_CONTEXT="$1"
AUTO_APPROVE="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is required but not installed"
  exit 1
fi

if [[ "${AUTO_APPROVE}" != "" && "${AUTO_APPROVE}" != "--yes" ]]; then
  echo "Invalid option: ${AUTO_APPROVE}"
  echo "Usage: $0 <kube-context> [--yes]"
  exit 1
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
kubectl config use-context "${KUBE_CONTEXT}" >/dev/null

echo "Applying AppProjects from ${REPO_ROOT}/projects"
kubectl apply -k "${REPO_ROOT}/projects"

echo "Applying root application from ${REPO_ROOT}/bootstrap/root-app.yaml"
kubectl apply -f "${REPO_ROOT}/bootstrap/root-app.yaml"

echo
echo "Bootstrap apply complete. Quick verification:"
kubectl -n argocd get appproject
kubectl -n argocd get applications
