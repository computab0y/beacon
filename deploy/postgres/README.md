# EDB PGD wiring for beacon

The database is the EDB Postgres Distributed group `homelab-pgd`
(namespace `pgd-group-homelab`). PGD replicates only its own database, `app`,
so beacon does **not** get a database of its own: it uses schema `beacon`
inside `app`, created by the migration Job (`migrations/V*.sql`).
The app connects through the PGD connection manager
`homelab-pgd-proxy.pgd-group-homelab.svc:6432`, which routes to the current
write leader.

The login role `beacon` is a **managed role** on the `PGDGroup` (the operator
pushes `spec.cnp` to every node `Cluster`; don't patch the node Clusters, they
are owned by the PGDGroup). Its password lives in Vault (`secret/beacon/config`)
and is mirrored into the Secret `beacon-db-password` in `pgd-group-homelab` by
`vault/setup-vault.sh` (labelled `k8s.enterprisedb.io/reload=true`, so the
password is re-applied on change).

Add this to the PGDGroup manifest (merge it into any existing `roles` list):

```yaml
spec:
  cnp:
    managed:
      roles:
        - name: beacon
          ensure: present
          login: true
          connectionLimit: 20
          passwordSecret:
            name: beacon-db-password
```

Then, once, let the role create its schema in `app` (run on the write leader;
PGD replicates the DDL):

```bash
LEADER=$(oc -n pgd-group-homelab get pgdgroup homelab-pgd -o jsonpath='{.status.PGD.writeLeadLastDetected}')
oc -n pgd-group-homelab exec ${LEADER}-1 -c postgres -- \
  psql -d app -c 'GRANT CREATE, CONNECT ON DATABASE app TO beacon'
```

`pg_hba`: the default rules allow password (scram) auth over TLS from any pod,
which is what the app uses (`DB_SSLMODE=require`).
