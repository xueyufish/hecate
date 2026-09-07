# Hecate on Kubernetes (catalog 13.2)

Minimal one-command deployment mirroring `docker/docker-compose.yml`:
PostgreSQL 16 + Qdrant + MinIO + Temporal + the Hecate app, with a migration
Job that runs `alembic upgrade head` before the app starts.

## Apply

```bash
kubectl apply -f deploy/k8s/00-namespace.yaml
kubectl apply -f deploy/k8s/
```

(Namespaced resources carry `namespace: hecate`; the namespace file applies
first.)

## Secrets

Credentials default to the compose defaults for local evaluation. For anything
shared or internet-reachable, create a real secret and reference it instead of
the inline env values in `10-postgres.yaml` / `30-hecate-app.yaml`:

```bash
kubectl -n hecate create secret generic hecate-secrets \
  --from-literal=POSTGRES_PASSWORD=change-me \
  --from-literal=HECATE_API_KEY=change-me
```

## App image

`20-hecate-migrate-job.yaml` and `30-hecate-app.yaml` use
`image: hecate/hecate:latest` — build and push it first
(`docker build -f docker/Dockerfile -t <registry>/hecate:latest .`), or set
`imagePullPolicy: Never` for a local cluster (kind/minikube).

## Access

The app is exposed via ClusterIP on port 8000. For external access, add an
Ingress or change `30-hecate-app.yaml` to `type: NodePort` / `LoadBalancer`.
