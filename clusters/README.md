# Cluster Helpers

Use a single helper script for all environments.

## Command

Run from repository root:

- `./clusters/bootstrap-cluster.sh <kube-context>`
- `./clusters/bootstrap-cluster.sh <kube-context> --yes`

Examples:

- `./clusters/bootstrap-cluster.sh dev`
- `./clusters/bootstrap-cluster.sh management`
- `./clusters/bootstrap-cluster.sh test`
- `./clusters/bootstrap-cluster.sh prod`

For automation/non-interactive runs, use:

- `./clusters/bootstrap-cluster.sh prod --yes`

The script applies:

1. `projects/` (AppProjects)
2. `bootstrap-root` Application (cluster-specific source path when available)

## Cluster-Specific Bootstrap Paths

If `bootstrap/apps/<kube-context>/` exists, the bootstrap script uses it automatically.
Otherwise it falls back to `bootstrap/apps/`.

Examples:

- `./clusters/bootstrap-cluster.sh dev` uses `bootstrap/apps/dev`
- `./clusters/bootstrap-cluster.sh prod` uses `bootstrap/apps/prod`
- `./clusters/bootstrap-cluster.sh test` falls back to `bootstrap/apps`

## MetalLB Ingress Convention

For clusters using `bootstrap/apps/{management,dev,staging,prod}`:

- ingress-nginx is configured as `LoadBalancer`
- MetalLB is installed as a platform component
- each cluster gets its own non-overlapping MetalLB `IPAddressPool`
- Kind `extraPortMappings` for ingress (`30080`/`30443`) are not required

Default pools configured:

- management: `172.18.255.10-172.18.255.19`
- dev: `172.18.255.20-172.18.255.29`
- staging: `172.18.255.30-172.18.255.39`
- prod: `172.18.255.40-172.18.255.49`

Legacy/fallback path (`bootstrap/apps`) still uses NodePort ingress settings.

## NodePort Fallback Convention

For fallback path users (`bootstrap/apps`), ingress remains NodePort-based:

- Keep ingress-nginx NodePorts fixed to `30080` (HTTP) and `30443` (HTTPS).
- In each cluster config, map those fixed `containerPort` values to unique `hostPort` values.

Example mapping in a cluster config:

- `containerPort: 30080` -> `hostPort: 30082`
- `containerPort: 30443` -> `hostPort: 30445`

This allows one shared Argo CD app (`platform/ingress-nginx/application.yaml`) while each Kind cluster still exposes ingress on non-conflicting host ports.

## Production Safety Guard

If the context name includes `prod` or `production`, the script asks for explicit confirmation.
Type `prod` to continue, or pass `--yes` to skip the prompt.
