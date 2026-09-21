# Cluster Helpers

Use a single helper script for all environments.

## Command

Run from repository root:

- `./clusters/bootstrap-cluster.sh <kube-context>`
- `./clusters/bootstrap-cluster.sh <kube-context> --yes`

Examples:

- `./clusters/bootstrap-cluster.sh dev`
- `./clusters/bootstrap-cluster.sh test`
- `./clusters/bootstrap-cluster.sh prod`

For automation/non-interactive runs, use:

- `./clusters/bootstrap-cluster.sh prod --yes`

The script applies:

1. `projects/` (AppProjects)
2. `bootstrap/root-app.yaml` (root Application)

## Production Safety Guard

If the context name includes `prod` or `production`, the script asks for explicit confirmation.
Type `prod` to continue, or pass `--yes` to skip the prompt.
