FROM node:22-alpine AS dependencies
WORKDIR /workspace/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /workspace
COPY --from=dependencies /workspace/apps/web/node_modules ./apps/web/node_modules
COPY apps/web ./apps/web
COPY packages/shared-types ./packages/shared-types
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
WORKDIR /workspace/apps/web
RUN npm run build

FROM node:22-alpine AS runner
ENV NODE_ENV=production
WORKDIR /workspace/apps/web
COPY --from=builder /workspace/apps/web/package.json ./package.json
COPY --from=builder /workspace/apps/web/node_modules ./node_modules
COPY --from=builder /workspace/apps/web/.next ./.next
COPY --from=builder /workspace/apps/web/public ./public

EXPOSE 3000
CMD ["npm", "start"]
