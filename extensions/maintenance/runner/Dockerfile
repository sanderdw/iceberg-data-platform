# The isolated run image: one static Go binary on iceberg-go. Built with context extensions/maintenance/runner.
# Each run reaches only the platform's catalog and storage, on an internal network.
FROM golang:1.27.4-bookworm AS build
WORKDIR /src
ENV CGO_ENABLED=0 GOFLAGS=-trimpath
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY job.go health.go maintain.go main.go remote.go ./
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go build -ldflags="-s -w" -o /out/maintenance-runner .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/maintenance-runner /usr/local/bin/maintenance-runner
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/maintenance-runner"]
