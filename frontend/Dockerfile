FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm config set registry https://registry.npmmirror.com && npm ci

FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ARG BACKEND_INTERNAL_URL=http://backend:8000
ENV BACKEND_INTERNAL_URL=$BACKEND_INTERNAL_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup --system --gid 10001 autofish \
    && adduser --system --uid 10001 --ingroup autofish autofish
COPY --from=builder --chown=autofish:autofish /app/public ./public
COPY --from=builder --chown=autofish:autofish /app/.next/standalone ./
COPY --from=builder --chown=autofish:autofish /app/.next/static ./.next/static
USER autofish
EXPOSE 3000
CMD ["node", "server.js"]
