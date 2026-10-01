# The CopilotKit runtime: internal network only, no credentials, no telemetry. Context: extensions/conversationalbi/runtime.
FROM node:24-slim
WORKDIR /runtime
COPY package.json package-lock.json ./
RUN SCARF_ANALYTICS=false npm ci --ignore-scripts --omit=dev --no-audit --no-fund
COPY server.mjs ./
USER node
ENV NODE_ENV=production COPILOTKIT_TELEMETRY_DISABLED=true DO_NOT_TRACK=1 SCARF_ANALYTICS=false PORT=4000
EXPOSE 4000
CMD ["node", "server.mjs"]
