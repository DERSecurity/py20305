# Running in a container

The client is one long-lived process that makes outbound connections and reads
a certificate, which makes it a straightforward thing to containerize. The
image carries the client and nothing else: no configuration, no certificates,
no source tree.

## Build

```bash
docker build -t py20305 .
```

The build produces a wheel in one stage and installs it in another, so what
runs is the same artifact `pip install py20305` would fetch rather than a
source checkout.

By default the image includes the `cli`, `sunspec` and `mqtt` extras. The
management API is deliberately not among them — it serves on a port, and an
image that never serves it should not carry a web framework. Build with a
different set if you need it:

```bash
docker build --build-arg EXTRAS=cli,sunspec,mqtt,api -t py20305 .
```

## Run

Mount the directory holding your configuration file and the certificate it
names. Nothing in the image needs editing:

```bash
docker run -d --name py20305 \
  -v "$PWD/config:/etc/py20305:ro" \
  py20305
```

Read-only, because the client never writes there. Relative certificate paths in
the configuration file resolve against the file's own directory, so
`client_cert: client.pem` refers to `config/client.pem` on the host regardless
of where the container's working directory happens to be.

Validate before starting for real. This connects to nothing:

```bash
docker run --rm -v "$PWD/config:/etc/py20305:ro" \
  py20305 --config /etc/py20305/client.yaml --check
```

There is a compose file at
[`examples/docker-compose.yml`](https://github.com/DERSecurity/py20305/blob/main/examples/docker-compose.yml).

## What the image does

**Runs as a non-root user** (`py20305`, uid 10005). The process needs no
privilege beyond making outbound connections and reading its certificate. The
uid is fixed so a mounted configuration directory can be made readable to it
without inspecting the image first.

**Receives signals directly.** The entrypoint is exec-form, so `docker stop`
delivers `SIGTERM` to the client rather than to a shell that would not forward
it. The client then shuts down in order — finishing what it is doing and
closing its session. Allow it more than the ten-second default:
`--stop-timeout 30`.

**Matches mounted files by id, not by name.** A private key should not be
world-readable, so it has to be readable by uid 10005 specifically: either
`chown 10005:10005` it, build with `--build-arg UID=... --build-arg GID=...`,
or run with `--user "$(id -u):$(id -g)"` to borrow the host account that owns
it already.

**Exits 2 on a configuration error** and 3 when it could not reach the server.
The distinction is what lets a supervisor decline to restart the first: no
number of restarts fixes a malformed certificate path, and a restart loop
hides the message that says what was wrong. Configure your restart policy so a
2 stays visible.

## Reaching the management API

Enabling the API in a container takes one step more than it looks. The runner
binds `api.host` directly, so the documented `127.0.0.1` is the *container's*
loopback and no published port reaches it. Bind `0.0.0.0` inside and publish to
the host's loopback:

```yaml
api:
  enabled: true
  host: 0.0.0.0
```

```bash
docker run -d -p 127.0.0.1:8080:8080   -v "$PWD/config:/etc/py20305:ro" py20305
```

The publication is the access boundary, not the in-container bind. The image
also has to be built with the `api` extra, which the default build omits.

## Serial devices

A device on an RS-485 line needs the line passed into the container:

```bash
docker run -d \
  -v "$PWD/config:/etc/py20305:ro" \
  --device /dev/ttyUSB0 \
  py20305
```

The container user must be able to read it. Either add the group that owns the
line with `--group-add`, or set the ownership on the host — granting the
container broad device privileges to avoid that trade is not worth it for a
serial port.

## What is not in the image

Configuration and certificates, deliberately. Baking a certificate into an
image makes it hard to rotate, spreads it to every registry the image reaches,
and ties one build to one deployment. `.dockerignore` excludes `*.pem`, `*.key`
and `client.yaml` from the build context so a stray key in the working
directory cannot be copied in by accident.
