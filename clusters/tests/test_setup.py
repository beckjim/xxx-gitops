import base64
import json
import os
from pathlib import Path
import subprocess
import shutil
import signal
import sys
import tempfile
import unittest


CLUSTERS = Path(__file__).resolve().parents[1]
SCRIPT = CLUSTERS / "setup-mgmt.sh"
ARGOCD_SCRIPT = CLUSTERS / "setup-argocd.sh"
LAN_HELPER = CLUSTERS / "lan-access.sh"
K3S_CONFIG = {
    "apiVersion": "v1",
    "kind": "Config",
    "clusters": [{"name": "default", "cluster": {
        "server": "https://127.0.0.1:6443",
        "certificate-authority-data": base64.b64encode(b"test-ca").decode(),
    }}],
    "users": [{"name": "default", "user": {
        "client-certificate-data": base64.b64encode(b"test-cert").decode(),
        "client-key-data": base64.b64encode(b"test-key").decode(),
    }}],
    "contexts": [{"name": "default", "context": {"cluster": "default", "user": "default"}}],
    "current-context": "default",
}
MOCK = r'''
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["MOCK_LOG"], "a") as log:
    log.write(json.dumps([tool, *args]) + "\n")

if tool == "uname":
    print(os.environ.get("MOCK_OS", "Linux"))
elif tool == "id":
    print(os.environ.get("MOCK_UID", "1000"))
elif tool == "hostname":
    if os.environ.get("MOCK_HOSTNAME_FAIL"):
        sys.exit(1)
    if args == ["-s"]:
        print(os.environ.get("MOCK_SHORT_HOSTNAME", "management"))
    elif args == ["-d"]:
        print(os.environ.get("MOCK_DOMAIN", ""))
    else:
        print(os.environ.get("MOCK_FQDN", "management.home.arpa"))
elif tool == "getent":
    if os.environ.get("MOCK_DNS_FAIL"):
        sys.exit(2)
    if args[0] == "hosts":
        name = os.environ.get(
            "MOCK_REVERSE_FQDN" if args[-1] == "192.168.10.20" else "MOCK_CANONICAL_FQDN"
        )
        if not name:
            sys.exit(2)
        print("192.168.10.20 " + name)
        sys.exit(0)
    addresses = os.environ.get("MOCK_DNS_ADDRESSES", "192.168.10.20")
    if "MOCK_DNS_RECORDS" in os.environ:
        addresses = json.loads(os.environ["MOCK_DNS_RECORDS"]).get(args[-1], "")
        if not addresses:
            sys.exit(2)
    for address in addresses.split():
        print(address + " STREAM " + args[-1])
elif tool == "ip":
    if "route" in args:
        if os.environ.get("MOCK_NO_ROUTE"):
            sys.exit(1)
        print("1.1.1.1 via 192.168.10.1 dev eth0 src 192.168.10.20 uid 1000")
    else:
        print("2: eth0 inet 192.168.10.20/24 brd 192.168.10.255 scope global eth0")
        print("3: eth1 inet 192.168.20.30/24 brd 192.168.20.255 scope global eth1")
elif tool == "sudo":
    if args == ["-v"]:
        pass
    elif args[0] == "test":
        sys.exit(0 if os.environ.get("MOCK_EXISTING_K3S") else 1)
    elif args[0] == "cat":
        print(os.environ["MOCK_K3S_CONFIG"])
    elif args[0] == "env":
        sys.exit(subprocess.call(args))
    else:
        sys.exit("Unexpected privileged command: " + repr(args))
elif tool == "curl":
    if os.environ.get("MOCK_DOWNLOAD_FAIL") or os.environ.get("MOCK_ACCESS_FAIL"):
        sys.exit(22)
    if os.environ.get("MOCK_DNS_ACCESS_FAIL") and any("home.arpa" in arg for arg in args):
        sys.exit(22)
    if "https://get.k3s.io" in args:
        Path(args[args.index("--output") + 1]).write_text(
            'printf "%s\\n" "$@" > "$MOCK_INSTALL_ARGS"\n'
            'printf "%s\\n" "$INSTALL_K3S_VERSION" > "$MOCK_INSTALL_VERSION"\n'
            'printf "%s\\n" "$INSTALL_K3S_SYMLINK" > "$MOCK_INSTALL_SYMLINK"\n'
        )
elif tool == "kind":
    if os.environ.get("MOCK_KIND_FAIL"):
        sys.exit(1)
    config = json.loads(os.environ["MOCK_K3S_CONFIG"])
    name = "kind-" + args[args.index("--name") + 1]
    for section in ("clusters", "users", "contexts"):
        config[section][0]["name"] = name
    config["contexts"][0]["context"] = {"cluster": name, "user": name}
    config["current-context"] = name
    path = Path(args[args.index("--kubeconfig") + 1])
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(config))
elif tool in ("k3s", "kubectl"):
    if "rename-cluster" in args or "rename-user" in args:
        sys.exit("error: unknown command")
    if tool == "kubectl":
        path = (Path(args[args.index("--kubeconfig") + 1]) if "--kubeconfig" in args
                else Path.home() / ".kube/config")
        if os.environ.get("MOCK_REAL_K3S") and "config" in args:
            sys.exit(subprocess.call(
                [os.environ["MOCK_REAL_K3S"], "kubectl", *args],
                env={**os.environ, "KUBECONFIG": os.environ.get("KUBECONFIG", str(path))},
            ))
        config = json.loads(path.read_text()) if path.exists() and "config" in args else {}
        if "get-contexts" in args:
            for entry in config.get("contexts", []):
                index = args.index("get-contexts") + 1
                if args[index] == "-o" or entry["name"] == args[index]:
                    print(entry["name"])
        elif "rename-context" in args:
            for entry in config["contexts"]:
                if entry["name"] == args[-2]:
                    entry["name"] = args[-1]
            if config["current-context"] == args[-2]:
                config["current-context"] = args[-1]
            path.write_text(json.dumps(config))
        elif "use-context" in args:
            config["current-context"] = args[-1]
            path.write_text(json.dumps(config))
        elif "view" in args and any("jsonpath=" in arg for arg in args):
            context = next(e["context"] for e in config["contexts"] if e["name"] == "management")
            cluster = next(e["cluster"] for e in config["clusters"] if e["name"] == context["cluster"])
            print(cluster["server"])
        elif "nodes" in args:
            if os.environ.get("MOCK_NODES_FAIL"):
                sys.exit(1)
            if args[-1] == "json":
                print(json.dumps({"items": [{
                    "spec": {"providerID": os.environ.get("MOCK_PROVIDER_ID", "kind://docker/test/node")},
                    "status": {"nodeInfo": {
                        "kubeletVersion": os.environ.get("MOCK_KUBELET_VERSION", "v1.34.1"),
                    }, "addresses": [{"type": "InternalIP", "address": "192.168.10.20"}]},
                }]}))
            else:
                print(os.environ.get("MOCK_KUBELET_VERSION", "v1.34.1"))
        elif "kustomize" in args:
            print(os.environ.get("MOCK_RENDERED_PLATFORM",
                  "apiVersion: argoproj.io/v1alpha1\nkind: Application\nmetadata:\n  name: platform"))
        elif "addons.k3s.cattle.io" in args:
            print(json.dumps({"items": []}))
        elif "apply" in args and "-f" in args:
            Path(os.environ["HOME"], "applied-root.yaml").write_text(sys.stdin.read())
        elif "port-forward" in args:
            Path(os.environ["MOCK_FORWARD_PID"]).write_text(str(os.getpid()))
            if os.environ.get("MOCK_FORWARD_FAIL"):
                sys.exit("Mock port-forward failed")
            print("Forwarding from 127.0.0.1:8080 -> 8080", flush=True)
            while True:
                time.sleep(1)
    if "view" in args:
        if tool == "kubectl" and any("jsonpath=" in arg for arg in args):
            pass
        elif os.environ.get("MOCK_REAL_K3S"):
            sys.exit(subprocess.call([os.environ["MOCK_REAL_K3S"], *args]))
        elif "--kubeconfig" in args:
            print(Path(args[args.index("--kubeconfig") + 1]).read_text())
        else:
            configs = [json.loads(Path(path).read_text())
                       for path in os.environ["KUBECONFIG"].split(":") if Path(path).exists()]
            merged = configs[0]
            for config in configs[1:]:
                for section in ("clusters", "users", "contexts"):
                    known = {entry["name"] for entry in merged[section]}
                    merged[section].extend(entry for entry in config[section] if entry["name"] not in known)
            if "--raw" not in args:
                for user in merged["users"]:
                    user["user"] = {"client-key-data": "REDACTED"}
            print(json.dumps(merged))
    if "wait" in args and os.environ.get("MOCK_WAIT_FAIL"):
        sys.exit(1)
    if "--raw=/readyz" in args and os.environ.get("MOCK_API_ACCESS_FAIL"):
        sys.exit(1)
    if "secret" in args:
        if os.environ.get("MOCK_SECRET_FAIL"):
            sys.exit("Mock secret missing")
        if not os.environ.get("MOCK_EMPTY_SECRET"):
            print(base64.b64encode(b"test-admin-password").decode())
elif tool == "helm":
    if "upgrade" in args and os.environ.get("MOCK_HELM_FAIL"):
        sys.exit(1)
elif tool == "argocd":
    if os.environ.get("MOCK_LOGIN_FAIL"):
        sys.exit(1)
elif tool == "sleep":
    time.sleep(0.02)
else:
    sys.exit("Unexpected command: " + tool)
'''


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        mock = self.bin / "mock"
        mock.write_text(f"#!{sys.executable}\n" + MOCK)
        mock.chmod(0o755)
        for tool in ("uname", "id", "ip", "sudo", "curl", "k3s", "kubectl",
                     "kind", "helm", "argocd", "sleep", "hostname", "getent"):
            (self.bin / tool).symlink_to(mock)
        self.log = self.root / "commands.jsonl"
        self.config = self.root / ".kube" / "k3s-management.yaml"
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.root),
            "TMPDIR": str(self.root),
            "MOCK_LOG": str(self.log),
            "MOCK_INSTALL_ARGS": str(self.root / "install-args"),
            "MOCK_INSTALL_VERSION": str(self.root / "install-version"),
            "MOCK_INSTALL_SYMLINK": str(self.root / "install-symlink"),
            "MOCK_K3S_CONFIG": json.dumps(K3S_CONFIG),
            "MOCK_FORWARD_PID": str(self.root / "forward-pid"),
            "CLUSTER_ID": "management",
            "KUBECONFIG_FILE": str(self.config),
        }
        self.addCleanup(self.stop_forward)

    def stop_forward(self):
        path = self.root / "forward-pid"
        if path.exists():
            try:
                os.kill(int(path.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run_script(self, script, *args, **overrides):
        if self.log.exists():
            self.log.unlink()
        result = subprocess.run(
            ["bash", str(script), *args],
            env={**self.env, **overrides},
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.calls = (
            [json.loads(line) for line in self.log.read_text().splitlines()]
            if self.log.exists() else []
        )
        return result

    def run_setup(self, *args, **overrides):
        return self.run_script(SCRIPT, "k3s", *args, **overrides)

    def management_config(self, server="https://192.168.10.20:6443"):
        config = json.loads(json.dumps(K3S_CONFIG))
        for section in ("clusters", "users", "contexts"):
            config[section][0]["name"] = "management"
        config["clusters"][0]["cluster"]["server"] = server
        config["contexts"][0]["context"] = {"cluster": "management", "user": "management"}
        # Argo CD must not depend on the caller's current context.
        config["current-context"] = "production"
        config["contexts"].append({"name": "production", "context": {
            "cluster": "production", "user": "management",
        }})
        config["clusters"].append({"name": "production", "cluster": {
            "server": "https://10.1.1.1:6443",
        }})
        path = self.root / ".kube/config"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(config))
        return path


class LanFqdnTests(SetupTests):
    def discover(self, requested="", **overrides):
        result = subprocess.run(
            ["bash", "-c",
             'set -euo pipefail; fail() { echo "ERROR: $*" >&2; exit 1; }; '
             'source "$1"; select_lan_fqdn "$2" 192.168.10.20',
             "test-fqdn", str(LAN_HELPER), requested],
            env={**self.env, **overrides}, text=True, capture_output=True, timeout=20,
        )
        return result

    def test_short_hostname_uses_reverse_lookup(self):
        result = self.discover(
            MOCK_FQDN="k8s", MOCK_SHORT_HOSTNAME="k8s",
            MOCK_REVERSE_FQDN="K8S.Home.Arpa.",
            MOCK_DNS_RECORDS=json.dumps({"k8s.home.arpa": "192.168.10.20"}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "k8s.home.arpa\n")
        self.assertEqual(result.stderr, "")

    def test_reverse_lookup_is_forward_confirmed_and_aliases_are_tried(self):
        result = self.discover(
            MOCK_FQDN="k8s", MOCK_REVERSE_FQDN="wrong.home.arpa right.home.arpa",
            MOCK_DNS_RECORDS=json.dumps({
                "wrong.home.arpa": "192.168.10.99",
                "right.home.arpa": "192.168.10.20",
            }),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "right.home.arpa\n")

    def test_resolver_canonical_name_uses_dns_search(self):
        result = self.discover(
            MOCK_FQDN="k8s", MOCK_SHORT_HOSTNAME="k8s",
            MOCK_CANONICAL_FQDN="k8s.home.arpa",
            MOCK_DNS_RECORDS=json.dumps({"k8s.home.arpa": "192.168.10.20"}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "k8s.home.arpa\n")

    def test_short_hostname_uses_host_domain(self):
        result = self.discover(
            MOCK_FQDN="k8s", MOCK_SHORT_HOSTNAME="k8s", MOCK_DOMAIN="home.arpa",
            MOCK_DNS_RECORDS=json.dumps({"k8s.home.arpa": "192.168.10.20"}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "k8s.home.arpa\n")

    def test_hostname_failure_still_tries_reverse_lookup(self):
        result = self.discover(
            MOCK_HOSTNAME_FAIL="1", MOCK_REVERSE_FQDN="k8s.home.arpa",
            MOCK_DNS_RECORDS=json.dumps({"k8s.home.arpa": "192.168.10.20"}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "k8s.home.arpa\n")

    def test_resolver_search_and_domain_candidates(self):
        resolver = self.root / "resolv.conf"
        resolver.write_text(
            "# resolver configuration\n"
            "nameserver 192.168.10.1\n"
            "search home.arpa example.test # ignored.test\n"
            "domain lan.test ; ignored.test\n"
        )
        result = subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; lan_fqdn_candidates "$2" "$3"',
             "test-candidates", str(LAN_HELPER), "192.168.10.20", str(resolver)],
            env={**self.env, "MOCK_FQDN": "k8s", "MOCK_SHORT_HOSTNAME": "k8s"},
            text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ["k8s", "k8s.home.arpa", "k8s.example.test", "k8s.lan.test"])

    def test_unconfirmed_reverse_lookup_warns(self):
        result = self.discover(
            MOCK_FQDN="k8s", MOCK_REVERSE_FQDN="wrong.home.arpa",
            MOCK_DNS_RECORDS=json.dumps({"wrong.home.arpa": "192.168.10.99"}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("WARNING: No FQDN", result.stderr)

    def test_explicit_name_does_not_fall_back_to_discovery(self):
        result = self.discover(
            "wrong.home.arpa", MOCK_REVERSE_FQDN="right.home.arpa",
            MOCK_DNS_RECORDS=json.dumps({"right.home.arpa": "192.168.10.20"}),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR:", result.stderr)


@unittest.skipUnless(
    Path("/run/systemd/system").is_dir() or os.access("/sbin/openrc-run", os.X_OK),
    "The provisioner requires a Linux host with systemd or OpenRC",
)
class SetupK3sTests(SetupTests):
    def test_distinct_k3s_identity_preserves_existing_kind_identity(self):
        path = self.management_config()
        original = json.loads(path.read_text())
        result = self.run_setup("management-k3s",
                                KUBECONFIG_FILE=str(self.root / ".kube/management-k3s.yaml"))
        self.assertEqual(result.returncode, 0, result.stderr)
        merged = json.loads(path.read_text())
        for section in ("clusters", "users", "contexts"):
            for entry in original[section]:
                self.assertIn(entry, merged[section])
            self.assertTrue(any(entry["name"] == "management-k3s" for entry in merged[section]))
        self.assertEqual(merged["current-context"], original["current-context"])
        args = (self.root / "install-args").read_text().splitlines()
        for disable in ("traefik", "servicelb", "metrics-server"):
            self.assertIn(f"--disable={disable}", args)
        self.assertEqual(args[args.index("--node-name") + 1], "management-k3s")

    def test_default_lan_setup(self):
        existing = self.root / ".kube" / "config"
        existing.parent.mkdir()
        existing.write_text(json.dumps(K3S_CONFIG))
        original = existing.read_text()
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.root / "install-args").read_text().splitlines()
        for flag in ("--bind-address", "--advertise-address", "--node-ip", "--tls-san"):
            self.assertEqual(args[args.index(flag) + 1], "192.168.10.20")
        self.assertEqual(args[args.index("--https-listen-port") + 1], "6443")
        self.assertEqual((self.root / "install-symlink").read_text().strip(), "skip")
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        merged = json.loads(existing.read_text())
        self.assertEqual(len(merged["clusters"]), 2)
        self.assertEqual(existing.stat().st_mode & 0o777, 0o600)
        new_user = next(entry for entry in merged["users"] if entry["name"] == "management")
        self.assertEqual(
            new_user["user"]["client-key-data"],
            K3S_CONFIG["users"][0]["user"]["client-key-data"],
        )
        old = json.loads(original)
        for section in ("clusters", "users", "contexts"):
            original_entry = next(entry for entry in merged[section] if entry["name"] == "default")
            self.assertEqual(original_entry, old[section][0])
        self.assertEqual(merged["current-context"], "default")
        self.assertFalse(any(call[0] in ("helm", "argocd") for call in self.calls))
        config = json.loads(self.config.read_text())
        for section in ("clusters", "users", "contexts"):
            self.assertEqual(config[section][0]["name"], "management")
        self.assertEqual(config["contexts"][0]["context"], {
            "cluster": "management", "user": "management",
        })
        waits = [call for call in self.calls if call[0] == "k3s" and "wait" in call]
        self.assertIn("--for=create", waits[0])
        self.assertIn("node/management", waits[0])
        self.assertIn("--for=condition=Ready", waits[1])
        for call in waits:
            self.assertEqual(call[call.index("--context") + 1], "management")
        self.assertNotIn("test-admin-password", result.stdout + result.stderr)
        self.assertFalse(any("port-forward" in call for call in self.calls))
        self.assertEqual(list(self.root.glob("tmp.*")), [])

    @unittest.skipUnless(shutil.which("k3s"), "K3s is not installed")
    def test_real_kubectl_offline_config_commands(self):
        self.env["MOCK_REAL_K3S"] = shutil.which("k3s")
        self.test_default_lan_setup()

    def test_explicit_overrides(self):
        result = self.run_setup(
            API_SERVER_ADDRESS="192.168.20.30",
            API_SERVER_PORT="7443",
            K3S_VERSION="v1.34.1+k3s1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(["ip", "-4", "route", "get", "1.1.1.1"], self.calls)
        self.assertIn("https://192.168.20.30:7443", result.stdout)
        self.assertEqual((self.root / "install-version").read_text().strip(), "v1.34.1+k3s1")

    def test_api_fqdn_and_ip_are_in_certificate_and_checked(self):
        result = self.run_setup(API_SERVER_FQDN="API.Home.Arpa.")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.root / "install-args").read_text().splitlines()
        sans = [args[i + 1] for i, arg in enumerate(args) if arg == "--tls-san"]
        self.assertEqual(sans, ["192.168.10.20", "api.home.arpa"])
        checks = [call for call in self.calls if "--raw=/readyz" in call]
        self.assertEqual(len(checks), 2)
        self.assertIn("https://api.home.arpa:6443", checks[1])
        config = json.loads(self.config.read_text())
        self.assertEqual(config["clusters"][0]["cluster"]["server"],
                         "https://192.168.10.20:6443")

    def test_auto_fqdn_is_added_to_certificate(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("management.home.arpa", (self.root / "install-args").read_text())

    def test_unusable_auto_fqdn_warns_and_keeps_ip_access(self):
        for overrides in ({"MOCK_DNS_FAIL": "1"},
                          {"MOCK_FQDN": "localhost", "MOCK_DNS_RECORDS": "{}"},
                          {"MOCK_HOSTNAME_FAIL": "1"},
                          {"MOCK_DNS_ADDRESSES": "192.168.10.99"}):
            with self.subTest(overrides=overrides):
                if self.config.exists():
                    self.config.unlink()
                user_config = self.root / ".kube/config"
                if user_config.exists():
                    user_config.unlink()
                result = self.run_setup(**overrides)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("WARNING:", result.stderr)
                args = (self.root / "install-args").read_text().splitlines()
                self.assertEqual(args.count("--tls-san"), 1)

    def test_invalid_fqdns_stop_before_install(self):
        for fqdn in ("localhost", "192.168.10.20", "https://api.home.arpa",
                     "api.home.arpa:6443", "api..home.arpa", ".home.arpa",
                     "api.home.arpa..", "-api.home.arpa", "api-.home.arpa",
                     "a" * 64 + ".home.arpa"):
            with self.subTest(fqdn=fqdn):
                result = self.run_setup(API_SERVER_FQDN=fqdn)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ERROR:", result.stderr)
                self.assertFalse(any(call[0] == "curl" for call in self.calls))

    def test_invalid_configuration_never_downloads(self):
        for overrides in (
            {"API_SERVER_ADDRESS": "0.0.0.0"},
            {"API_SERVER_ADDRESS": "127.0.0.1"},
            {"API_SERVER_ADDRESS": "999.1.1.1"},
            {"API_SERVER_ADDRESS": "192.168.10.99"},
            {"API_SERVER_PORT": "65536"},
            {"API_SERVER_PORT": "abc"},
            {"API_SERVER_PORT": "30443"},
            {"API_SERVER_PORT": "30080"},
            {"KUBECONFIG_FILE": "relative.yaml"},
            {"KUBECONFIG_FILE": str(self.root / ".kube/config")},
            {"KUBECONFIG_FILE": str(self.root / ".kube/../.kube/config")},
            {"MOCK_NO_ROUTE": "1"},
            {"MOCK_OS": "Darwin"},
            {"MOCK_EXISTING_K3S": "1"},
            {"API_SERVER_FQDN": "invalid_name.home.arpa"},
            {"API_SERVER_FQDN": "api.home.arpa", "MOCK_DNS_FAIL": "1"},
            {"API_SERVER_FQDN": "api.home.arpa", "MOCK_DNS_ADDRESSES": "192.168.10.99"},
            {"API_SERVER_FQDN": "api.home.arpa",
             "MOCK_DNS_ADDRESSES": "192.168.10.20 192.168.10.99"},
        ):
            with self.subTest(overrides=overrides):
                result = self.run_setup(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ERROR:", result.stderr)
                self.assertFalse(any(call[0] == "curl" for call in self.calls))

    def test_existing_kubeconfig_is_not_overwritten(self):
        self.config.parent.mkdir()
        self.config.write_text("do-not-overwrite")
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), "do-not-overwrite")
        self.assertFalse(any(call[0] == "curl" for call in self.calls))

    def test_invalid_installer_kubeconfig_is_not_normalized(self):
        config = {**K3S_CONFIG, "clusters": []}
        result = self.run_setup(MOCK_K3S_CONFIG=json.dumps(config))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected a single K3s entry", result.stderr)
        self.assertEqual(json.loads(self.config.read_text()), config)
        self.assertFalse((self.root / ".kube" / "config").exists())
        self.assertEqual(list(self.root.glob(".kube/.k3s-config.*")), [])

    def test_symlink_kubeconfig_is_not_overwritten(self):
        self.config.parent.mkdir()
        original = self.root / "original-config"
        original.write_text(json.dumps(K3S_CONFIG))
        self.config.symlink_to(original)
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing symlink", result.stderr)
        self.assertEqual(json.loads(original.read_text()), K3S_CONFIG)

    @unittest.skipUnless(shutil.which("k3s"), "K3s is not installed")
    def test_invalid_user_config_merge_preserves_original(self):
        user_config = self.config.parent / "config"
        user_config.parent.mkdir()
        original = "not valid: ["
        user_config.write_text(original)
        result = self.run_setup(MOCK_REAL_K3S=shutil.which("k3s"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(user_config.read_text(), original)
        self.assertEqual(list(self.root.glob(".kube/.config.*")), [])
        self.assertNotIn("Management cluster ready.", result.stdout)

    @unittest.skipUnless(shutil.which("k3s"), "K3s is not installed")
    def test_existing_management_identity_is_not_overwritten(self):
        path = self.management_config("https://192.168.10.99:6443")
        original = json.loads(path.read_text())
        result = self.run_setup(MOCK_REAL_K3S=shutil.which("k3s"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cluster identity already exists", result.stderr)
        self.assertEqual(json.loads(path.read_text()), original)

    def test_failures_do_not_report_success(self):
        for failure in ("MOCK_DOWNLOAD_FAIL", "MOCK_WAIT_FAIL", "MOCK_API_ACCESS_FAIL"):
            with self.subTest(failure=failure):
                if self.config.exists():
                    self.config.unlink()
                result = self.run_setup(**{failure: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Management cluster ready.", result.stdout)
                self.assertEqual(list(self.root.glob("tmp.*")), [])
                self.assertEqual(list(self.root.glob(".kube/.config.*")), [])
                self.assertEqual(list(self.root.glob(".kube/.k3s-config.*")), [])

    def test_missing_client_stops_before_install(self):
        (self.bin / "ip").unlink()
        (self.bin / "bash").symlink_to("/bin/bash")
        result = self.run_setup(PATH=str(self.bin))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ip is required", result.stderr)
        self.assertFalse(any(call[0] == "curl" for call in self.calls))

    def test_provisioning_needs_no_argocd_clients(self):
        for tool in ("helm", "argocd", "kubectl"):
            (self.bin / tool).unlink()
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)




class SetupKindTests(SetupTests):
    def test_existing_kind_cluster_entry_without_matching_context_is_rejected(self):
        path = self.management_config()
        config = json.loads(path.read_text())
        config["clusters"].append({
            "name": "kind-management-kind", "cluster": {"server": "https://192.168.10.99:6443"},
        })
        path.write_text(json.dumps(config))
        original = path.read_text()
        result = self.run_script(SCRIPT, "kind", "management-kind",
                                 KUBECONFIG_FILE=str(self.root / ".kube/management-kind.yaml"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cluster identity already exists", result.stderr)
        self.assertEqual(path.read_text(), original)
        self.assertFalse(any(call[0] == "kind" for call in self.calls))

    def test_kind_identity_preserves_existing_current_context(self):
        path = self.management_config()
        original = json.loads(path.read_text())
        result = self.run_script(SCRIPT, "kind", "management-kind",
                                 KUBECONFIG_FILE=str(self.root / ".kube/management-kind.yaml"))
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(path.read_text())
        self.assertEqual(config["current-context"], "production")
        for entry in original["contexts"]:
            self.assertIn(entry, config["contexts"])
        self.assertTrue(any(entry["name"] == "management-kind" for entry in config["contexts"]))
        self.assertFalse(any("use-context" in call for call in self.calls))

    def test_distribution_must_match_declared_profile(self):
        result = self.run_script(SCRIPT, "kind", "management-k3s")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uses profile k3s-lan", result.stderr)
        self.assertEqual(self.calls, [])

    def test_kind_setup_from_other_directory(self):
        result = self.run_script(SCRIPT, "kind")
        self.assertEqual(result.returncode, 0, result.stderr)
        create = next(call for call in self.calls if call[0] == "kind")
        self.assertEqual(create[create.index("--config") + 1],
                         str(CLUSTERS / "management/cluster.yaml"))
        path = self.root / ".kube/config"
        if self.env.get("MOCK_REAL_K3S"):
            config = json.loads(subprocess.check_output(
                [self.env["MOCK_REAL_K3S"], "kubectl", "--kubeconfig", str(path),
                 "config", "view", "--raw", "-o", "json"],
                text=True,
            ))
        else:
            config = json.loads(path.read_text())
        self.assertIn(config["current-context"], ("", "management"))
        self.assertEqual(config["contexts"][0]["name"], "management")
        self.assertEqual(config["contexts"][0]["context"]["cluster"], "kind-management")
        self.assertFalse(any(call[0] in ("helm", "argocd", "curl", "k3s") for call in self.calls))

    @unittest.skipUnless(shutil.which("k3s"), "K3s is not installed")
    def test_kind_real_offline_context_commands(self):
        self.env["MOCK_REAL_K3S"] = shutil.which("k3s")
        self.test_kind_setup_from_other_directory()

    def test_conflicting_context_stops_before_creation(self):
        path = self.management_config()
        original = path.read_text()
        result = self.run_script(SCRIPT, "kind")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_text(), original)
        self.assertFalse(any(call[0] == "kind" for call in self.calls))

    def test_invalid_arguments(self):
        for args in ((), ("other",), ("kind", "--resume"), ("k3s", "--resume"),
                     ("k3s", "--other"), ("k3s", "--other", "extra")):
            with self.subTest(args=args):
                result = self.run_script(SCRIPT, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue("Usage:" in result.stderr or "Invalid cluster ID:" in result.stderr)
                self.assertEqual(self.calls, [])

    def test_creation_failure(self):
        result = self.run_script(SCRIPT, "kind", MOCK_KIND_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Management cluster ready.", result.stdout)
        self.assertFalse(any("rename-context" in call for call in self.calls))

    def test_node_readiness_failure(self):
        result = self.run_script(SCRIPT, "kind", MOCK_WAIT_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Management cluster ready.", result.stdout)

    def test_legacy_context_conflict(self):
        path = self.management_config()
        config = json.loads(path.read_text())
        config["contexts"][0]["name"] = "kind-management"
        path.write_text(json.dumps(config))
        result = self.run_script(SCRIPT, "kind")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Context kind-management already exists", result.stderr)
        self.assertFalse(any(call[0] == "kind" for call in self.calls))


class SetupArgoCDTests(SetupTests):
    def setUp(self):
        super().setUp()
        self.management_config()
        # Argo CD does not require either distribution's provisioner.
        for tool in ("kind", "k3s"):
            (self.bin / tool).unlink()

    def run_argocd(self, *args, **overrides):
        return self.run_script(ARGOCD_SCRIPT, *args, **overrides)

    def assert_management_targeting(self):
        helm = next(call for call in self.calls if call[:2] == ["helm", "upgrade"])
        self.assertEqual(helm[helm.index("--kube-context") + 1], "management")
        for call in self.calls:
            if call[0] == "kubectl" and call[1:3] != ["config", "get-contexts"]:
                self.assertEqual(call[1:3], ["--context", "management"])
        self.assertFalse(any(call[0] in ("kind", "k3s", "sudo", "ip") for call in self.calls))

    def test_default_local_access(self):
        result = self.run_argocd()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_management_targeting()
        helm = next(call for call in self.calls if call[:2] == ["helm", "upgrade"])
        self.assertIn("server.service.type=ClusterIP", helm)
        login = next(call for call in self.calls if call[:2] == ["argocd", "login"])
        self.assertIn("localhost:8080", login)
        self.assertIn("Local port-forward running", result.stdout)
        self.assertNotIn("test-admin-password", result.stdout + result.stderr)
        os.kill(int((self.root / "forward-pid").read_text()), 0)
        self.assertEqual(list(self.root.glob("tmp.*")), [])
        self.assertEqual(json.loads((self.root / ".kube/config").read_text())["current-context"],
                         "production")

    def test_explicit_context_targets_only_selected_cluster(self):
        path = self.root / ".kube/config"
        config = json.loads(path.read_text())
        config["contexts"][0]["name"] = "management-kind"
        path.write_text(json.dumps(config))
        result = self.run_argocd("--context", "management-kind", "--local")
        self.assertEqual(result.returncode, 0, result.stderr)
        helm = next(call for call in self.calls if call[:2] == ["helm", "upgrade"])
        self.assertEqual(helm[helm.index("--kube-context") + 1], "management-kind")
        self.assertFalse(any("use-context" in call for call in self.calls))

    def test_nodeport_and_chart_overrides(self):
        result = self.run_argocd("--nodeport", ARGOCD_HTTPS_NODE_PORT="31443",
                                 ARGOCD_CHART_VERSION="9.0.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_management_targeting()
        helm = next(call for call in self.calls if call[:2] == ["helm", "upgrade"])
        self.assertIn("server.service.nodePortHttps=31443", helm)
        self.assertIn("server.service.type=NodePort", helm)
        self.assertEqual(helm[helm.index("--version") + 1], "9.0.0")
        login = next(call for call in self.calls if call[:2] == ["argocd", "login"])
        self.assertIn("192.168.10.20:31443", login)
        self.assertIn("--grpc-web", login)
        self.assertFalse(any("port-forward" in call for call in self.calls))
        self.assertEqual(list(self.root.glob("tmp.*")), [])

    def test_k3s_automatically_uses_lan_access(self):
        result = self.run_argocd(MOCK_KUBELET_VERSION="v1.34.1+k3s1")
        self.assertEqual(result.returncode, 0, result.stderr)
        helm = next(call for call in self.calls if call[:2] == ["helm", "upgrade"])
        self.assertIn("server.service.type=NodePort", helm)
        self.assertIn("configs.cm.url=https://management.home.arpa:31443", helm)
        self.assertIn("global.domain=management.home.arpa", helm)
        for host in ("192.168.10.20", "management.home.arpa"):
            self.assertTrue(any(f"https://{host}:31443/healthz" in call
                                for call in self.calls if call[0] == "curl"))
            self.assertIn(f"https://{host}:31443", result.stdout)
        self.assertFalse(any("port-forward" in call for call in self.calls))

    def test_k3s_local_override(self):
        result = self.run_argocd("--local", MOCK_KUBELET_VERSION="v1.34.1+k3s1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("https://localhost:8080", result.stdout)
        self.assertFalse(any(call[0] == "hostname" for call in self.calls))

    def test_explicit_fqdn(self):
        result = self.run_argocd("--nodeport", ARGOCD_FQDN="argocd.home.arpa")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("https://argocd.home.arpa:31443", result.stdout)
        self.assertFalse(any(call[0] == "hostname" for call in self.calls))

    def test_auto_fqdn_fallback(self):
        result = self.run_argocd("--nodeport", MOCK_DNS_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING:", result.stderr)
        self.assertIn("https://192.168.10.20:31443", result.stdout)
        self.assertNotIn("Argo CD DNS URL:", result.stdout)

    def test_short_hostname_reverse_lookup_is_used_for_argocd(self):
        result = self.run_argocd(
            "--nodeport", MOCK_FQDN="k8s", MOCK_SHORT_HOSTNAME="k8s",
            MOCK_REVERSE_FQDN="k8s.home.arpa",
            MOCK_DNS_RECORDS=json.dumps({"k8s.home.arpa": "192.168.10.20"}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Argo CD DNS URL: https://k8s.home.arpa:31443", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_dns_readiness_failure(self):
        result = self.run_argocd("--nodeport", MOCK_DNS_ACCESS_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not reachable at https://management.home.arpa", result.stderr)
        self.assertFalse(any(call[0] == "argocd" for call in self.calls))

    def test_distribution_detection_failure_stops_before_helm(self):
        for overrides in ({"MOCK_NODES_FAIL": "1"}, {"MOCK_KUBELET_VERSION": ""}):
            with self.subTest(overrides=overrides):
                result = self.run_argocd(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call[0] == "helm" for call in self.calls))

    def test_nodeport_rerun(self):
        for _ in range(2):
            result = self.run_argocd("--nodeport")
            self.assertEqual(result.returncode, 0, result.stderr)
            helm = next(call for call in self.calls if call[:2] == ["helm", "upgrade"])
            self.assertIn("--install", helm)
            self.assertIn("server.service.nodePortHttps=31443", helm)
            self.assertIn("server.service.nodePortHttp=31080", helm)

    @unittest.skipUnless(shutil.which("k3s"), "K3s is not installed")
    def test_real_offline_management_endpoint(self):
        self.env["MOCK_REAL_K3S"] = shutil.which("k3s")
        result = self.run_argocd("--nodeport")
        self.assertEqual(result.returncode, 0, result.stderr)
        login = next(call for call in self.calls if call[:2] == ["argocd", "login"])
        self.assertIn("192.168.10.20:31443", login)

    def test_nodeport_readiness_failure(self):
        result = self.run_argocd("--nodeport", MOCK_ACCESS_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not reachable", result.stderr)
        self.assertFalse(any(call[0] == "argocd" for call in self.calls))
        self.assertEqual(list(self.root.glob("tmp.*")), [])

    def test_invalid_input_before_helm(self):
        for overrides in ({"ARGOCD_HTTPS_NODE_PORT": "29999"},
                          {"ARGOCD_HTTPS_NODE_PORT": "65536"},
                          {"ARGOCD_HTTPS_NODE_PORT": "abc"},
                          {"ARGOCD_HTTP_NODE_PORT": "31443"},
                          {"ARGOCD_HTTP_NODE_PORT": "abc"},
                          {"ARGOCD_HTTP_NODE_PORT": "29999"},
                          {"ARGOCD_FQDN": "bad_name.home.arpa"},
                          {"ARGOCD_FQDN": "argocd.home.arpa", "MOCK_DNS_FAIL": "1"}):
            with self.subTest(overrides=overrides):
                result = self.run_argocd("--nodeport", **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call[0] == "helm" for call in self.calls))
        result = self.run_argocd("--other")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls, [])
        result = self.run_argocd("--local", ARGOCD_FQDN="argocd.home.arpa")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[0] == "helm" for call in self.calls))


    def test_missing_context_before_helm(self):
        (self.root / ".kube/config").unlink()
        result = self.run_argocd()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Context management is missing", result.stderr)
        self.assertFalse(any(call[0] == "helm" for call in self.calls))

    def test_invalid_nodeport_endpoint_before_helm(self):
        for server in ("https://127.0.0.1:6443", "https://0.0.0.0:6443",
                       "https://999.1.1.1:6443", "https://example.test:6443"):
            with self.subTest(server=server):
                self.management_config(server)
                result = self.run_argocd("--nodeport")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call[0] == "helm" for call in self.calls))

    def test_missing_client_before_helm(self):
        (self.bin / "argocd").unlink()
        (self.bin / "bash").symlink_to("/bin/bash")
        result = self.run_argocd(PATH=str(self.bin))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("argocd is required", result.stderr)
        self.assertFalse(any(call[0] == "helm" for call in self.calls))

    def test_failures_do_not_report_success(self):
        for failure in ("MOCK_HELM_FAIL", "MOCK_WAIT_FAIL", "MOCK_SECRET_FAIL",
                        "MOCK_EMPTY_SECRET", "MOCK_LOGIN_FAIL", "MOCK_FORWARD_FAIL",
                        "MOCK_ACCESS_FAIL"):
            with self.subTest(failure=failure):
                result = self.run_argocd(**{failure: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Argo CD setup complete.", result.stdout)
                self.assertNotIn("test-admin-password", result.stdout + result.stderr)
                self.assertEqual(list(self.root.glob("tmp.*")), [])
                if (self.root / "forward-pid").exists():
                    pid = int((self.root / "forward-pid").read_text())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)


class BootstrapTests(SetupTests):
    def test_profile_rejects_wrong_distribution_before_apply(self):
        path = self.management_config()
        config = json.loads(path.read_text())
        config["contexts"][0]["name"] = "management-kind"
        path.write_text(json.dumps(config))
        result = self.run_script(
            CLUSTERS.parent / "bootstrap/bootstrap-cluster.sh", "management-kind",
            MOCK_PROVIDER_ID="k3s://node",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires kind nodes", result.stderr)
        self.assertFalse(any("apply" in call for call in self.calls))

    def test_explicit_cluster_path_and_context(self):
        path = self.management_config()
        config = json.loads(path.read_text())
        config["contexts"][0]["name"] = "management-kind"
        path.write_text(json.dumps(config))
        result = self.run_script(CLUSTERS.parent / "bootstrap/bootstrap-cluster.sh",
                                 "management-kind", "--context", "management-kind", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("path: bootstrap/apps/management-kind",
                      (self.root / "applied-root.yaml").read_text())
        for call in self.calls:
            if call[0] == "kubectl" and "apply" in call:
                self.assertEqual(call[1:3], ["--context", "management-kind"])
        self.assertFalse(any("use-context" in call for call in self.calls))

    def test_unknown_cluster_has_no_generic_fallback(self):
        result = self.run_script(CLUSTERS.parent / "bootstrap/bootstrap-cluster.sh", "unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No bootstrap source", result.stderr)
        self.assertFalse(any("apply" in call for call in self.calls))

    def test_unconfigured_k3s_cannot_apply(self):
        result = self.run_script(
            CLUSTERS.parent / "bootstrap/bootstrap-cluster.sh", "management-k3s",
            MOCK_RENDERED_PLATFORM="REPLACE_ME",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unconfigured deployment input", result.stderr)
        self.assertFalse(any("apply" in call for call in self.calls))


if __name__ == "__main__":
    unittest.main()
