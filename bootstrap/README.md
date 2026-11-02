# Cluster Bootstrap Runbook

This runbook defines the minimal manual bootstrap after Argo CD is installed.
Everything after this seed step is managed by Argo CD from Git.

## Goal

- Seed project guardrails first (AppProjects)
- Seed one root Application
- Let root manage platform and workload apps

## Preconditions

- Argo CD is installed in namespace `argocd`
- You have cluster-admin access on the target cluster
- You are on the correct Kubernetes context

## Bootstrap (per cluster)

Run from the repository root.

1. Apply AppProjects (guardrails first):

   ```bash
   kubectl apply -k projects
   ```

2. Apply root application:

   ```bash
   kubectl apply -f bootstrap/root-app.yaml
   ```

## Verify

1. Projects are present:

   ```bash
   kubectl -n argocd get appproject
   ```

2. Root and child apps exist:

   ```bash
   kubectl -n argocd get applications
   ```

3. Reconciliation is healthy:

   ```bash
   argocd app list --refresh -o wide
   ```

## Ongoing Operations

- Do not `kubectl apply` app manifests directly after bootstrap
- Add or change applications via Git PRs only
- Keep `default` AppProject deny-all

## Dev/Management/Test/Prod Usage

Apply the same two-step seed flow to each target cluster context:

- `dev`
- `management`
- `test`
- `prod`

The same root app then reconciles environment-specific content from Git as your repo evolves.

For one-command onboarding, use:

```bash
./bootstrap/bootstrap-cluster.sh <cluster-id> [--context name]
```

Examples:

```bash
./bootstrap/bootstrap-cluster.sh dev
./bootstrap/bootstrap-cluster.sh test
./bootstrap/bootstrap-cluster.sh prod
```

New clusters use unique IDs such as `management-kind` and `management-k3s`.
The helper selects `bootstrap/apps/<cluster-id>` exactly, validates configured
profiles, and passes the context explicitly without changing your current context.
For compatibility, the existing `dev`, `staging`, and `prod` IDs retain their
`kind-` context defaults. Unknown IDs do not fall back to a generic source.

```bash
bash bootstrap/bootstrap-cluster.sh management-kind
bash bootstrap/bootstrap-cluster.sh management-k3s
```

The K3s overlay is a deployment-blocked template until its LAN/DNS inputs are
configured. See [Cluster Profiles](../clusters/PROFILES.md) for composition,
prerequisites, controller ownership, and a staged rollout.

For production-like contexts, the helper asks for explicit confirmation.
Use `--yes` for non-interactive automation when appropriate.

## Create a Headlamp admin token

The Headlamp chart manages the `headlamp` ServiceAccount in namespace `headlamp`
and its `headlamp-admin` ClusterRoleBinding to `cluster-admin`. The binding name
is not the ServiceAccount name.

After Headlamp has synced, verify access and create a short-lived token on the
intended cluster (shown here for `management-k3s`):

```bash
kubectl --context management-k3s auth can-i list events --all-namespaces --as=system:serviceaccount:headlamp:headlamp
kubectl --context management-k3s -n headlamp create token headlamp --duration=1h
```

The access check should return `yes`. Replace `management-k3s` with the context
for your target cluster as needed.

Sign out of Headlamp and sign in with the new token. Treat it as a cluster-admin
credential; do not commit or share it. Generate a new token when it expires.

Do not create a separate `kube-system/headlamp-admin` ServiceAccount for this
flow. Its token is not authorized by the chart-managed binding and will produce
errors such as `cannot list resource "events" ... at the cluster scope`.
Keep RBAC changes in Git rather than editing the chart-managed binding manually.