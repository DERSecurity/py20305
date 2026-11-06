# Running the client under systemd

The unit in this directory runs the client from a configuration file as an
unprivileged service. It expects a virtualenv at `/opt/py20305/venv` and a
configuration at `/etc/py20305/client.yaml`; both paths appear in `ExecStart`,
so change them together if you prefer a different layout.

## Install

Create the service account. It owns nothing and cannot log in — the unit only
needs an identity to drop to:

```bash
sudo useradd --system --home-dir /opt/py20305 --shell /usr/sbin/nologin py20305
```

Install the client into its own virtualenv, so its dependencies never argue
with the distribution's Python packages:

```bash
sudo install -d -o py20305 -g py20305 /opt/py20305
sudo -u py20305 python3 -m venv /opt/py20305/venv
sudo -u py20305 /opt/py20305/venv/bin/pip install "py20305[cli,sunspec]"
```

Put the configuration and certificates where the unit expects them. Paths
inside the configuration resolve relative to the configuration file, so
`client_cert: certs/client.pem` means `/etc/py20305/certs/client.pem`:

```bash
sudo install -d -m 0755 /etc/py20305
sudo install -d -m 0750 -o root -g py20305 /etc/py20305/certs
sudo install -m 0640 -o root -g py20305 client.yaml /etc/py20305/client.yaml
```

The private key is the client's identity to the utility. Keep it readable by
the service account and nobody else:

```bash
sudo install -m 0640 -o root -g py20305 client.key /etc/py20305/certs/client.key
sudo install -m 0644 -o root -g py20305 client.pem /etc/py20305/certs/client.pem
sudo install -m 0644 -o root -g py20305 ca.pem     /etc/py20305/certs/ca.pem
```

If the device is on a serial port rather than Modbus TCP, the account also
needs the group that owns the port. `DeviceAllow` in the unit lifts
systemd's device policy but not the port's own permissions, which are
typically `root:dialout` mode 0660:

```bash
stat -c '%G' /dev/ttyUSB0          # confirm the group on your port
sudo usermod -aG dialout py20305
```

## Check before starting

```bash
sudo -u py20305 /opt/py20305/venv/bin/py20305 \
  --config /etc/py20305/client.yaml --check
```

This validates the document, resolves the certificate and prints the LFDI —
the identity the utility has to register. It connects to nothing. Running it as
the service account also proves the file permissions above are right, which is
the failure this catches that a root-run check would not.

## Enable

```bash
sudo cp py20305.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now py20305
journalctl -u py20305 -f
```

## Notes on the unit

`RestartPreventExitStatus=2` is why the client distinguishes its exit codes. A
wrong configuration exits 2 and the service stops, visibly; an unreachable
server exits 3 and systemd retries. Without that line a typo in the config
becomes a restart loop that looks like a network problem.

The sandboxing directives assume a client that talks to a device over Modbus
TCP. A device on a serial port needs its tty, which `PrivateDevices=true`
hides — the unit says how to grant it.

The client retries its own *connection* and leaves process supervision to
systemd. That is why the unit has a restart policy at all, and why the client
does not try to restart itself.
