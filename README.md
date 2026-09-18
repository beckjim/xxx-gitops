# Argo CD AppProjects

This folder contains AppProject definitions that are applied before bootstrap Applications.

## Guardrail: default project is locked down

The `default` AppProject in this repository is intentionally deny-all.

- File: `default.yaml`
- No allowed `sourceRepos`
- No allowed `destinations`
- All namespace and cluster resources blocked

Do not place any Application in the `default` project.
Use explicit projects such as `platform-admins`, `app1`, or `security`.

## Apply order

1. Apply AppProjects first:
   `kubectl apply -k projects`
2. Apply bootstrap root application:
   `kubectl apply -f bootstrap/root-app.yaml`
