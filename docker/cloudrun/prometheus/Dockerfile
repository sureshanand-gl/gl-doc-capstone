# Prometheus image packaging repo scrape config for per-service Cloud Run deployment.

ARG PROMETHEUS_BASE_IMAGE=prom/prometheus:v2.54.1
FROM ${PROMETHEUS_BASE_IMAGE}

COPY docker/cloudrun/prometheus/prometheus.yml.tmpl /etc/prometheus/prometheus.yml.tmpl
COPY --chmod=755 docker/cloudrun/prometheus/entrypoint.sh /cloudrun-entrypoint.sh

EXPOSE 9090

ENTRYPOINT ["/bin/sh", "/cloudrun-entrypoint.sh"]
CMD ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus", "--web.listen-address=0.0.0.0:9090"]
