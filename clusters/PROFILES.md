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
