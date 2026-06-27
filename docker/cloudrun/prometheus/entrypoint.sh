#!/usr/bin/env sh

set -eu

if [ -z "${APP_METRICS_HOST:-}" ]; then
  echo "APP_METRICS_HOST is required" >&2
  exit 1
fi

if [ -z "${PUSHGATEWAY_HOST:-}" ]; then
  echo "PUSHGATEWAY_HOST is required" >&2
  exit 1
fi

sed \
  -e "s|__APP_METRICS_HOST__|${APP_METRICS_HOST}|g" \
  -e "s|__PUSHGATEWAY_HOST__|${PUSHGATEWAY_HOST}|g" \
  /etc/prometheus/prometheus.yml.tmpl > /etc/prometheus/prometheus.yml

exec /bin/prometheus "$@"
