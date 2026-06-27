#!/usr/bin/env sh
# Runtime entrypoint that installs nginx config before start.

set -eu

cp /etc/nginx/default.conf.template /etc/nginx/conf.d/default.conf

exec "$@"
