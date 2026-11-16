FROM docker.io/library/golang@sha256:5e8afd493a7124a8586e134d9b532731161d4b9becf9dee5a5956b107426f93d AS build
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY web ./web
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /saferus ./cmd/console
FROM scratch
COPY --from=build /saferus /saferus
USER 1000:1000
EXPOSE 8443
ENTRYPOINT ["/saferus"]
