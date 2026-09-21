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

## Platform Sync Wave Matrix

Current Argo CD sync-wave order for platform applications:

- `1`: cert-manager
- `2`: kubescape
- `3`: metallb, metrics-server
- `4`: ingress-nginx
- `5`: kyverno
- `6`: external-secrets
- `7`: headlamp, kube-prometheus-stack, policy-reporter
- `8`: loki
- `9`: inspektor-gadget

Ingress manifests for UI apps are colocated in each app folder and use wave `8`:

- `platform/headlamp/ingress.yaml`
- `platform/kube-prometheus-stack/ingress.yaml`
- `platform/policy-reporter/ingress.yaml`

This keeps ingress creation after ingress-nginx (`4`) and after app declarations (`7`).

## Platform App Access via Ingress

Three platform UIs are exposed through ingress-nginx:

- `platform.local/grafana` -> Grafana (`monitoring/kube-prometheus-stack-grafana`)
- `platform.local/headlamp` -> Headlamp (`headlamp/headlamp`)
- `platform.local/policy-reporter` -> Policy Reporter UI (`kyverno/policy-reporter-ui`)

Get the ingress endpoint:

```bash
kubectl -n ingress-nginx get svc ingress-nginx-controller
```

If ingress-nginx is `LoadBalancer`, add entries to `/etc/hosts` using the `EXTERNAL-IP`:

```bash
<EXTERNAL-IP> platform.local
```

Then open:

- `http://platform.local/grafana`
- `http://platform.local/headlamp`
- `http://platform.local/policy-reporter`

Root path behavior:

- `http://platform.local` redirects to `http://platform.local/grafana`

If you are on the NodePort fallback path (`bootstrap/apps`), use the mapped host port
for ingress and send the Host header (or browser host mapping) for `platform.local`.

## Verify Platform Ingress

Run these checks after Argo CD sync:

```bash
kubectl -n argocd get app ingress-nginx kube-prometheus-stack headlamp kyverno policy-reporter
kubectl -n ingress-nginx get svc ingress-nginx-controller
kubectl -n monitoring get svc kube-prometheus-stack-grafana
kubectl -n headlamp get svc headlamp
kubectl -n kyverno get svc policy-reporter-ui
kubectl get ingress -A
```

Quick HTTP checks (replace `<EXTERNAL-IP>` if you are not using `/etc/hosts`):

```bash
curl -sSI -H 'Host: platform.local' http://<EXTERNAL-IP>/grafana | head -n 1
curl -sSI -H 'Host: platform.local' http://<EXTERNAL-IP>/headlamp | head -n 1
curl -sSI -H 'Host: platform.local' http://<EXTERNAL-IP>/policy-reporter | head -n 1
```

Expected result: `HTTP/1.1 200 OK` or `HTTP/1.1 302 Found`.

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
