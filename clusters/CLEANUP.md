## Cluster Cleanup

### Delete a k3s cluster

1. Stop and uninstall k3s:

	```bash
	sudo /usr/local/bin/k3s-uninstall.sh
	```

2. If installed as an agent node, uninstall the agent:

	```bash
	sudo /usr/local/bin/k3s-agent-uninstall.sh
	```

3. (Optional) Remove leftover kube config/context entries:

	```bash
	kubectl config delete-context default || true
	kubectl config delete-cluster default || true
	kubectl config delete-user default || true
	```

### Delete a kind cluster

1. List existing kind clusters:

	```bash
	kind get clusters
	```

2. Delete a specific cluster:

	```bash
	kind delete cluster --name <cluster-name>
	```

3. Delete all kind clusters:

	```bash
	for c in $(kind get clusters); do
	  kind delete cluster --name "$c"
	done
	```
