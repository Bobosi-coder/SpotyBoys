# ============================================================
# SpotyBoys — Navidrome fork with Recommendations page
#
# Build:
#   docker build -t spotiboys-navidrome:latest .
#
# Pinned to a stable Navidrome release; bump NAVIDROME_TAG to upgrade.
# ============================================================

# ── Stage 1: build the patched UI ───────────────────────────────────────────
FROM node:20-alpine AS ui-builder

ARG NAVIDROME_TAG=v0.54.5

RUN apk add --no-cache git patch

# Clone Navidrome at the pinned tag
RUN git clone --depth 1 --branch ${NAVIDROME_TAG} \
    https://github.com/navidrome/navidrome.git /navidrome

WORKDIR /navidrome/ui

# 1. Overlay our new source files
COPY ui/src/recommendations/ ./src/recommendations/
COPY ui/src/eventbridge/     ./src/eventbridge/

# 2. Apply diffs to existing Navidrome files
COPY patches/ /patches/
RUN patch -p1 src/routes.jsx                   < /patches/routes.jsx.patch \
 && patch -p1 src/App.jsx                       < /patches/App.jsx.patch \
 && patch -p1 src/layout/Menu.jsx               < /patches/layout/Menu.jsx.patch \
 && patch -p1 src/reducers/playerReducer.js     < /patches/reducers/playerReducer.js.patch

# 3. Install dependencies and build
RUN npm ci && npm run build

# ── Stage 2: build the Go binary ────────────────────────────────────────────
# Use Debian (glibc) to match the cross-taglib static library ABI.
# curl is used — busybox wget cannot follow GitHub release CDN redirects.
FROM golang:1.23-bookworm AS go-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ pkg-config zlib1g-dev curl && rm -rf /var/lib/apt/lists/*

# Download official pre-built static taglib 2.0.2 (navidrome/cross-taglib)
# Detect arch so the same Dockerfile works on amd64 and arm64 hosts.
RUN ARCH=$(uname -m) && \
    case "$ARCH" in \
      x86_64)  PLATFORM=linux-amd64 ;; \
      aarch64) PLATFORM=linux-arm64 ;; \
      *) echo "Unsupported arch: $ARCH" && exit 1 ;; \
    esac && \
    curl -fSL https://github.com/navidrome/cross-taglib/releases/download/v2.0.2-1/taglib-${PLATFORM}.tar.gz \
        -o /tmp/taglib.tar.gz \
    && mkdir /taglib \
    && tar -xzf /tmp/taglib.tar.gz -C /taglib

WORKDIR /navidrome

# Copy the full Navidrome source (with our built UI embedded)
COPY --from=ui-builder /navidrome /navidrome

WORKDIR /navidrome
RUN PKG_CONFIG_PATH=/taglib/lib/pkgconfig \
    CGO_ENABLED=1 \
    go build -tags=netgo \
    -ldflags="-extldflags '-static -latomic' -w -s" \
    -o navidrome .

# ── Stage 3: final minimal image ─────────────────────────────────────────────
FROM alpine:3.21

RUN apk add --no-cache ffmpeg mpv

COPY --from=go-builder /navidrome/navidrome /app/navidrome

VOLUME ["/music", "/data"]

EXPOSE 4533

ENTRYPOINT ["/app/navidrome"]
