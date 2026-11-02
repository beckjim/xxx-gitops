# Cluster profiles: kind and native K3s

Cluster identity, environment and infrastructure are separate. The checked-in
examples are `management-kind` (environment `management`, profile `kind`) and
`management-k3s` (environment `management`, profile `k3s-lan`). Each has its own
kubeconfig, context, bootstrap root and platform overlay.

## Composition and ownership

- `platform/base` selects the shared Applications and Gateway API CRDs.
- `platform/profiles/kind` adds MetalLB; existing environment entrypoints retain
  their prior configuration through the legacy `platform` entrypoint. The new
  `management-kind` overlay also adds Alloy and explicitly selects Loki's
  single-binary mode/internal datasource without changing legacy environments.
- `platform/profiles/k3s-lan` adds MetalLB and Alloy, plus K3s metrics, runtime,
  monitoring and persistence parameters.
- `platform-overlays/<cluster-id>` supplies concrete per-cluster settings.
  The `management-k3s` overlay additionally selects NFD and the Intel GPU device
  plugin; neither is added to the shared base or other cluster overlays.
- `bootstrap/apps/<cluster-id>` selects that exact overlay.
- `clusters/<cluster-id>/settings.json` records identity and expected network
  settings. Validation checks these against the actual manifests.

All existing add-ons remain mandatory. K3s installation disables **Traefik,
ServiceLB and bundled metrics-server** so GitOps owns their replacements:
NGINX Gateway, MetalLB and metrics-server. Keep Flannel, CoreDNS, the network
policy controller, built-in cloud controller and local-path provisioner.

Do not install two owners for the metrics API, load balancer or Gateway CRDs.
There is no need to add another CNI or a distributed storage operator for this
single-node profile.

## Configure before deploying

The K3s overlay is deliberately a **deployment-blocked template**. No LAN pool,
gateway address or DNS names are assumed. Replace `REPLACE_ME` in these files:

1. `clusters/management-k3s/settings.json`
2. `platform-overlays/management-k3s/cluster.yaml`
3. `platform/metallb-config/management-k3s/pool.yaml`
4. `platform/routes/management-k3s/certificates.yaml`

Reserve the MetalLB range on the physical LAN, outside DHCP, excluding node IPs
and other clusters' pools. Select the gateway IP from that range. DNS records:

- Platform FQDN -> gateway MetalLB IP.
- API FQDN and Argo CD FQDN -> K3s node IP.

Use your LAN DNS zone (for example `home.arpa`), not unicast `.local` names.
Confirm L2 reachability, DHCP exclusions and firewall policy separately;
an unused-looking IP is not evidence that it is safe to allocate.

Install the validation/test dependency in your Python environment:

```bash
python3 -m pip install -r clusters/requirements.txt
python3 clusters/validate-profile.py management-k3s
```

Validation renders the platform, pool and routes, rejects placeholders, checks
identity, pool membership/exclusion, gateway address and certificate SANs.
It does not configure DNS, reserve DHCP leases or prove LAN-client reachability.
Kustomize requires network access to the pinned Gateway API dependencies.
Commit and push the reviewed manifests to the bootstrap Applications' target
revision (currently `main`) before bootstrap: local validation does not make
uncommitted files available to Argo CD.

Before K3s bootstrap, create the `monitoring` namespace and a `grafana-admin`
Secret with `admin-user` and `admin-password` using your trusted secret tooling
and explicit cluster context. The LAN profile references that Secret instead of
the legacy `admin` password; bootstrap checks it exists and is not the default.
Do not commit either credential. An ExternalSecret can own it later once your
secrets provider is configured.

## Fresh provisioning and bootstrap

```bash
# Disposable Docker cluster; context and kubeconfig identity: management-kind.
bash clusters/setup-mgmt.sh kind management-kind
bash clusters/setup-argocd.sh --context management-kind --local
bash bootstrap/bootstrap-cluster.sh management-kind

# Separate fresh Linux host for native K3s.
# Supply the node IP and API FQDN selected for this cluster.
API_SERVER_ADDRESS=<node-ip> API_SERVER_FQDN=<api-fqdn> \
  bash clusters/setup-mgmt.sh k3s management-k3s
ARGOCD_FQDN=<argocd-fqdn> \
  bash clusters/setup-argocd.sh --context management-k3s
bash bootstrap/bootstrap-cluster.sh management-k3s
```

The provisioner defaults to `management-kind` or `management-k3s` if no cluster ID
is supplied. `CLUSTER_ID` can override that default; the positional ID wins.
Dedicated kubeconfigs default to `~/.kube/<cluster-id>.yaml`. Setup merges without
replacing existing identities or changing an existing current context.
The Argo CD helper accepts `--context`; its old `management` default remains for
compatibility with already provisioned installations. Use explicit contexts for
all new clusters.

Bootstrap uses the selected cluster's exact source path and context, with no
generic-source fallback. K3s bootstrap rejects the wrong distribution or
remaining overlapping packaged AddOns. Do not bypass these checks by applying
the template root manually. Native K3s remains one installation per host, not
multiple native clusters side by side.

Argo CD bootstrap ports are **31080 HTTP / 31443 HTTPS**. The gateway's optional
NodePorts remain **30080 / 30443**. Default NodePort allocations do not conflict.
Use `ARGOCD_HTTP_NODE_PORT` and `ARGOCD_HTTPS_NODE_PORT` only after checking all
other Service allocations. Keep HTTP blocked unless deliberately needed.

For day-to-day switching, select a context:

```bash
kubectl --context management-kind get nodes
kubectl --context management-k3s get nodes
# Or explicitly change your interactive default:
kubectl config use-context management-k3s
```

Scripts target a context explicitly instead of changing the global default.
Local Argo CD port-forwards still share localhost port 8080: stop one before
starting another or manually use another local port.

## TLS and IP access

The K3s routes use HTTPS port 443 and hostname-independent path routing for
Grafana (`/grafana`), Headlamp (`/headlamp`) and Policy Reporter
(`/policy-reporter`). Both the platform FQDN and gateway IP are Certificate SANs;
the root path redirects to `/grafana` while retaining the requested host.
There is deliberately no plaintext HTTP listener on this profile.

The example creates an **internal CA** through cert-manager and a trust-manager
Bundle. No private keys are committed. The Bundle distributes the CA certificate
only to namespaces labeled `platform-ca-trust=true`; applications must still be
configured to use that bundle. Export the **public** CA certificate securely and
install it in each LAN client's trust store before trusting the endpoints.
trust-manager cannot install trust in your browser or operating system.

A separate Certificate supplies `argocd-server-tls` with the Argo CD FQDN and
node IP SANs. Argo CD serves it when available; initial bootstrap still uses its
self-signed certificate. The Kubernetes API retains its own K3s CA, not this CA.
Back up the internal CA key securely, plan CA rotation/client trust updates, and
do not assume renewing the CA Certificate rotates all dependent certificates.
Replace the example issuer references deliberately if using an existing issuer.
ACME HTTP-01 cannot issue these private IP SANs.

## Full-stack runtime and storage

The K3s profile retains all add-ons, not just a minimal K3s footprint:

- Prometheus: one replica, 10Gi local-path PVC, 7-day / 8GB retention.
- Grafana: 1Gi local-path PVC.
- Loki: single binary, 10Gi local-path PVC, explicit TSDB v13 schema.
- Alloy: DaemonSet tailing this node's `/var/log/pods` CRI logs into Loki.
- Loki is internal ClusterIP port 3100, not an unauthenticated LAN NodePort.
- SQLite has no etcd metrics. Scheduler/controller-manager/kube-proxy monitors
  and their corresponding rules are disabled initially because K3s does not
  expose the upstream chart's default endpoints. Their actual services are not
  disabled. Enable profile-specific monitors only after validating reachable,
  authenticated endpoints; API server, kubelet, node and workload metrics remain.
- Gadget uses `/run/k3s/containerd/containerd.sock`. Kubescape retains its
  chart's `/run` host mount and uses the K3s-specific **runc binary** at
  `/host/var/lib/rancher/k3s/data/current/bin/runc`, not a socket as runtime path.
- Kyverno reports-server's PostgreSQL container uses AppArmor `Unconfined` in
  this profile to work around Ubuntu/containerd AppArmor stacking denying
  signals between PostgreSQL processes. Symptoms include kernel audit
  `operation="signal"` denials and `could not signal for checkpoint: Permission
  denied` during recovery. This removes AppArmor confinement only for that
  container; non-root execution, dropped capabilities, disabled privilege
  escalation and RuntimeDefault seccomp remain enabled. Other profiles and
  containers retain their defaults. Revisit this exception after the host/runtime
  issue is fixed. Do not delete the database PVC or reset WAL to address these
  signal denials. If an unhealthy existing Pod blocks the StatefulSet rollout,
  sync the updated template first, then recreate only `kyverno-postgresql-0`
  in namespace `kyverno`, retaining its PVC.
- K3s metrics-server does not inherit kind's `--kubelet-insecure-tls`. Confirm
  kubelet certificate/SAN trust; if your host configuration differs, fix trust
  or add an explicit, reviewed profile exception rather than weakening all
  distributions.
- The generated NGINX Gateway data-plane container also uses AppArmor
  `Unconfined` in this profile, through the chart's `nginx.patches` setting.
  Ubuntu/containerd AppArmor stacking can block the NGINX agent's process
  inspection and reload signals, leaving the Gateway `Programmed=False` with
  `failed to reload NGINX permission denied`. The exception applies only to
  the data-plane `nginx` container, not the Gateway controller. Non-root
  execution, read-only root filesystem, dropped capabilities, disabled privilege
  escalation and RuntimeDefault seccomp remain enabled. Revisit this exception
  after the host/runtime issue is fixed; do not disable AppArmor globally.
  If the new data-plane Pod is Ready and serves traffic but the Gateway retains
  the old reload error, restart the `nginx-gateway-nginx-gateway-fabric`
  controller Deployment in namespace `nginx-gateway` and verify
  `Programmed=True`.

- MetalLB disables the unused FRR-K8s BGP backend (`frrk8s.enabled=false`) in
  this L2-only profile. This removes its failing reload loop without another
  AppArmor exception. IP pools and L2 advertisement remain unchanged. Enabling
  BGP later requires explicitly re-enabling/configuring its backend and resolving
  the host/runtime signal denials first.
- Policy Reporter's main container uses AppArmor `Unconfined` after observed
  denials of its runtime's internal signals. Its UI and other containers retain
  their defaults; non-root execution, read-only root filesystem, dropped
  capabilities and RuntimeDefault seccomp remain enabled. As with the exceptions
  above, revisit this after fixing the host/runtime AppArmor stacking issue.

Resource requests are initial single-node budgets, not proof the entire stack
fits. Inspect allocatable memory/CPU, Kubescape's chart defaults, disk capacity,
host kernel/BTF support and privileged workload policies before rollout.
The full stack needs substantially more than the minimum memory for K3s alone.
External Secrets still requires your own SecretStore/provider configuration
and credentials; installing its operator does not supply a secrets backend.

Local-path data is tied to this node and is not replicated or highly available.
Loki's filesystem has no object-store lifecycle policy here; monitor disk growth
and configure a tested retention/deletion policy before sustained ingestion.
Alloy positions default to ephemeral storage and restarts can reread recent
files; its log collection is not a guaranteed exactly-once archival pipeline.

## Headlamp desktop and Inspektor Gadget

The official Headlamp Inspektor Gadget plugin currently hardcodes the daemon
port-forward endpoint to namespace `gadget`. The shared Inspektor Gadget
Application therefore deploys to `gadget` and enables namespace creation. This
applies to all profiles, not just native K3s. Headlamp's own namespace and
desktop installation do not need to change.

An older deployment in `kube-system` can appear healthy in Argo CD while the
desktop plugin spins after Create/Run: it tries to connect to the right Pod
name in the wrong namespace. Confirm a failing
`/api/v1/namespaces/gadget/pods/<pod>/portforward?ports=8080` request in
Headlamp's developer tools. The plugin also needs permission to list nodes/Pods
and create `pods/portforward` in `gadget`; do not grant cluster-admin merely
to work around a namespace mismatch.

For an existing cluster, schedule a short interruption of gadget sessions:

1. Stop active gadgets and back up any user-created ConfigMaps labeled
   `type=gadget-instance` in `kube-system`. Do not delete the namespace.
2. Commit/push the namespace change and sync the parent platform Application,
   then `inspektor-gadget` with pruning. The old namespaced resources must be
   removed; do not leave two gadget DaemonSets running on the same nodes.
   The chart's cluster-scoped RBAC bindings must now reference the `gadget`
   ServiceAccount in namespace `gadget`.
3. Check that the old DaemonSet is gone and the new one is ready, then reopen
   Headlamp and retry Create/Run:

```bash
kubectl --context management-k3s -n kube-system get daemonset gadget --ignore-not-found
kubectl --context management-k3s -n gadget rollout status daemonset/gadget --timeout=120s
kubectl --context management-k3s -n gadget logs daemonset/gadget --since=5m --tail=100
kubectl --context management-k3s auth can-i create pods/portforward -n gadget
```

The first command should return no resources. After reconnecting, confirm the
UI receives gadget metadata/events, not just that the daemon is Ready.
If image pulls fail, inspect daemon logs for registry/DNS/signature errors;
namespace migration does not resolve those. Keep image verification enabled.
The default trace uses `ghcr.io/inspektor-gadget/gadget/trace_open:v0.57.0`,
matching the daemon release. The `/gadget/` repository segment is required;
`ghcr.io/inspektor-gadget/trace_open:latest` returns a registry access denial.
Restore user-created gadget ConfigMaps into `gadget` only after reviewing their
configuration; GitOps recreates the chart-managed default instance.

Plugin connection implementation:
[official Headlamp plugin](https://github.com/inspektor-gadget/headlamp-plugin/blob/main/src/gadgets/conn.tsx).

## Intel GPU allocation with NFD (management-k3s only)

The native K3s overlay adds two Argo CD Applications:

- `node-feature-discovery`: official NFD Git chart at `v0.20.1`, namespace
  `node-feature-discovery`, sync wave 2.
- `intel-gpu-plugin`: standalone Intel device-plugin manifests at `v0.37.1`,
  namespace `intel-device-plugins`, sync wave 3. The AMD64 image is digest-pinned.

The Intel device-plugin operator is not installed. NFD publishes PCI/kernel
features; a cluster-scoped GPU-only `NodeFeatureRule` matches Intel display
devices (vendor `8086`, classes `0300`/`0380`) with `i915` or `xe` enabled.
It produces `intel.feature.node.kubernetes.io/gpu=true`, which selects the
Linux/AMD64 GPU DaemonSet. Do not label nodes manually. The rule has no namespace
and is applied before the DaemonSet inside the GPU Application. Existing nested
Application health handling must be enabled so NFD, including its CRDs, is ready
before the GPU child syncs.

Discovery is limited to PCI/kernel sources. NFD node tainting, topology updater
and the Helm post-delete cleanup hook are disabled; the NFD garbage collector
remains enabled. The custom Intel label namespace is explicitly allowed.
NFD containers retain non-root, read-only filesystem, dropped-capability and
RuntimeDefault seccomp settings. No API credentials are mounted in the GPU
plugin Pod. The plugin itself runs as root for host device/socket access, but
not privileged; its root filesystem is read-only and privilege escalation is
disabled. Required hostPath access is an infrastructure exception, not permission
to grant workload Pods privileged mode. No broad admission-policy or AppArmor
exceptions are introduced.

### Host prerequisites and rollout gate

Read-only inspection of the current native host found an Intel **HD Graphics
520 (Skylake-U, `8086:1916`)**, using the upstream `i915` driver, with
`/dev/dri/card1` and `/dev/dri/renderD128`. Do not assume `card0` exists.
The host runs Ubuntu 26.04.1 / Linux 7.0.0-42-generic; K3s reports Kubernetes
v1.37.1+k3s1 and containerd 2.3.4-k3s1. Discovery/registration on this combination
still needs post-deployment verification; the manifests do not install firmware
or kernel/user-space GPU drivers.

**Before deploying**, an administrator must confirm the real kubelet
device-plugin socket path on the host. The directory is root-protected and was
not inspectable by this session:

```bash
sudo test -S /var/lib/kubelet/device-plugins/kubelet.sock
sudo ls -l /var/lib/kubelet/device-plugins/kubelet.sock
```

The first command must succeed. If K3s uses a custom kubelet root, confirm it
from the service's kubelet arguments and update the `kubeletsockets` hostPath in
`platform/intel-gpu-plugin/resources/daemonset.yaml` before syncing. The plugin's
container-side registration path remains `/var/lib/kubelet/device-plugins`.
Never create an empty host directory or relax host permissions to hide a wrong
socket path. DRM/sysfs/kubelet volumes deliberately require existing directories.

Apply the updated AppProject through its existing bootstrap ownership before
syncing, so it allows the official NFD Git chart source. Commit/push the reviewed
configuration to the Applications' tracked revision through the normal workflow;
local files are not visible to Argo CD. Do not install a second NFD instance.

### Verify after Argo CD deployment

These are read-only checks, using the intended context explicitly:

```bash
kubectl --context management-k3s -n argocd get applications node-feature-discovery intel-gpu-plugin
kubectl --context management-k3s -n node-feature-discovery get deployments,daemonsets,pods
kubectl --context management-k3s get nodefeaturerule intel-gpu
kubectl --context management-k3s get nodes \
  -L intel.feature.node.kubernetes.io/gpu
kubectl --context management-k3s -n intel-device-plugins get daemonset intel-gpu-plugin
kubectl --context management-k3s -n intel-device-plugins logs daemonset/intel-gpu-plugin --tail=100
kubectl --context management-k3s get node management-k3s \
  -o jsonpath='{.status.capacity.gpu\.intel\.com/i915}{" "}{.status.allocatable.gpu\.intel\.com/i915}{"\n"}'
```

Expect both Applications `Synced/Healthy`, healthy NFD workloads, the Intel label
`true`, and **one desired/ready GPU plugin Pod** on this host. A DaemonSet showing
zero desired Pods is not a successful GPU installation. Capacity and allocatable
should both be **1** for `gpu.intel.com/i915`; `xe` hardware would instead expose
`gpu.intel.com/xe`. Allocatable is the total schedulable inventory, not the current
number of free allocations; inspect allocated resources/Pods for current usage.
Allow an NFD scan and kubelet registration interval before diagnosing missing
resources.

If labels are absent, inspect NFD worker/master logs, NodeFeature objects, the
rule and the custom-label allowlist. If labels exist but resources do not,
inspect plugin logs/Pod events, the real kubelet socket and DRM device/driver
availability. Resolve policy/host-access errors narrowly; do not enable
privileged mode, disable AppArmor or chmod DRM devices as a generic workaround.

### Inotify exhaustion: `couldn't initialize inotify: too many open files`

If registration succeeds but the plugin then fails to create its socket watcher,
check the **K3s node's** limits, not the shell on a remote kubectl client:

```bash
sysctl fs.inotify.max_user_instances fs.inotify.max_user_watches
```

`inotify_init` can return `EMFILE` when either the per-real-UID instance limit or
the process's open-file limit is exhausted. The observed host instance limit was
128, shared by root processes on the host and in containers. Raising a shell's
`ulimit` does not raise this limit. Watch exhaustion instead normally fails when
adding a watch with `ENOSPC`; increasing `max_user_watches` alone does not fix
instance exhaustion.

On the native K3s node, from the repository root, install the persistent host
configuration and apply it immediately. Review existing sysctl configuration
first; retain a higher instance limit if one is already configured:

```bash
sudo install -m 0644 clusters/management-k3s/99-kubernetes-inotify.conf \
  /etc/sysctl.d/99-kubernetes-inotify.conf
sudo sysctl -p /etc/sysctl.d/99-kubernetes-inotify.conf
sysctl fs.inotify.max_user_instances
```

The expected instance limit is **1024** (or your existing higher value). Apply
this prerequisite before deploying the GPU plugin on fresh nodes too. It is
host configuration, not an Argo CD resource or a Pod `securityContext.sysctls`
setting. Do not add a privileged init container to change host limits.

The crash-looping plugin will retry automatically. To clear its backoff after
applying the host fix, restart only this DaemonSet and verify registration:

```bash
kubectl --context management-k3s -n intel-device-plugins rollout restart daemonset/intel-gpu-plugin
kubectl --context management-k3s -n intel-device-plugins rollout status daemonset/intel-gpu-plugin --timeout=120s
kubectl --context management-k3s -n intel-device-plugins logs daemonset/intel-gpu-plugin --tail=100
kubectl --context management-k3s get node management-k3s \
  -o jsonpath='{.status.capacity.gpu\.intel\.com/i915}{" "}{.status.allocatable.gpu\.intel\.com/i915}{"\n"}'
```

Expect a ready plugin, no watcher error, and capacity/allocatable **1 1**. If
`EMFILE` persists, inspect the plugin process's `/proc/<pid>/limits` and open
descriptors on the node, and identify processes leaking inotify instances before
raising limits further. No K3s service restart or GPU driver change is required.
The host sysctl file persists independently of GitOps rollback; restore previous
values only after checking other host/container users of inotify.

### Workload requests and optional smoke test

Consumers request `gpu.intel.com/i915: 1` as an extended-resource limit.
`-shared-dev-num=1` allows only one consuming container per GPU; monitoring
resources, sharing and health sidecars are not enabled. Host desktop processes
can still use the same physical GPU: this is Kubernetes allocation exclusivity,
not isolation from the host or a dedicated GPU partition.

The following optional Pod verifies scheduling and device visibility only, not
GPU computation. It is documentation, not a GitOps-managed workload. Save it
separately and apply it only when ready to consume the node's single allocation.
The BusyBox image is version/digest-pinned and has no GPU userspace drivers:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: intel-gpu-smoke
  namespace: default
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 65534
    runAsGroup: 65534
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: check
      image: busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec
      command: [sh, -ec, "ls -l /dev/dri; test -c /dev/dri/renderD128"]
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: [ALL]
      resources:
        requests:
          cpu: 10m
          memory: 8Mi
        limits:
          memory: 16Mi
          gpu.intel.com/i915: 1
```

This example intentionally checks this host's observed render device. A real
compute/transcode test additionally needs a pinned workload image with
Skylake-compatible userspace drivers and its own functional GPU operation.
Modern Level Zero/OpenVINO support is not implied by successful allocation.
For non-root device access, discover the actual host `render`/`video` GIDs and
configure the workload's supplemental groups if needed; do not assume portable
numeric GIDs. Workloads should not manually mount `/dev/dri` or run privileged.

After an explicitly approved smoke test, read logs and remove only that Pod:

```bash
kubectl --context management-k3s -n default logs intel-gpu-smoke
kubectl --context management-k3s -n default delete pod intel-gpu-smoke
```

### Validation and rollback

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  clusters.tests.test_profiles.ProfileTests.test_gpu_applications_are_k3s_only_and_ordered \
  clusters.tests.test_profiles.ProfileTests.test_gpu_rule_matches_only_intel_display_with_enabled_driver \
  clusters.tests.test_profiles.ProfileTests.test_gpu_daemonset_uses_nfd_and_exclusive_allocation \
  clusters.tests.test_profiles.ProfileTests.test_k3s_keeps_all_addons_and_adds_collector_and_gpu
RUN_HELM_TESTS=1 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  clusters.tests.test_profiles.PinnedChartTests.test_nfd_gpu_discovery_chart
```

These tests fetch/render pinned sources without changing the cluster. Rule tests
assert the exact PCI/kernel conditions and representative positive/negative
matches; they do not replace testing the actual NFD controller on the host.

For rollback, stop/remove GPU consumers first, then remove the GPU Application
from the cluster overlay in Git and allow Argo CD to prune its DaemonSet/rule.
Leave NFD installed if anything else depends on it. If removing NFD, review its
CRDs, NodeFeature objects and managed-label cleanup explicitly before removing
its Application: automated pruning/finalizers can delete shared cluster APIs.
The Helm post-delete hook is disabled, so do not assume it will remove NFD node
labels. Use NFD's documented cleanup process in a separately reviewed operation.
Do not unload `i915`, delete host DRM devices or remove host drivers for rollback.

Sources: [Intel GPU plugin](https://github.com/intel/intel-device-plugins-for-kubernetes/blob/v0.37.1/cmd/gpu_plugin/README.md),
[Intel detection rule](https://github.com/intel/intel-device-plugins-for-kubernetes/blob/v0.37.1/deployments/nfd/overlays/node-feature-rules/node-feature-rules.yaml),
[NFD chart](https://github.com/kubernetes-sigs/node-feature-discovery/tree/v0.20.1/deployment/helm/node-feature-discovery).

## Staged rollout and existing-cluster migration

1. Back up the current cluster and inspect the proposed Argo CD diff. Do not
   repoint a live root from a kind overlay to K3s.
2. On fresh K3s, disable overlapping packaged components from installation.
   Existing installations need a deliberate service/configuration migration;
   this is not a resume mode. Verify the installed K3s release before disabling
   previously enabled Traefik: older releases can remove shared Gateway CRDs.
3. Ensure Argo CD has the Application health customization installed by
   `setup-argocd.sh`. It makes nested sync waves wait for child sync and health.
   If your initial admin credential was changed, apply the customization through
   your existing Argo CD configuration ownership rather than rerunning login.
4. Confirm CRDs and controllers first, then pools, issuers, certificates and
   gateway/routes. Health gating does not remove the need to inspect failures.
5. Confirm PVCs are Bound and workloads fit on the node before routing traffic.
6. Confirm exactly one metrics API and LoadBalancer owner; Gateway Accepted and
   Programmed; HTTPRoutes Accepted with ResolvedRefs; certificates Ready.
7. Test API, Argo CD, and platform IP/FQDN access from a **separate LAN client**,
   using the correct CA trust. Query recent pod logs in Grafana/Loki.

Argo CD Application finalizers and automated pruning can delete workloads.
Changing persistence, schema or cluster source paths requires data backups and
an explicit transition. Do not rewrite an existing Loki schema period in place.
The provided schema is for a fresh K3s profile, not a Loki data-migration tool.

Back up K3s SQLite consistently (stop the service or use a supported consistent
backup method), the server token/config, CA material, and local PV contents.
etcd snapshots are not SQLite backups. Do not uninstall K3s until data recovery
has been verified.

## Verification and future profiles

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s clusters/tests -p 'test_*.py'
RUN_HELM_TESTS=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s clusters/tests -p 'test_profiles.py'
```

The second command fetches and templates the pinned charts and checks actual
service ports and PVC configuration. It does not deploy anything.

For another setup type, reuse `platform/base`, add a small profile/component,
and create a concrete cluster overlay/root with its own identity. A managed-cloud
profile can use its provider's load balancer and CSI without selecting MetalLB.
Keep current environment entrypoints until explicitly migrated. ApplicationSet
or a centralized Argo CD control plane is not required for this model.
