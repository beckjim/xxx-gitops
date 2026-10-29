# Cluster Helpers

For infrastructure selection, coexistence, and the deployment-blocked K3s LAN
overlay, see [Cluster Profiles](PROFILES.md). Existing environment overlays are
preserved; new clusters use explicit IDs and contexts.

## Management Cluster Setup

Provision the cluster first, then install Argo CD. Run from the repository root
as your normal user (not `sudo bash`). Default contexts are
**`management-kind`** and **`management-k3s`**, respectively. Supply a second
argument to choose a unique cluster ID. The scripts work from other directories.

For **kind**, install Docker, kind, kubectl, Python 3, Helm, curl, and the Argo CD CLI:

```bash
bash clusters/setup-mgmt.sh kind
bash clusters/setup-argocd.sh --context management-kind
```

The provisioner uses [management/cluster.yaml](management/cluster.yaml), creates
the kind cluster using the chosen ID, and renames its generated `kind-<id>` context
to that ID. It refuses existing target contexts or destination kubeconfigs
instead of overwriting them. For an older kind management cluster, rename the
context manually after checking that it is the intended cluster:

```bash
kubectl config rename-context kind-management management
kubectl config use-context management
```

The Argo CD installer targets `--context` (legacy default `management`),
regardless of the current context. It honors `KUBECONFIG`, installs/upgrades the Helm release, waits for
readiness, and logs in without printing the initial admin password. On kind,
it defaults to a ClusterIP service and a localhost-only port-forward at
`https://localhost:8080`. It prints the forwarding PID and stop command; stop
that process before rerunning local-access setup. Forwarding is cleaned up if
setup fails. To restart local access later:

```bash
kubectl --context management-kind -n argocd port-forward --address 127.0.0.1 svc/argocd-server 8080:443
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
bash clusters/setup-argocd.sh --context management-k3s
```

Run as your normal user on a **fresh Linux host** with systemd or OpenRC,
`sudo`, `curl`, `python3`, `hostname`, `getent`, and `ip` (iproute2).
WSL2 requires systemd and a LAN-reachable
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
  and includes both the IP and optional FQDN in the API certificate's SANs.
  The kubeconfig keeps the IP endpoint so it also works without DNS.
- Argo CD setup detects K3s from the nodes' kubelet versions and exposes Argo CD persistently at
  `https://<LAN-IP>:31443` using NodePort,
  rather than a background port-forward. Argo CD uses a self-signed certificate;
  the initial CLI login uses `--insecure --grpc-web`.
  The LAN address is read from the selected context's API endpoint.
  Use `--local` to force localhost forwarding, including on K3s, or
  `--nodeport` to force LAN access without distribution detection.
  Automatic detection requires permission to list nodes.
- Does **not** change firewall rules. Allow TCP `6443` and `31443` only from
  trusted LAN clients; NodePorts may listen on other host interfaces too.
  The script also configures an HTTP NodePort (`31080`); keep it blocked
  unless needed.
  These ports are separate from the platform gateway's fallback NodePorts
  (`30080`/`30443`). Check for conflicts before overriding any of them.
  Do not forward these ports from the public Internet. For additional nodes,
  follow the [K3s networking requirements](https://docs.k3s.io/installation/requirements#networking);
  never expose Flannel VXLAN UDP `8472` publicly.
- Bundled Traefik, ServiceLB, and metrics-server are disabled at fresh installation
  so the GitOps-managed replacements have one owner. Flannel, CoreDNS, network
  policy and local-path storage remain enabled.
- Creates only a management cluster. Native K3s is a host-level service, not
  multiple isolated kind clusters; use separate hosts/VMs for target clusters.
- Refuses existing K3s installations or an existing destination kubeconfig.

### LAN IP and DNS Access

When their FQDN override is empty, both scripts try `hostname -f`, reverse
lookup of the selected LAN IP, canonical names/aliases returned by resolving the
short hostname, and the short hostname combined with `hostname -d` and DNS
`search`/`domain` suffixes from `/etc/resolv.conf`, in that order. This also handles
hosts where `hostname -f` returns only a short name such as `k8s`.
They use the first candidate that is a valid FQDN and all returned IPv4
addresses resolve to the selected LAN IP. Otherwise, they warn and retain IP-only
access. An explicitly configured name that fails validation stops setup before
installation. Discovery runs on the machine executing the script; when installing
Argo CD from another machine, set `ARGOCD_FQDN` explicitly.

For separate API and Argo CD names, create LAN DNS A records pointing to the
K3s host's reserved IP (or equivalent hosts-file entries on **every client**):

```bash
API_SERVER_ADDRESS=192.168.1.50 API_SERVER_FQDN=api.home.arpa \
  bash clusters/setup-mgmt.sh k3s
ARGOCD_FQDN=argocd.home.arpa bash clusters/setup-argocd.sh --context management-k3s
```

The API is accessible through both `https://192.168.1.50:6443` and
`https://api.home.arpa:6443`, with certificate verification using the kubeconfig's
CA. To use DNS without modifying the saved IP endpoint:

```bash
kubectl --context management-k3s --server=https://api.home.arpa:6443 get nodes
```

Argo CD is accessible through both `https://192.168.1.50:31443` and
`https://argocd.home.arpa:31443`. Its configured external URL uses the FQDN when
available. Argo CD still uses its default self-signed certificate: expect browser
trust/name warnings and use `--insecure` for CLI access until you provision a
trusted `argocd-server-tls` certificate with both DNS and IP SANs. The setup scripts
do not install a CA; the configured K3s platform overlay supplies an internal CA
and Argo CD Certificate. See [Cluster Profiles](PROFILES.md#tls-and-ip-access).

Setup checks authenticated API readiness through the IP and selected FQDN and
Argo CD health through both URLs. These are checks from the setup machine,
**not proof of reachability from another LAN client**. Verify from a separate
client with the kubeconfig and `curl --insecure --fail https://argocd.home.arpa:31443/healthz`
(also test the IP URL). LAN DNS, routing, and firewall rules must permit access;
the scripts do not configure them. Other platform services remain unchanged.

### Recovering a Failed Setup

There is no resume mode. If K3s installation succeeds but a later setup step
fails, inspect the service and kubeconfig state and recover manually; the script
will refuse to overwrite an existing installation or destination kubeconfig.
For a fresh reinstall, use the official K3s uninstall script only if you intend
to destroy the cluster, and remove the generated destination kubeconfig only
after verifying that it belongs to this setup. If only Argo CD setup fails,
rerun `setup-argocd.sh` with the same options.

Optional overrides:

```bash
API_SERVER_ADDRESS=192.168.1.50 \
API_SERVER_PORT=6443 \
KUBECONFIG_FILE="$HOME/.kube/management-k3s.yaml" \
bash clusters/setup-mgmt.sh k3s

ARGOCD_HTTPS_NODE_PORT=32443 ARGOCD_CHART_VERSION=9.0.0 \
bash clusters/setup-argocd.sh --context management-k3s --nodeport
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
kubectl --context management-k3s get nodes
```

The separate kubeconfig is mode `600`. Its cluster, user, and context are all
named after the cluster ID to avoid collisions with existing `default` entries.
The script also merges it into `~/.kube/config`, preserving other named entries
without changing an existing active context. Existing entries with the same
identity are rejected, not replaced. The merge retains raw credentials.
Subsequent setup commands must select the target context explicitly.
The invoking user's `$HOME` determines which default config is updated; do not
run the entire script through sudo. If `KUBECONFIG` points elsewhere, unset it
before the Argo CD step or select the dedicated file explicitly:

```bash
KUBECONFIG="$HOME/.kube/management-k3s.yaml" \
  bash clusters/setup-argocd.sh --context management-k3s --nodeport
```

Both files contain **cluster-admin credentials**. Transfer them
only to trusted LAN clients over a secure channel. K3s refreshes its original
`/etc/rancher/k3s/k3s.yaml` on startup, not this copy: refresh copied client
certificates before expiry, retaining the LAN server URL and context name.

Neither setup script bootstraps platform applications. Configure the concrete
cluster overlay first, then use `bash bootstrap/bootstrap-cluster.sh management-k3s`
or `management-kind`. The K3s profile requires a reserved **physical LAN** MetalLB
pool, not the Docker-network pool generator below. Follow
[Cluster Profiles](PROFILES.md) for validation, TLS and staged rollout.

The official `/usr/local/bin/k3s-uninstall.sh` removes the native cluster and
local cluster data. Use it **only if you intend to destroy this installation**.

Run the mocked setup tests (no cluster or root access required):

```bash
python3 -m unittest discover -s clusters/tests -p 'test_*.py'
```

## Command

For legacy clusters retaining their `kind-` context prefix, run from repository
root. For new management clusters, use the explicit profile flow above.

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
