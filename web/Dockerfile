# Dev image for the web monorepo. Bakes pnpm in at build time so
# `docker compose up` doesn't re-download it on every container start.
# Dependency install still happens in the entrypoint command in
# docker-compose.yml because the node_modules volume is mounted at
# start, hiding anything baked into the image.

FROM node:24-alpine

# `corepack enable` wires the pnpm shim; `corepack prepare ... --activate`
# fetches the pinned version once at build time. Pin this in lockstep
# with `packageManager` in web/package.json when bumping pnpm.
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate

WORKDIR /web
