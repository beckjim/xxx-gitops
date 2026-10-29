from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("validate_profile", ROOT / "clusters/validate-profile.py")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


@lru_cache(maxsize=None)
def render(path):
    result = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / path)],
        check=True, text=True, capture_output=True, timeout=300,
    )
    return list(yaml.safe_load_all(result.stdout))


def applications(path):
    return {item["metadata"]["name"]: item for item in render(path) if item["kind"] == "Application"}


@unittest.skipUnless(shutil.which("kubectl"), "kubectl is required for Kustomize rendering")
class ProfileTests(unittest.TestCase):
    def test_existing_entrypoints_and_kind_preserve_all_addons(self):
        expected = {
            "cert-manager", "trust-manager", "kubescape", "metrics-server", "metallb",
            "nginx-gateway", "platform-routes", "kyverno", "policy-reporter",
            "external-secrets", "kube-prometheus-stack", "loki", "headlamp",
            "inspektor-gadget", "metallb-config",
        }
        for name in ("management", "dev", "staging", "prod"):
            with self.subTest(name=name):
                self.assertEqual(set(applications(f"platform-overlays/{name}")), expected)
        self.assertEqual(set(applications("platform-overlays/management-kind")), expected | {"alloy"})

    def test_k3s_keeps_all_addons_and_adds_collector(self):
        legacy = applications("platform-overlays/management")
        k3s = applications("platform-overlays/management-k3s")
        self.assertEqual(set(k3s), set(legacy) | {"alloy"})
        self.assertEqual(k3s["loki"]["spec"]["source"]["helm"]["values"],
                         legacy["loki"]["spec"]["source"]["helm"]["values"])
        self.assertEqual(k3s["kube-prometheus-stack"]["spec"]["source"]["helm"]["values"],
                         legacy["kube-prometheus-stack"]["spec"]["source"]["helm"]["values"])

    def test_k3s_runtime_and_metrics_settings(self):
        apps = applications("platform-overlays/management-k3s")
        metrics = apps["metrics-server"]["spec"]["source"]["helm"]["parameters"]
        self.assertNotIn("--kubelet-insecure-tls", {item["value"] for item in metrics})
        gadget = apps["inspektor-gadget"]["spec"]["source"]["helm"]["parameters"]
        self.assertIn({"name": "config.containerdSocketPath",
                       "value": "/run/k3s/containerd/containerd.sock"}, gadget)
        params = apps["kubescape"]["spec"]["source"]["helm"]["parameters"]
        self.assertIn({"name": "clusterName", "value": "management-k3s"}, params)

    def test_k3s_routes_have_ip_and_dns_certificates(self):
        resources = render("platform/routes/management-k3s")
        gateway = next(item for item in resources if item["kind"] == "Gateway")
        self.assertEqual(gateway["spec"]["listeners"][0]["protocol"], "HTTPS")
        self.assertNotIn("hostname", gateway["spec"]["listeners"][0])
        for route in (item for item in resources if item["kind"] == "HTTPRoute"):
            self.assertNotIn("hostnames", route["spec"])
            self.assertEqual(route["spec"]["parentRefs"][0]["sectionName"], "https")
        certificates = {item["metadata"]["name"]: item["spec"]
                        for item in resources if item["kind"] == "Certificate"}
        self.assertIn("ipAddresses", certificates["platform-tls"])
        self.assertIn("dnsNames", certificates["platform-tls"])
        self.assertEqual(certificates["argocd-server-tls"]["secretName"], "argocd-server-tls")

    def test_cluster_bootstrap_selects_exact_profile(self):
        for cluster in ("management-kind", "management-k3s"):
            apps = applications(f"bootstrap/apps/{cluster}")
            self.assertEqual(apps["platform"]["spec"]["source"]["path"],
                             f"platform-overlays/{cluster}")

    def test_unconfigured_k3s_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "Unconfigured deployment input"):
            validator.validate("management-k3s")

    def test_all_helm_parameter_values_are_strings(self):
        for app in applications("platform-overlays/management-k3s").values():
            for parameter in app["spec"]["source"].get("helm", {}).get("parameters", []):
                self.assertIsInstance(parameter["value"], str)


class ConfigurationValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        directory = self.root / "clusters/example"
        directory.mkdir(parents=True)
        self.settings = {
            "context": "example", "profile": "k3s-lan", "nodeAddress": "192.168.10.20",
            "gatewayAddress": "192.168.10.30", "pool": "192.168.10.30-192.168.10.39",
            "platformFqdn": "platform.home.arpa", "argocdFqdn": "argocd.home.arpa",
        }
        self.path = directory / "settings.json"
        self.platform = [{"kind": "Application", "metadata": {"name": "nginx-gateway"},
                          "spec": {"source": {"helm": {"parameters": [
                              {"name": "nginx.service.loadBalancerIP", "value": "192.168.10.30"},
                          ]}}}}]
        self.pools = [{"kind": "IPAddressPool", "spec": {"addresses": [self.settings["pool"]]}}]
        self.routes = [
            {"kind": "Certificate", "metadata": {"name": name},
             "spec": {"dnsNames": [self.settings[dns]], "ipAddresses": [self.settings[ip]]}}
            for name, dns, ip in (
                ("platform-tls", "platformFqdn", "gatewayAddress"),
                ("argocd-server-tls", "argocdFqdn", "nodeAddress"),
            )
        ]

    def validate(self):
        self.path.write_text(json.dumps(self.settings))
        with patch.object(validator, "ROOT", self.root), patch.object(
            validator, "render", side_effect=[self.platform, self.pools, self.routes],
        ):
            validator.validate("example")

    def test_matching_configuration(self):
        self.validate()

    def test_pool_excludes_node(self):
        self.settings["nodeAddress"] = "192.168.10.31"
        with self.assertRaisesRegex(ValueError, "exclude the node"):
            self.validate()

    def test_mismatched_pool_or_certificate_is_rejected(self):
        self.pools[0]["spec"]["addresses"] = ["192.168.10.40-192.168.10.49"]
        with self.assertRaisesRegex(ValueError, "pool does not match"):
            self.validate()
        self.pools[0]["spec"]["addresses"] = [self.settings["pool"]]
        self.routes[0]["spec"]["dnsNames"] = ["wrong.home.arpa"]
        with self.assertRaisesRegex(ValueError, "SANs do not match"):
            self.validate()


@unittest.skipUnless(os.environ.get("RUN_HELM_TESTS") == "1", "Set RUN_HELM_TESTS=1 to fetch and render pinned charts")
class PinnedChartTests(unittest.TestCase):
    def test_k3s_chart_outputs(self):
        cases = [("management-k3s", name) for name in (
            "kube-prometheus-stack", "loki", "alloy", "nginx-gateway",
            "inspektor-gadget", "kubescape", "metrics-server",
        )] + [("management-kind", "loki")]
        for cluster, name in cases:
            apps = applications(f"platform-overlays/{cluster}")
            with self.subTest(cluster=cluster, chart=name), tempfile.TemporaryDirectory() as temp:
                source = apps[name]["spec"]["source"]
                helm = source["helm"]
                values_path = Path(temp) / "values.yaml"
                values_path.write_text(helm.get("values", "{}"))
                chart = source["chart"]
                command = ["helm", "template", helm["releaseName"], chart,
                           "--version", source["targetRevision"],
                           "--namespace", apps[name]["spec"]["destination"]["namespace"],
                           "--values", str(values_path)]
                if source["repoURL"].startswith("ghcr.io/"):
                    command[3] = f"oci://{source['repoURL']}/{chart}"
                else:
                    command += ["--repo", source["repoURL"]]
                for parameter in helm.get("parameters", []):
                    value = parameter["value"].replace(",", r"\,")
                    command += ["--set-string" if parameter.get("forceString") else "--set",
                                f"{parameter['name']}={value}"]
                result = subprocess.run(command, capture_output=True, text=True, timeout=300)
                self.assertEqual(result.returncode, 0, result.stderr)
                resources = list(yaml.safe_load_all(result.stdout))
                if name == "loki":
                    service = next(item for item in resources if item and item["kind"] == "Service"
                                   and item["metadata"]["name"] == "loki")
                    self.assertEqual(service["spec"].get("type", "ClusterIP"), "ClusterIP")
                    self.assertIn(3100, [port["port"] for port in service["spec"]["ports"]])
                    stateful = next(item for item in resources if item and item["kind"] == "StatefulSet")
                    claim = stateful["spec"]["volumeClaimTemplates"][0]["spec"]
                    if cluster == "management-k3s":
                        self.assertEqual(claim["storageClassName"], "local-path")
                if name == "kube-prometheus-stack":
                    prom = next(item for item in resources if item and item["kind"] == "Prometheus")
                    self.assertEqual(prom["spec"]["storage"]["volumeClaimTemplate"]["spec"]
                                     ["resources"]["requests"]["storage"], "10Gi")
                    self.assertEqual(prom["spec"]["retentionSize"], "8GB")
                    grafana = next(item for item in resources if item and item["kind"] == "Deployment"
                                   and item["metadata"]["name"] == "kube-prometheus-stack-grafana")
                    env = next(container["env"] for container in
                               grafana["spec"]["template"]["spec"]["containers"]
                               if container["name"] == "grafana")
                    password = next(item for item in env if item["name"] == "GF_SECURITY_ADMIN_PASSWORD")
                    self.assertEqual(password["valueFrom"]["secretKeyRef"]["name"], "grafana-admin")
                if name == "alloy":
                    daemonset = next(item for item in resources if item and item["kind"] == "DaemonSet")
                    self.assertTrue(any(volume.get("hostPath", {}).get("path") == "/var/log"
                                        for volume in daemonset["spec"]["template"]["spec"]["volumes"]))
                if name == "kubescape":
                    agents = [item for item in resources if item and item["kind"] == "DaemonSet"
                              and "node-agent" in item["metadata"]["name"]]
                    self.assertTrue(agents)
                    env = agents[0]["spec"]["template"]["spec"]["containers"][0]["env"]
                    self.assertIn({"name": "RUNTIME_PATH",
                                   "value": "/host/var/lib/rancher/k3s/data/current/bin/runc"}, env)
                if name == "metrics-server":
                    deployment = next(item for item in resources if item and item["kind"] == "Deployment")
                    args = deployment["spec"]["template"]["spec"]["containers"][0]["args"]
                    self.assertNotIn("--kubelet-insecure-tls", args)
                    self.assertIn("--kubelet-preferred-address-types=InternalIP,Hostname,InternalDNS", args)


if __name__ == "__main__":
    unittest.main()
