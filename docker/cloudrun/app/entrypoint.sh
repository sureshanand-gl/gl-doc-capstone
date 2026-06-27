#!/usr/bin/env sh

set -eu

cp /etc/nginx/default.conf.template /etc/nginx/conf.d/default.conf

uv run --no-sync streamlit run app_frontend.py \
  --server.headless true \
  --server.port 8502 \
  --server.address 0.0.0.0 &

exec nginx -g 'daemon off;'
