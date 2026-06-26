#!/usr/bin/env sh
set -eu

if [ -z "${OPS_BASIC_AUTH_USER:-}" ]; then
  echo "OPS_BASIC_AUTH_USER is required" >&2
  exit 1
fi

if [ -z "${OPS_BASIC_AUTH_PASSWORD:-}" ]; then
  echo "OPS_BASIC_AUTH_PASSWORD is required" >&2
  exit 1
fi

htpasswd -bc /etc/nginx/.htpasswd "$OPS_BASIC_AUTH_USER" "$OPS_BASIC_AUTH_PASSWORD" >/dev/null
cp /etc/nginx/default.conf.template /etc/nginx/conf.d/default.conf

exec "$@"
