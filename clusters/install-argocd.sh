
#!/bin/bash
set -euo pipefail

echo "Installing ArgoCD..."

# Create namespace if it doesn't exist
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -

# Apply ArgoCD manifests with server-side apply
kubectl apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

echo "ArgoCD installation completed successfully"
