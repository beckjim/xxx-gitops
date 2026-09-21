# Cluster Helpers

Use a single helper script for all environments.

## Command

Run from repository root:

- `./bootstrap/bootstrap-cluster.sh <kube-context>`
- `./bootstrap/bootstrap-cluster.sh <kube-context> --yes`

Examples:

- `./bootstrap/bootstrap-cluster.sh dev`
- `./bootstrap/bootstrap-cluster.sh management`
- `./bootstrap/bootstrap-cluster.sh test`
- `./bootstrap/bootstrap-cluster.sh prod`

For automation/non-interactive runs, use:

- `./bootstrap/bootstrap-cluster.sh prod --yes`

The script applies:

1. `projects/` (AppProjects)
2. `bootstrap-root` Application (cluster-specific source path when available)

## Cluster-Specific Bootstrap Paths

If `bootstrap/apps/<kube-context>/` exists, the bootstrap script uses it automatically.
Otherwise it falls back to `bootstrap/apps/`.

Examples:

- `./bootstrap/bootstrap-cluster.sh dev` uses `bootstrap/apps/dev`
- `./bootstrap/bootstrap-cluster.sh prod` uses `bootstrap/apps/prod`
- `./bootstrap/bootstrap-cluster.sh test` falls back to `bootstrap/apps`

## MetalLB Gateway Convention

Note: checkout https://oneuptime.com/blog/post/2026-02-20-metallb-kind-local-development/view for general usage on Windows/WSL2/Docker and kind.

For clusters using `bootstrap/apps/{management,dev,staging,prod}`:

- nginx-gateway is configured as `LoadBalancer`
- MetalLB is installed as a platform component
- each cluster gets its own non-overlapping MetalLB `IPAddressPool`
- Kind `extraPortMappings` for gateway NodePorts (`30080`/`30443`) are not required

Pool ranges are generated from the Docker network subnet.

From repository root:

```bash
./platform/metallb-config/update-from-docker-network.sh kind
```

This script inspects the network subnet (for example with
`docker network inspect kind -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}'`)
and rewrites the following files:

- `platform/metallb-config/management/pool.yaml` (slice `.10-.19`)
- `platform/metallb-config/dev/pool.yaml` (slice `.20-.29`)
- `platform/metallb-config/staging/pool.yaml` (slice `.30-.39`)
- `platform/metallb-config/prod/pool.yaml` (slice `.40-.49`)

Commit and push these changes so Argo CD can apply them.

Legacy/fallback path (`bootstrap/apps`) still uses NodePort gateway settings.

## Platform Sync Wave Matrix

Current Argo CD sync-wave order for platform applications:

- `1`: cert-manager
- `2`: kubescape
- `3`: gateway-api-crds, metallb, metrics-server
- `4`: nginx-gateway
- `5`: kyverno
- `6`: external-secrets
- `7`: headlamp, kube-prometheus-stack, policy-reporter
- `8`: loki
- `9`: inspektor-gadget

Gateway route manifests for UI apps are colocated in each app folder and use wave `8`:

- `platform/headlamp/httproute.yaml`
- `platform/kube-prometheus-stack/httproute.yaml`
- `platform/policy-reporter/httproute.yaml`

This keeps route creation after nginx-gateway (`4`) and after app declarations (`7`).

## Platform App Access via Gateway

Three platform UIs are exposed through Gateway API HTTPRoutes:

- `platform.<cluster>.local/grafana` -> Grafana (`monitoring/kube-prometheus-stack-grafana`)
- `platform.<cluster>.local/headlamp` -> Headlamp (`headlamp/headlamp`)
- `platform.<cluster>.local/policy-reporter` -> Policy Reporter UI (`kyverno/policy-reporter-ui`)

Get the gateway endpoint:

```bash
kubectl -n nginx-gateway get svc
```

If nginx-gateway is `LoadBalancer`, add entries to `/etc/hosts` using the `EXTERNAL-IP`:

```bash
<EXTERNAL-IP> platform.management.local
<EXTERNAL-IP> platform.dev.local
<EXTERNAL-IP> platform.staging.local
<EXTERNAL-IP> platform.prod.local
```

Then open:

- `http://platform.management.local/grafana`
- `http://platform.management.local/headlamp`
- `http://platform.management.local/policy-reporter`

Root path behavior:

- `http://platform.<cluster>.local` redirects to `http://platform.<cluster>.local/grafana`

If you are on the NodePort fallback path (`bootstrap/apps`), use the mapped host port
for gateway and send the Host header (or browser host mapping) for
`platform.<cluster>.local`.

## Verify Platform Gateway

Run these checks after Argo CD sync:

```bash
kubectl -n argocd get app gateway-api-crds nginx-gateway kube-prometheus-stack headlamp kyverno policy-reporter
kubectl -n nginx-gateway get gateway platform-gateway
kubectl -n nginx-gateway get svc
kubectl -n monitoring get svc kube-prometheus-stack-grafana
kubectl -n headlamp get svc headlamp
kubectl -n kyverno get svc policy-reporter-ui
kubectl get httproute -A
```

Quick HTTP checks (replace `<EXTERNAL-IP>` if you are not using `/etc/hosts`):

```bash
curl -sSI -H 'Host: platform.management.local' http://<EXTERNAL-IP>/grafana | head -n 1
curl -sSI -H 'Host: platform.management.local' http://<EXTERNAL-IP>/headlamp | head -n 1
curl -sSI -H 'Host: platform.management.local' http://<EXTERNAL-IP>/policy-reporter | head -n 1
```

Expected result: `HTTP/1.1 200 OK` or `HTTP/1.1 302 Found`.

## NodePort Fallback Convention

For fallback path users (`bootstrap/apps`), gateway remains NodePort-based:

- Keep nginx-gateway NodePorts fixed to `30080` (HTTP) and `30443` (HTTPS).
- In each cluster config, map those fixed `containerPort` values to unique `hostPort` values.

Example mapping in a cluster config:

- `containerPort: 30080` -> `hostPort: 30082`
- `containerPort: 30443` -> `hostPort: 30445`

This allows one shared Argo CD app (`platform/nginx-gateway/application.yaml`) while each Kind cluster still exposes gateway traffic on non-conflicting host ports.

## Production Safety Guard

If the context name includes `prod` or `production`, the script asks for explicit confirmation.
Type `prod` to continue, or pass `--yes` to skip the prompt.
