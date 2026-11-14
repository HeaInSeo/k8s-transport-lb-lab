ARG GO_VERSION=1.26.11
FROM golang:${GO_VERSION}-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /lab ./cmd/lab
FROM scratch
COPY --from=build /lab /lab
USER 65532:65532
ENTRYPOINT ["/lab"]
