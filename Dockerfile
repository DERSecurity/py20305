# Build the wheel in one stage and install it in another, so the runtime image
# carries neither the build backend nor the source tree -- what ships is the
# same artifact `pip install py20305` would fetch.
FROM python:3.12-slim AS build

WORKDIR /src
RUN pip install --no-cache-dir build

# Copy only what the build backend reads. The source tree changes far more
# often than the metadata, so ordering it this way keeps the dependency layer
# cached across ordinary edits.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN python -m build --wheel --outdir /dist


FROM python:3.12-slim AS runtime

# The extras a container deployment actually uses: `cli` for the configuration
# file the entrypoint reads, `sunspec` to reach a device, `mqtt` to forward.
# `api` is left out -- it serves on a port, and a deployment that wants it can
# build with a different extra set rather than have every image carry a web
# framework it does not serve.
ARG EXTRAS=cli,sunspec,mqtt

COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir "$(echo /tmp/*.whl)[${EXTRAS}]" \
    && rm -rf /tmp/*.whl

# Runs as a non-root user: this process makes outbound connections and reads a
# certificate, and needs no privilege beyond that. The ids are pinned rather
# than left to useradd, because a bind-mounted file is matched by number and a
# base-image change that shifted them would stop the client reading its own
# key. Override at build time, or run with `--user "$(id -u):$(id -g)"` to
# borrow the host account that already owns the certificate -- either beats
# making a private key world-readable.
ARG UID=10005
ARG GID=10005
RUN groupadd --system --gid "${GID}" py20305 \
    && useradd --system --uid "${UID}" --gid "${GID}" \
        --create-home --shell /usr/sbin/nologin py20305

# Where a deployment mounts its configuration and certificates. Declared so
# `docker run -v ./config:/etc/py20305` needs no path archaeology, and left
# read-only in the compose example -- the client never writes here.
ENV PY20305_CONFIG=/etc/py20305/client.yaml
RUN mkdir -p /etc/py20305 && chown py20305:py20305 /etc/py20305

USER py20305
WORKDIR /home/py20305

# Exec form, so the client receives SIGTERM directly from the runtime rather
# than through a shell that would not forward it. The runner shuts down in
# order on that signal, and a shell wrapper here would turn every stop into a
# ten-second kill instead.
ENTRYPOINT ["py20305"]
CMD ["--config", "/etc/py20305/client.yaml"]
