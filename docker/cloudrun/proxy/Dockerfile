# Nginx reverse-proxy image exposing app and protecting observability endpoints on Cloud Run.

ARG PROXY_BASE_IMAGE=nginx:1.27-alpine
FROM ${PROXY_BASE_IMAGE}

RUN apk add --no-cache apache2-utils gettext

COPY docker/cloudrun/proxy/default.conf.template /etc/nginx/default.conf.template
COPY docker/cloudrun/proxy/entrypoint.sh /cloudrun-entrypoint.sh

RUN sed -i 's/\r$//' /cloudrun-entrypoint.sh \
    && chmod +x /cloudrun-entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/bin/sh", "/cloudrun-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
