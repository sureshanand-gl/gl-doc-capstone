ARG PROXY_BASE_IMAGE=nginx:1.27-alpine
FROM ${PROXY_BASE_IMAGE}

RUN apk add --no-cache apache2-utils gettext

COPY docker/cloudrun/proxy/default.conf.template /etc/nginx/default.conf.template
COPY docker/cloudrun/proxy/entrypoint.sh /cloudrun-entrypoint.sh

RUN chmod +x /cloudrun-entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/cloudrun-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
