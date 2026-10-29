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
    for section in ("clusters", "users", "contexts"):
        config[section][0]["name"] = "kind-management"
    config["contexts"][0]["context"] = {"cluster": "kind-management", "user": "kind-management"}
    config["current-context"] = "kind-management"
    path = Path.home() / ".kube/config"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(config))
elif tool in ("k3s", "kubectl"):
    if "rename-cluster" in args or "rename-user" in args:
        sys.exit("error: unknown command")
    if tool == "kubectl":
        path = Path.home() / ".kube/config"
        if os.environ.get("MOCK_REAL_K3S") and "config" in args:
            sys.exit(subprocess.call(
                [os.environ["MOCK_REAL_K3S"], "kubectl", *args],
                env={**os.environ, "KUBECONFIG": str(path)},
            ))
        config = json.loads(path.read_text()) if path.exists() and "config" in args else {}
        if "get-contexts" in args:
            for entry in config.get("contexts", []):
                if "management" not in args or entry["name"] == "management":
                    print(entry["name"])
        elif "rename-context" in args:
            for entry in config["contexts"]:
                if entry["name"] == "kind-management":
                    entry["name"] = "management"
            if config["current-context"] == "kind-management":
                config["current-context"] = "management"
            path.write_text(json.dumps(config))
        elif "use-context" in args:
            config["current-context"] = args[-1]
            path.write_text(json.dumps(config))
        elif "view" in args:
            context = next(e["context"] for e in config["contexts"] if e["name"] == "management")
            cluster = next(e["cluster"] for e in config["clusters"] if e["name"] == context["cluster"])
            print(cluster["server"])
        elif "port-forward" in args:
            Path(os.environ["MOCK_FORWARD_PID"]).write_text(str(os.getpid()))
            if os.environ.get("MOCK_FORWARD_FAIL"):
                sys.exit("Mock port-forward failed")
            print("Forwarding from 127.0.0.1:8080 -> 8080", flush=True)
            while True:
                time.sleep(1)
    if "view" in args:
        if tool == "kubectl":
            pass
        elif os.environ.get("MOCK_REAL_K3S"):
            sys.exit(subprocess.call([os.environ["MOCK_REAL_K3S"], *args]))
        elif "--kubeconfig" in args:
            print(Path(args[args.index("--kubeconfig") + 1]).read_text())
        else:
            configs = [json.loads(Path(path).read_text()) for path in os.environ["KUBECONFIG"].split(":")]
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
                     "kind", "helm", "argocd", "sleep"):
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


@unittest.skipUnless(
    Path("/run/systemd/system").is_dir() or os.access("/sbin/openrc-run", os.X_OK),
    "The provisioner requires a Linux host with systemd or OpenRC",
)
class SetupK3sTests(SetupTests):
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
        self.assertEqual(merged["current-context"], "management")
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

    def test_resume_after_kubeconfig_failure(self):
        self.config.parent.mkdir()
        self.config.write_text(json.dumps(K3S_CONFIG))
        for _ in range(2):
            result = self.run_setup("--resume", MOCK_EXISTING_K3S="1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(any(call[0] == "curl" for call in self.calls))
            self.assertFalse(any(call[:2] == ["sudo", "env"] for call in self.calls))
            self.assertEqual(
                json.loads(self.config.read_text())["current-context"], "management"
            )

    def test_resume_requires_existing_kubeconfig(self):
        result = self.run_setup("--resume")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--resume requires", result.stderr)
        self.assertFalse(any(call[0] == "curl" for call in self.calls))

    def test_resume_rejects_unexpected_kubeconfig_without_overwriting(self):
        self.config.parent.mkdir()
        config = {**K3S_CONFIG, "clusters": []}
        original = json.dumps(config)
        self.config.write_text(original)
        result = self.run_setup("--resume")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected a single K3s entry", result.stderr)
        self.assertEqual(self.config.read_text(), original)
        self.assertFalse((self.root / ".kube" / "config").exists())
        self.assertEqual(list(self.root.glob(".kube/.k3s-config.*")), [])

    def test_symlink_kubeconfig_is_not_overwritten(self):
        self.config.parent.mkdir()
        original = self.root / "original-config"
        original.write_text(json.dumps(K3S_CONFIG))
        self.config.symlink_to(original)
        result = self.run_setup("--resume")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing symlink", result.stderr)
        self.assertEqual(json.loads(original.read_text()), K3S_CONFIG)

    @unittest.skipUnless(shutil.which("k3s"), "K3s is not installed")
    def test_invalid_user_config_merge_preserves_original(self):
        self.config.parent.mkdir()
        self.config.write_text(json.dumps(K3S_CONFIG))
        user_config = self.config.parent / "config"
        original = "not valid: ["
        user_config.write_text(original)
        result = self.run_setup("--resume", MOCK_REAL_K3S=shutil.which("k3s"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(user_config.read_text(), original)
        self.assertEqual(list(self.root.glob(".kube/.config.*")), [])
        self.assertNotIn("Management cluster ready.", result.stdout)

    @unittest.skipUnless(shutil.which("k3s"), "K3s is not installed")
    def test_management_entries_replace_only_matching_names(self):
        path = self.management_config("https://192.168.10.99:6443")
        original = json.loads(path.read_text())
        result = self.run_setup(MOCK_REAL_K3S=shutil.which("k3s"))
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(path.read_text())
        management = next(e for e in config["clusters"] if e["name"] == "management")
        self.assertEqual(management["cluster"]["server"], "https://192.168.10.20:6443")
        for section in ("clusters", "contexts"):
            production = next(e for e in config[section] if e["name"] == "production")
            self.assertIn(production, original[section])
        self.assertEqual(config["current-context"], "management")

    def test_failures_do_not_report_success(self):
        for failure in ("MOCK_DOWNLOAD_FAIL", "MOCK_WAIT_FAIL"):
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

    def test_resume_legacy_names(self):
        self.config.parent.mkdir()
        config = json.loads(json.dumps(K3S_CONFIG))
        for section in ("clusters", "users", "contexts"):
            config[section][0]["name"] = "k3s-management"
        config["contexts"][0]["context"] = {
            "cluster": "k3s-management", "user": "k3s-management",
        }
        config["current-context"] = "k3s-management"
        self.config.write_text(json.dumps(config))
        result = self.run_setup("--resume")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.config.read_text())["current-context"], "management")


class SetupKindTests(SetupTests):
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
        self.assertEqual(config["current-context"], "management")
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
        for args in ((), ("other",), ("kind", "--resume"), ("k3s", "--other"),
                     ("k3s", "--resume", "extra")):
            with self.subTest(args=args):
                result = self.run_script(SCRIPT, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Usage:", result.stderr)
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

    def test_nodeport_rerun(self):
        for _ in range(2):
            result = self.run_argocd("--nodeport")
            self.assertEqual(result.returncode, 0, result.stderr)
            helm = next(call for call in self.calls if call[:2] == ["helm", "upgrade"])
            self.assertIn("--install", helm)
            self.assertIn("server.service.nodePortHttps=30443", helm)

    @unittest.skipUnless(shutil.which("k3s"), "K3s is not installed")
    def test_real_offline_management_endpoint(self):
        self.env["MOCK_REAL_K3S"] = shutil.which("k3s")
        result = self.run_argocd("--nodeport")
        self.assertEqual(result.returncode, 0, result.stderr)
        login = next(call for call in self.calls if call[:2] == ["argocd", "login"])
        self.assertIn("192.168.10.20:30443", login)

    def test_nodeport_readiness_failure(self):
        result = self.run_argocd("--nodeport", MOCK_ACCESS_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not reachable", result.stderr)
        self.assertFalse(any(call[0] == "argocd" for call in self.calls))
        self.assertEqual(list(self.root.glob("tmp.*")), [])

    def test_invalid_input_before_helm(self):
        for overrides in ({"ARGOCD_HTTPS_NODE_PORT": "29999"},
                          {"ARGOCD_HTTPS_NODE_PORT": "65536"},
                          {"ARGOCD_HTTPS_NODE_PORT": "abc"}):
            with self.subTest(overrides=overrides):
                result = self.run_argocd("--nodeport", **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call[0] == "helm" for call in self.calls))
        result = self.run_argocd("--other")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls, [])

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


if __name__ == "__main__":
    unittest.main()
