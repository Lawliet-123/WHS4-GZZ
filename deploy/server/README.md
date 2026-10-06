# Central server runtime deployment (role 2)

This directory covers only the central FastAPI runtime, persistent data, and
`systemd` lifecycle. Google Cloud resources, DNS, firewall, TLS certificates,
React builds, and Nginx routing belong to the infrastructure and Dashboard
deployment owners.

## 1. Runtime contract

- Ubuntu VM with Python 3.11 or newer.
- Repository checkout: `/opt/meccha-anticheat`.
- Virtual environment: `/opt/meccha-anticheat/.venv`.
- Service account: `meccha` with no interactive login.
- FastAPI listener: `127.0.0.1:8000`; it must not be exposed by the VM firewall.
- One Uvicorn worker. Startup performs Shared writer initialization, Scoring
  initialization, and recovery before accepting requests.
- Persistent state: `/var/lib/meccha-anticheat`.
- Secret environment file: `/etc/meccha-anticheat/server.env`.

The public Nginx routes are expected to forward `/api/detection`,
`/api/heartbeat`, and Dashboard query paths to this loopback listener.

## 2. First installation

The VM owner installs the base packages. The commands below are executed by an
administrator after the repository has been copied or cloned to
`/opt/meccha-anticheat`.

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv curl

sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin meccha || true
sudo python3 -m venv /opt/meccha-anticheat/.venv
sudo /opt/meccha-anticheat/.venv/bin/python -m pip install --upgrade pip
sudo /opt/meccha-anticheat/.venv/bin/python -m pip install \
  -r /opt/meccha-anticheat/server/requirements.txt

sudo chown -R root:root /opt/meccha-anticheat
sudo chmod -R go-w /opt/meccha-anticheat
```

Create the environment file without committing any real secret:

```bash
sudo install -d -m 0750 -o root -g meccha /etc/meccha-anticheat
sudo install -m 0640 -o root -g meccha \
  /opt/meccha-anticheat/deploy/server/server.env.example \
  /etc/meccha-anticheat/server.env
sudoedit /etc/meccha-anticheat/server.env
```

Generate four independent values. Do not paste their output into GitHub,
Discord, screenshots, or deployment logs.

```bash
openssl rand -hex 32
```

The service runs `validate_environment.py` before Uvicorn. Startup is rejected
when a required value is missing, an example placeholder remains, two secrets
are identical, or a persistent path points outside
`/var/lib/meccha-anticheat`.

Install and start the service:

```bash
sudo install -m 0644 \
  /opt/meccha-anticheat/deploy/server/meccha-anticheat.service \
  /etc/systemd/system/meccha-anticheat.service
sudo systemctl daemon-reload
sudo systemctl enable --now meccha-anticheat.service
```

`StateDirectory=meccha-anticheat` creates the persistent root with ownership
for the service user. The application creates its configured child folders and
SQLite files during startup.

## 3. Verification before Nginx connection

Do not continue to the public Nginx step until all commands pass.

```bash
sudo systemctl status meccha-anticheat.service --no-pager
sudo journalctl -u meccha-anticheat.service -n 100 --no-pager
curl --fail http://127.0.0.1:8000/health
bash /opt/meccha-anticheat/deploy/server/check_backend.sh
```

Expected results:

- `/health` returns `{"status":"ok"}`.
- Detection, Heartbeat, and Dashboard requests without credentials return 401.
- `ss -ltn` shows port 8000 bound to `127.0.0.1`, not `0.0.0.0`.
- Service logs contain writer/scoring initialization and recovery completion.

The existing application smoke test exercises valid authenticated Detection,
Heartbeat, Shared storage, Scoring, and Dashboard requests on an isolated local
server:

```bash
cd /opt/meccha-anticheat
sudo -u meccha .venv/bin/python -m server.dashboard_backend.browser_smoke \
  serve --port 8002 --duration 60
```

This fixture is synthetic and temporary. It prints a one-run test token, starts
only on loopback, verifies Detection/Heartbeat/Scoring/Dashboard integration,
and removes its temporary data when the bounded run finishes.

## 4. Updating the application

Prepare and test the new commit before replacing the running checkout. Keep the
persistent data and environment file outside the checkout. For the current
single-VM prototype, use a short maintenance window:

```bash
sudo systemctl stop meccha-anticheat.service

# Update /opt/meccha-anticheat to the reviewed main commit here.

sudo /opt/meccha-anticheat/.venv/bin/python -m pip install \
  -r /opt/meccha-anticheat/server/requirements.txt
sudo systemctl start meccha-anticheat.service
bash /opt/meccha-anticheat/deploy/server/check_backend.sh
```

If the health or protected-route check fails, restore the previously reviewed
commit and restart the service. Do not delete or reset
`/var/lib/meccha-anticheat` during rollback.

## 5. Backup and restart recovery

The data directory contains JSONL/ledger state and SQLite databases. Do not copy
active SQLite files without their WAL files. The simple prototype backup uses a
short maintenance window:

```bash
sudo systemctl stop meccha-anticheat.service
sudo tar -C /var/lib -czf \
  "/var/backups/meccha-anticheat-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" \
  meccha-anticheat
sudo systemctl start meccha-anticheat.service
```

After a VM restart or restore, verify `/health`, service logs, one existing
Dashboard query, and one new Detection/Heartbeat event. Recovery is not proven
by process status alone.

## 6. Handoff to the other deployment owners

Provide the following without revealing secret values:

- Infrastructure owner: confirm `127.0.0.1:8000` is healthy and port 8000 is
  not publicly reachable.
- Dashboard/Nginx owner: confirm the loopback upstream and protected route list.
- Launcher owners: privately provide only the Detection and Heartbeat tokens
  plus the final HTTPS origin.
- Dashboard testers: privately provide only the Dashboard token plus the final
  HTTPS origin.

Never give the Dashboard token to Launcher builds, or the Receiver tokens to
the React source/build. `GZZ_DASHBOARD_CURSOR_SECRET` remains server-only.
