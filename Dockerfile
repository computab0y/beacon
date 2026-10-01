# ---- build: install deps into a venv using the full UBI Python image ----
FROM registry.access.redhat.com/ubi9/python-312:latest AS build
USER 0
WORKDIR /opt/app-root/src
COPY requirements.txt ./
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- runtime: minimal UBI Python, non-root, OKD random-UID friendly ----
FROM registry.access.redhat.com/ubi9/python-312-minimal:latest

ARG APP_VERSION=0.0.0-dev
ARG GIT_COMMIT=unknown

LABEL org.opencontainers.image.title="beacon" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.source="https://github.com/OWNER/beacon"

WORKDIR /opt/app-root/src
COPY --from=build --chown=1001:0 /opt/venv /opt/venv
COPY --chown=1001:0 VERSION ./
COPY --chown=1001:0 app ./app
COPY --chown=1001:0 migrations ./migrations
# OKD runs pods with a random UID in group 0: give the group the owner's rights.
USER 0
RUN chmod -R g=u /opt/app-root/src /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    APP_VERSION=${APP_VERSION} \
    GIT_COMMIT=${GIT_COMMIT} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 1001
EXPOSE 8080

# One worker per pod: scale with replicas so Prometheus sees one process per pod.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--no-access-log", "--timeout-graceful-shutdown", "20"]
