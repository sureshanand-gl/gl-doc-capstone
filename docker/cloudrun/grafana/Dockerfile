# Grafana image layering repo-managed provisioning onto Cloud Run compatible base.

ARG GRAFANA_BASE_IMAGE=grafana/grafana-oss:11.2.0
FROM ${GRAFANA_BASE_IMAGE}

COPY docker/cloudrun/grafana/provisioning/dashboards/dashboards.yml /etc/grafana/provisioning/dashboards/dashboards.yml
COPY docker/cloudrun/grafana/provisioning/datasources/prometheus.yml.tmpl /etc/grafana/provisioning/datasources/prometheus.yml.tmpl
COPY docker/grafana/dashboards /var/lib/grafana/dashboards
COPY --chmod=755 docker/cloudrun/grafana/entrypoint.sh /cloudrun-entrypoint.sh

EXPOSE 3000

ENTRYPOINT ["/bin/sh", "/cloudrun-entrypoint.sh"]
CMD ["/run.sh"]
