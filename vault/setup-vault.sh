#!/usr/bin/env bash
# One-off bootstrap: Vault Kubernetes auth + policy + role + KV secret for beacon,
# plus the PG4K managed-role password Secret. Idempotent - safe to re-run.
#
# Prereqs: vault CLI logged in with an admin token (VAULT_ADDR/VAULT_TOKEN),
#          oc logged in to the OKD cluster as cluster-admin (unless SKIP_OC=true).
#
#   ./vault/setup-vault.sh                       # defaults below
#   DB_PASSWORD=... ./vault/setup-vault.sh       # use a known password
set -euo pipefail

# ---------- settings (override via env) ----------
APP_NS="${APP_NS:-beacon}"                 # namespace the app runs in
APP_SA="${APP_SA:-beacon}"                 # ServiceAccount the app runs as
VAULT_ROLE="${VAULT_ROLE:-beacon}"
VAULT_AUTH_MOUNT="${VAULT_AUTH_MOUNT:-kubernetes}"
KV_MOUNT="${KV_MOUNT:-secret}"
SECRET_PATH="${SECRET_PATH:-beacon/config}"
AUDIENCE="${AUDIENCE:-vault}"              # must match the projected token audience

PG_NS="${PG_NS:-postgres}"                 # namespace of your PG4K Cluster
PG_CLUSTER="${PG_CLUSTER:-pg-main}"        # name of your PG4K Cluster
DB_NAME="${DB_NAME:-beacon}"
DB_USER="${DB_USER:-beacon}"
DB_HOST="${DB_HOST:-${PG_CLUSTER}-rw.${PG_NS}.svc}"
DB_PASSWORD="${DB_PASSWORD:-}"

# Kubernetes API that Vault calls for TokenReview.
K8S_HOST="${K8S_HOST:-}"                   # e.g. https://api.okd.example.com:6443
K8S_CA_FILE="${K8S_CA_FILE:-}"             # CA bundle for that API
REVIEWER_JWT="${REVIEWER_JWT:-}"           # token of the vault-auth SA (see vault/k8s-auth-reviewer.yaml)
SKIP_OC="${SKIP_OC:-false}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

command -v vault >/dev/null || { echo "vault CLI not found" >&2; exit 1; }
vault token lookup >/dev/null || { echo "vault: not authenticated" >&2; exit 1; }

# ---------- discover cluster details via oc ----------
if [[ "$SKIP_OC" != "true" ]]; then
  command -v oc >/dev/null || { echo "oc not found (or set SKIP_OC=true)" >&2; exit 1; }
  [[ -z "$K8S_HOST" ]] && K8S_HOST="$(oc whoami --show-server)"
  if [[ -z "$K8S_CA_FILE" ]]; then
    K8S_CA_FILE="$(mktemp)"
    # kube-root-ca.crt holds the service-account CA; the external API cert is
    # usually signed by the ingress/API CA - append it if present.
    oc get cm kube-root-ca.crt -n default -o jsonpath='{.data.ca\.crt}' > "$K8S_CA_FILE"
    oc config view --raw --minify --flatten \
      -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' 2>/dev/null \
      | base64 -d >> "$K8S_CA_FILE" 2>/dev/null || true
  fi
  if [[ -z "$REVIEWER_JWT" ]]; then
    log "applying token-reviewer ServiceAccount (vault-auth)"
    oc apply -f "$(dirname "$0")/k8s-auth-reviewer.yaml"
    for _ in {1..20}; do
      REVIEWER_JWT="$(oc get secret vault-auth-token -n vault-auth -o jsonpath='{.data.token}' 2>/dev/null | base64 -d || true)"
      [[ -n "$REVIEWER_JWT" ]] && break; sleep 1
    done
  fi
fi
[[ -n "$K8S_HOST" ]] || { echo "K8S_HOST is required" >&2; exit 1; }

# ---------- KV v2 ----------
if ! vault secrets list -format=json | grep -q "\"${KV_MOUNT}/\""; then
  log "enabling kv-v2 at ${KV_MOUNT}/"
  vault secrets enable -path="$KV_MOUNT" kv-v2
fi

# ---------- Kubernetes auth ----------
if ! vault auth list -format=json | grep -q "\"${VAULT_AUTH_MOUNT}/\""; then
  log "enabling kubernetes auth at ${VAULT_AUTH_MOUNT}/"
  vault auth enable -path="$VAULT_AUTH_MOUNT" kubernetes
fi

log "configuring auth/${VAULT_AUTH_MOUNT} -> ${K8S_HOST}"
args=(kubernetes_host="$K8S_HOST")
[[ -n "$K8S_CA_FILE" ]] && args+=(kubernetes_ca_cert=@"$K8S_CA_FILE")
if [[ -n "$REVIEWER_JWT" ]]; then
  args+=(token_reviewer_jwt="$REVIEWER_JWT")
else
  # No reviewer token: Vault uses the client's own JWT for TokenReview,
  # so the app SA itself needs system:auth-delegator.
  log "no reviewer JWT - client JWT will be used as reviewer"
fi
vault write "auth/${VAULT_AUTH_MOUNT}/config" "${args[@]}" >/dev/null

# ---------- policy ----------
log "writing policy ${VAULT_ROLE}"
vault policy write "$VAULT_ROLE" - <<EOF
# beacon may read (only) its own config secret
path "${KV_MOUNT}/data/${SECRET_PATH}" {
  capabilities = ["read"]
}
path "${KV_MOUNT}/metadata/${SECRET_PATH}" {
  capabilities = ["read"]
}
EOF

# ---------- role bound to the ServiceAccount ----------
log "writing role ${VAULT_ROLE} (sa=${APP_SA}, ns=${APP_NS}, aud=${AUDIENCE})"
vault write "auth/${VAULT_AUTH_MOUNT}/role/${VAULT_ROLE}" \
  bound_service_account_names="$APP_SA" \
  bound_service_account_namespaces="$APP_NS" \
  audience="$AUDIENCE" \
  alias_name_source=serviceaccount_name \
  token_policies="$VAULT_ROLE" \
  token_ttl=1h token_max_ttl=4h >/dev/null

# ---------- the secret ----------
if [[ -z "$DB_PASSWORD" ]]; then
  existing="$(vault kv get -mount="$KV_MOUNT" -field=db_password "$SECRET_PATH" 2>/dev/null || true)"
  DB_PASSWORD="${existing:-$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-28)}"
fi
log "writing ${KV_MOUNT}/${SECRET_PATH}"
vault kv put -mount="$KV_MOUNT" "$SECRET_PATH" \
  db_username="$DB_USER" db_password="$DB_PASSWORD" \
  db_host="$DB_HOST" db_name="$DB_NAME" \
  greeting="hello from vault" >/dev/null

# ---------- PG4K managed-role password Secret ----------
if [[ "$SKIP_OC" != "true" ]]; then
  log "creating Secret ${DB_USER}-db-password in ${PG_NS} for the PG4K managed role"
  oc create secret generic "${DB_USER}-db-password" -n "$PG_NS" \
    --type=kubernetes.io/basic-auth \
    --from-literal=username="$DB_USER" --from-literal=password="$DB_PASSWORD" \
    --dry-run=client -o yaml \
    | oc label --local -f - k8s.enterprisedb.io/reload=true -o yaml \
    | oc apply -f -
fi

cat <<EOF

Done. Next:
  1. Add the managed role to your PG4K Cluster '${PG_CLUSTER}' (see deploy/postgres/README.md):
       spec.managed.roles: [{name: ${DB_USER}, ensure: present, login: true,
                             passwordSecret: {name: ${DB_USER}-db-password}}]
  2. Let Argo CD sync deploy/postgres (Database CR) and deploy/overlays/okd (app).
  To rotate: re-run with a new DB_PASSWORD - PG4K reloads the role password and
  the app picks up the new Vault version within SECRET_REFRESH_SECONDS.
EOF
