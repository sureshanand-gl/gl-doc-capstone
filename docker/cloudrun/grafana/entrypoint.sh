#!/usr/bin/env sh

set -eu

if [ -z "${GF_PROMETHEUS_URL:-}" ]; then
  echo "GF_PROMETHEUS_URL is required" >&2
  exit 1
fi

sed \
  -e "s|__GF_PROMETHEUS_URL__|${GF_PROMETHEUS_URL}|g" \
  /etc/grafana/provisioning/datasources/prometheus.yml.tmpl > /etc/grafana/provisioning/datasources/prometheus.yml

exec "$@"
