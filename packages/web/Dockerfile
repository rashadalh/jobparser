# syntax=docker/dockerfile:1
# --- jdparser web (Next.js 16 / React 19), bun-managed ---
FROM oven/bun:1.2.2

ENV NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

# Dependency layer — cached unless package.json / bun.lock change.
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

# App source (node_modules / .next / .env* excluded via .dockerignore).
COPY . .

# NEXT_PUBLIC_* is inlined into the CLIENT bundle at BUILD time. The browser runs on
# the host, so this must be a host-reachable URL (the compose-mapped API port).
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}
RUN bun run build

EXPOSE 3000
CMD ["bunx", "next", "start", "-H", "0.0.0.0", "-p", "3000"]
