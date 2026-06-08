# Prod image for the email-render sidecar. Build context is the WEB WORKSPACE
# (web/), so pnpm resolves against the workspace lockfile. email-render has no
# @magpie/* workspace deps (just react-email + react + tsx), so a `pnpm deploy`
# yields a self-contained bundle we copy into a clean runner. Runs via tsx (no
# build step) — devDeps are included so tsx is present.
FROM node:24-alpine AS builder
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate
WORKDIR /web

COPY . .
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --filter @magpie/email-render... \
 && pnpm --filter @magpie/email-render --prod=false deploy /out

FROM node:24-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /out /app

# Drop root: node:alpine ships a non-privileged `node` user (uid 1000). The
# bundle is world-readable and tsx transpiles in-memory (no disk writes).
USER node

EXPOSE 3010
# tsx ships in node_modules/.bin; run the server directly (no pnpm at runtime).
CMD ["node_modules/.bin/tsx", "src/server.ts"]
