# Grafana image layering repo-managed provisioning onto Cloud Run compatible base.

ARG GRAFANA_BASE_IMAGE=grafana/grafana-oss:11.2.0
FROM ${GRAFANA_BASE_IMAGE}

COPY docker/cloudrun/grafana/provisioning /etc/grafana/provisioning
COPY docker/grafana/dashboards /var/lib/grafana/dashboards

EXPOSE 3000
