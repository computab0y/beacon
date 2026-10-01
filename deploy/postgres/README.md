# PG4K wiring for beacon

The app's DB login (`beacon`) is a **managed role** on your existing PG4K `Cluster`.
Its password lives in Vault (`secret/beacon/config`) and is mirrored into the
Secret `beacon-db-password` in the cluster's namespace by `vault/setup-vault.sh`
(labelled `k8s.enterprisedb.io/reload=true`, so PG4K re-applies it on change).

Add this to your Cluster manifest (wherever it is managed):

```yaml
spec:
  managed:
    roles:
      - name: beacon
        ensure: present
        login: true
        connectionLimit: 20
        passwordSecret:
          name: beacon-db-password
```

Then `database.yaml` (Argo CD app `beacon-db`) creates database `beacon`
owned by that role, and the app's migration Job creates schema `beacon` and its
tables (`migrations/V*.sql`).

Older PG4K without the `Database` CRD:

```bash
oc exec -n postgres pg-main-1 -c postgres -- psql -c 'CREATE DATABASE beacon OWNER beacon'
```

`pg_hba`: the default PG4K rules allow password (scram) auth over TLS from any
pod, which is what the app uses (`DB_SSLMODE=require`).
