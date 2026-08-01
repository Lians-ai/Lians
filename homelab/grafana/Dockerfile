FROM node:22-alpine AS plugin-builder

WORKDIR /plugin
COPY integrations/grafana-lians-app/package.json integrations/grafana-lians-app/package-lock.json ./
RUN npm ci --ignore-scripts
COPY integrations/grafana-lians-app/ ./
RUN npm run typecheck && npm run build

FROM grafana/grafana:12.4.6

USER root
COPY --from=plugin-builder --chown=472:0 /plugin/dist /var/lib/grafana/plugins/lians-lians-app
COPY --chown=472:0 homelab/grafana/provisioning /etc/grafana/provisioning
COPY --chown=472:0 homelab/grafana/dashboards /var/lib/grafana/dashboards
RUN mkdir -p /var/lib/grafana-data && chown 472:0 /var/lib/grafana-data
USER 472
