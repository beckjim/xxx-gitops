#!/usr/bin/env python3
"""Validate a configured cluster overlay without contacting a cluster."""

import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys

try:
    import yaml
except ImportError:
    sys.exit("ERROR: Install profile validation dependencies: pip install -r clusters/requirements.txt")


ROOT = Path(__file__).resolve().parents[1]


def render(path):
    result = subprocess.run(
        ["kubectl", "kustomize", str(path)], text=True, capture_output=True, check=True,
    )
    if "REPLACE_ME" in result.stdout:
        raise ValueError(f"Unconfigured deployment input in {path.relative_to(ROOT)}")
    resources = [item for item in yaml.safe_load_all(result.stdout) if item is not None]
    if any(not isinstance(item, dict) or not item.get("kind") for item in resources):
        raise ValueError(f"Invalid Kubernetes resources in {path.relative_to(ROOT)}")
    return resources


def validate(cluster_id):
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", cluster_id):
        raise ValueError("Invalid cluster ID")
    settings_path = ROOT / "clusters" / cluster_id / "settings.json"
    settings = json.loads(settings_path.read_text())
    if settings["context"] != cluster_id:
        raise ValueError("Profile context must match its unique cluster ID")
    platform = render(ROOT / "platform-overlays" / cluster_id)
    if not any(item and item.get("kind") == "Application" for item in platform):
        raise ValueError("Platform overlay must contain Applications")
    if settings["profile"] == "kind":
        return
    if settings["profile"] != "k3s-lan":
        raise ValueError("Unsupported infrastructure profile")
    node = ipaddress.IPv4Address(settings["nodeAddress"])
    gateway = ipaddress.IPv4Address(settings["gatewayAddress"])
    start, end = [ipaddress.IPv4Address(value) for value in settings["pool"].split("-")]
    if not start <= gateway <= end or start <= node <= end or end < start:
        raise ValueError("Pool must contain the gateway IP and exclude the node IP")
    if any(ip.is_loopback or ip.is_unspecified or ip.is_multicast for ip in (node, gateway, start, end)):
        raise ValueError("LAN addresses must be unicast addresses")
    pools = render(ROOT / "platform/metallb-config" / cluster_id)
    pool = next((item for item in pools if item["kind"] == "IPAddressPool"), None)
    if pool is None:
        raise ValueError("MetalLB overlay must contain an IPAddressPool")
    if pool["spec"]["addresses"] != [settings["pool"]]:
        raise ValueError("MetalLB pool does not match cluster settings")
    applications = {item["metadata"]["name"]: item for item in platform if item["kind"] == "Application"}
    parameters = applications["nginx-gateway"]["spec"]["source"]["helm"]["parameters"]
    values = {item["name"]: item["value"] for item in parameters}
    if values["nginx.service.loadBalancerIP"] != str(gateway):
        raise ValueError("Gateway service IP does not match cluster settings")
    routes = render(ROOT / "platform/routes" / cluster_id)
    certs = {item["metadata"]["name"]: item for item in routes if item["kind"] == "Certificate"}
    for certificate, dns_key, address in (
        ("platform-tls", "platformFqdn", gateway),
        ("argocd-server-tls", "argocdFqdn", node),
    ):
        fqdn = settings[dns_key]
        if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]*", fqdn):
            raise ValueError(f"Invalid FQDN: {fqdn}")
        spec = certs[certificate]["spec"]
        if spec["dnsNames"] != [fqdn] or spec["ipAddresses"] != [str(address)]:
            raise ValueError(f"{certificate} SANs do not match cluster settings")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: validate-profile.py <cluster-id>")
    try:
        validate(sys.argv[1])
    except (ValueError, KeyError, OSError, StopIteration, yaml.YAMLError) as error:
        sys.exit(f"ERROR: {error}")
    except subprocess.CalledProcessError as error:
        sys.exit(f"ERROR: Cannot render profile: {error.stderr}")
    print(f"Profile {sys.argv[1]} validated. DHCP reservations and LAN DNS still require operator verification.")
