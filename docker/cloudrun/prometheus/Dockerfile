# Prometheus image packaging repo scrape config for Cloud Run sidecar deployment.

ARG PROMETHEUS_BASE_IMAGE=prom/prometheus:v2.54.1
FROM ${PROMETHEUS_BASE_IMAGE}

COPY docker/cloudrun/prometheus/prometheus.yml /etc/prometheus/prometheus.yml

EXPOSE 9090

CMD ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus", "--web.listen-address=0.0.0.0:9090", "--web.external-url=/prometheus/", "--web.route-prefix=/prometheus"]
