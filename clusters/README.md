# Cluster Helpers

## Management Cluster Setup

Provision the cluster first, then install Argo CD. Run from the repository root
as your normal user (not `sudo bash`); both distributions use the Kubernetes
context **`management`**. The scripts also work from other working directories.

For **kind**, install Docker, kind, kubectl, Helm, curl, and the Argo CD CLI:

```bash
bash clusters/setup-mgmt.sh kind
bash clusters/setup-argocd.sh
```

The provisioner uses [management/cluster.yaml](management/cluster.yaml), creates
the kind cluster `management`, and renames its generated `kind-management` context
to `management`. It refuses existing `management` or `kind-management` contexts
instead of overwriting them. For an existing kind management cluster, rename the
context manually after checking that it is the intended cluster:

```bash
kubectl config rename-context kind-management management
kubectl config use-context management
```

The Argo CD installer always targets `management`, regardless of the current
context. It honors `KUBECONFIG`, installs/upgrades the Helm release, waits for
readiness, and logs in without printing the initial admin password. By default
it configures a ClusterIP service and starts a localhost-only port-forward at
`https://localhost:8080`. It prints the forwarding PID and stop command; stop
that process before rerunning local-access setup. Forwarding is cleaned up if
setup fails. To restart local access later:

```bash
kubectl --context management -n argocd port-forward --address 127.0.0.1 svc/argocd-server 8080:443
```

Use `ARGOCD_CHART_VERSION` to pin an Argo CD chart version. Setup login requires
the initial admin secret; after changing/deleting that credential, log in
manually using your current credentials rather than rerunning setup for login.
This script does not provision Kubernetes clusters or bootstrap GitOps apps.

## Native K3s Management Cluster (LAN Access)

For native K3s instead of kind:

```bash
brew install kubectl helm argocd
bash clusters/setup-mgmt.sh k3s
bash clusters/setup-argocd.sh --nodeport
```

Run as your normal user on a **fresh Linux host** with systemd or OpenRC,
`sudo`, `curl`, `python3`, and `ip` (iproute2). WSL2 requires systemd and a LAN-reachable
network configuration, such as mirrored networking; this script does not
configure Windows networking or firewalls. Install `curl` for the Argo CD access
readiness check too. K3s alone needs at least 2 CPU cores
and 2 GB RAM; allow more for Argo CD and platform workloads.

### Why not install K3s with Homebrew?

The [official K3s installer](https://docs.k3s.io/quick-start) is the recommended
choice for native Linux: it installs the K3s binary, bundled containerd, and a
service that starts at boot. No Docker daemon or extra VM is needed. The
single-server default uses SQLite, avoiding the overhead of an HA etcd cluster.
Homebrew is useful for the kubectl, Helm and Argo CD **clients**; the provisioner leaves
existing Homebrew kubectl/client installations untouched.
[Homebrew's k3d package](https://formulae.brew.sh/formula/k3d) instead runs K3s
**inside Docker**, requiring additional port mappings for LAN access. It is
useful for disposable clusters, but is not a native K3s service.

The K3s provisioner downloads and runs `https://get.k3s.io` with elevated privileges.
Review the installer first if required by your host's security policy.

### Networking and Installation Behavior

- Detects the host's default-route IPv4 source address (`ip route get` does not
  send a probe). On multi-interface/VPN hosts, explicitly select the LAN IP.
- Binds the Kubernetes API to that address on TCP `6443`, advertises that IP,
  and includes it in the API certificate's SANs.
- With `setup-argocd.sh --nodeport`, exposes Argo CD persistently at
  `https://<LAN-IP>:30443` using NodePort,
  rather than a background port-forward. Argo CD uses a self-signed certificate;
  the initial CLI login uses `--insecure --grpc-web`.
  The LAN address is read from the `management` context's API endpoint.
  Omit `--nodeport` to use localhost forwarding instead, including on K3s.
- Does **not** change firewall rules. Allow TCP `6443` and `30443` only from
  trusted LAN clients; NodePorts may listen on other host interfaces too.
  The chart also creates an HTTP NodePort (normally `30080`); keep it blocked
  unless needed.
  These default NodePorts can conflict with the later platform gateway's
  fallback NodePorts (`30080`/`30443`). Use localhost mode or coordinate the
  service/chart ports before enabling that gateway configuration.
  Do not forward these ports from the public Internet. For additional nodes,
  follow the [K3s networking requirements](https://docs.k3s.io/installation/requirements#networking);
  never expose Flannel VXLAN UDP `8472` publicly.
- Bundled Traefik, ServiceLB, and metrics-server are currently enabled. Before
  bootstrapping the repository's replacement platform components, disable the
  corresponding bundled components to avoid conflicts.
- Creates only a management cluster. Native K3s is a host-level service, not
  multiple isolated kind clusters; use separate hosts/VMs for target clusters.
- Refuses existing K3s installations or an existing destination kubeconfig.
  A failed run can leave a running K3s service;
  use `--resume` to continue after the per-cluster kubeconfig has been created.

### Recovering a Failed Setup

If K3s installed successfully but provisioning stopped after the dedicated
kubeconfig was created, resume with:

```bash
bash clusters/setup-mgmt.sh k3s --resume
```

Use the same API IP, API port, and `KUBECONFIG_FILE` overrides as the original run.
Resume uses the existing per-cluster kubeconfig without reinstalling,
restarting, or reconfiguring K3s. It repairs the cluster, user, and context names
using a JSON transformation because kubectl has no `rename-cluster` or
`rename-user` commands. It accepts the legacy `default` and `k3s-management`
names as well as `management`. Install Argo CD separately after provisioning.
If Argo CD installation fails, rerun only `setup-argocd.sh` with the same options.

Optional overrides:

```bash
API_SERVER_ADDRESS=192.168.1.50 \
API_SERVER_PORT=6443 \
KUBECONFIG_FILE="$HOME/.kube/k3s-management.yaml" \
bash clusters/setup-mgmt.sh k3s

ARGOCD_HTTPS_NODE_PORT=31443 ARGOCD_CHART_VERSION=9.0.0 \
bash clusters/setup-argocd.sh --nodeport
```

`API_SERVER_ADDRESS` must be an IPv4 address assigned to this host, not
`0.0.0.0` or loopback. Reserve the LAN IP in DHCP or configure a static address.
The Argo CD HTTPS NodePort must be in `30000-32767`; the API port must be outside
that reserved range.
Use `K3S_VERSION` (an exact K3s release tag) on the provisioner to pin K3s;
otherwise the stable K3s channel is used. Without `ARGOCD_CHART_VERSION`, the
Argo CD installer uses the latest chart.

### Credentials and GitOps Follow-Up

The provisioner waits for node readiness; the separate Argo CD installer waits
for Argo CD and logs in with the initial admin password. Change the password with
`argocd account update-password`. Access the cluster with:

```bash
kubectl --context management get nodes
```

The separate kubeconfig is mode `600`. Its cluster, user, and context are all
named `management` to avoid collisions with existing `default` entries.
The script also merges it into `~/.kube/config`, preserving other named entries
and selecting `management` as the active context. Existing entries with that
same name are replaced by the K3s entries. The merge retains raw credentials,
and subsequent setup commands explicitly target `management`.
The invoking user's `$HOME` determines which default config is updated; do not
run the entire script through sudo. If `KUBECONFIG` points elsewhere, unset it
before the Argo CD step or select the dedicated file explicitly:

```bash
KUBECONFIG="$HOME/.kube/k3s-management.yaml" bash clusters/setup-argocd.sh --nodeport
```

Both files contain **cluster-admin credentials**. Transfer them
only to trusted LAN clients over a secure channel. K3s refreshes its original
`/etc/rancher/k3s/k3s.yaml` on startup, not this copy: refresh copied client
certificates before expiry, retaining the LAN server URL and context name.

Neither setup script bootstraps the platform applications. Before doing so:

1. For native K3s, replace the management MetalLB pool with unused addresses on
   your **physical LAN**, outside DHCP allocations. Do not use the Docker-network
   pool generator below for native K3s.
2. The existing bootstrap helper prefixes contexts with `kind-`. It cannot
   target the renamed `management` context for **either kind or K3s**. Apply
   AppProjects and the management root Application directly:

   ```bash
   kubectl --context management apply -k projects
   kubectl patch --local -f bootstrap/root-app.yaml --type merge \
     -p '{"spec":{"source":{"path":"bootstrap/apps/management"}}}' \
     -o yaml | kubectl --context management apply -f -
   ```

   The local patch selects the management source path without changing the
   checked-in root Application.

The official `/usr/local/bin/k3s-uninstall.sh` removes the native cluster and
local cluster data. Use it **only if you intend to destroy this installation**.

Run the mocked setup tests (no cluster or root access required):

```bash
python3 -m unittest discover -s clusters/tests -p 'test_*.py'
```

## Command

For other clusters retaining their `kind-` context prefix, run from repository
root. For management, use the manual commands above.

- `./bootstrap/bootstrap-cluster.sh <kube-context>`
- `./bootstrap/bootstrap-cluster.sh <kube-context> --yes`

Examples:

- `./bootstrap/bootstrap-cluster.sh dev`
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
- `3`: gateway-api CRDs, metallb, metrics-server
- `4`: nginx-gateway
- `5`: kyverno
- `6`: external-secrets
- `7`: headlamp, kube-prometheus-stack, policy-reporter
- `8`: loki, platform-routes
- `9`: inspektor-gadget

Gateway route manifests are managed by the `platform-routes` application:

- base: `platform/routes/base/`
- management: `platform/routes/management/`
- dev: `platform/routes/dev/`
- staging: `platform/routes/staging/`
- prod: `platform/routes/prod/`

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
kubectl -n argocd get app nginx-gateway platform-routes kube-prometheus-stack headlamp kyverno policy-reporter
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
