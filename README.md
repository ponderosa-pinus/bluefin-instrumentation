# Bluefin Instrumentation

> **Status:** Prototype — intended for shipping to Bluefin users as a built-in monitoring feature.

Bluefin Instrumentation is a fully self-contained, rootless laptop health monitoring system built on Prometheus + Perses, managed by Podman Quadlets under systemd. It runs entirely as a normal user — no root, no systemd unit hacks, no cloud dependency.

---

## Table of Contents

1. [Design Goals](#design-goals)
2. [Architecture](#architecture)
3. [Hardware Reference](#hardware-reference)
4. [Prerequisites](#prerequisites)
5. [File Layout](#file-layout)
6. [Setup — Step by Step](#setup--step-by-step)
7. [Quadlet Units](#quadlet-units)
8. [Prometheus Configuration](#prometheus-configuration)
9. [Perses Datasource](#perses-datasource)
10. [Dashboard Suite](#dashboard-suite)
11. [ujust Integration](#ujust-integration)
12. [Playwright Test Suite](#playwright-test-suite)
13. [Data Persistence & Backup](#data-persistence--backup)
14. [GPU Monitoring — Status & Roadmap](#gpu-monitoring--status--roadmap)
15. [Lessons Learned](#lessons-learned)
16. [Troubleshooting](#troubleshooting)

---

## Design Goals

- **Rootless** — runs entirely as user `$USER` via Podman + systemd user units
- **Persistent** — named Podman volumes survive reboots and container recreation
- **Boot-safe** — `loginctl enable-linger` makes services start without login
- **Reproducible** — `bluefin-instrumentation.py` rebuilds all 9 dashboards from scratch in ~5 seconds
- **Testable** — 3-layer Playwright test suite verifies data flows end-to-end
- **Shippable** — minimal external dependencies; runs on any Bluefin/ublue machine

---

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │        systemd-monitoring network        │
                    │           (podman bridge, DNS)           │
                    │                                          │
  /proc  /sys  /    │  ┌───────────────┐                       │
  (host) ──────────►│  │ node_exporter │ :9100                 │
                    │  │  (read-only)  │                       │
                    │  └──────┬────────┘                       │
                    │         │ scrape every 15s               │
                    │  ┌──────▼────────┐   prometheus-data     │
                    │  │  prometheus   │ ◄─────────────────    │
                    │  │   :9090       │   (named volume)      │
                    │  └──────┬────────┘                       │
                    │         │ HTTP proxy                     │
                    │  ┌──────▼────────┐   perses-data         │
                    │  │    perses     │ ◄─────────────────    │
                    │  │   :8080       │   (named volume)      │
                    │  └───────────────┘                       │
                    └─────────────────────────────────────────┘
                                        │
                              browser at localhost:8080
```

### Key design decisions

**Container DNS over IP addresses**
All inter-container communication uses container names (`prometheus:9090`, `node_exporter:9100`) via Podman's built-in DNS on the named network. IPs change on recreation; names don't.

**HTTPProxy datasource, not directUrl**
The Perses datasource uses server-side `HTTPProxy` mode (`http://prometheus:9090`). The `directUrl` mode (`http://localhost:9090`) routes queries through the browser, which works but bypasses the Perses proxy — use HTTPProxy so Perses controls all data access.

**Datasource kind must be `PrometheusDatasource`, not `GlobalDatasource`**
Dashboard panel queries reference datasources by plugin kind. Using `"kind": "GlobalDatasource"` in the panel spec causes silent "No data" — the correct value is `"kind": "PrometheusDatasource"`.

**Named volumes, not anonymous volumes**
Perses must have a named volume (`perses-data.volume`) declared as a Quadlet file. Without it, every container restart creates a new anonymous volume and loses all dashboards, datasources, and projects.

**`/proc/self/mountinfo` instead of `/proc/1/mountinfo`**
Rootless containers cannot read PID 1's namespace files. The node_exporter filesystem collector needs mountinfo — solved by bind-mounting `/proc/self/mountinfo` into the container at `/host/proc/1/mountinfo`. Note: this requires the quadlet to mount the file, which doesn't work in all rootless configurations; the current workaround mounts `/sys` and lets node_exporter use its own proc.

---

## Hardware Reference

This was developed and tested on:

| Component | Detail |
|---|---|
| Machine | Framework Laptop 13 (Intel Core Ultra Series 1) |
| OS | Bluefin LTS `bluefin-dx:lts-hwe` (CentOS 10 base) |
| Kernel | 6.17.12-200.fc42.x86_64 |
| CPU | Intel Core Ultra 7 155H (22 logical cores, Meteor Lake) |
| GPU | Intel Arc Graphics (Meteor Lake-P, `8086:7d55`, `i915`/`xe` drivers) |
| RAM | 16 GB LPDDR5 |
| Storage | NVMe SSD |
| Battery | BAT1 — 4-cell, 77 cycles at time of writing |

---

## Prerequisites

All of these are present on any standard Bluefin install:

- `podman` (rootless)
- `systemd` user session
- `gum` (for `ujust` dialogs)
- `just` ≥ 1.46 (for `~/.justfile` tilde expansion)
- `python3` with `requests` (for dashboard builder)
- `node` + `npm` (for Playwright tests) — available via Homebrew

```bash
# Verify
podman --version
systemctl --user status
gum --version
just --version
python3 -c "import requests; print('ok')"
```

---

## File Layout

```
~/.config/containers/systemd/          # Podman Quadlet definitions
├── monitoring.network                 # Shared bridge network
├── prometheus-data.volume             # Prometheus TSDB volume
├── perses-data.volume                 # Perses config/dashboard volume
├── node-exporter.container
├── podman-exporter.container          # Prometheus-Podman exporter (container metrics)
├── prometheus.container
└── perses.container

~/.config/monitoring/
└── prometheus.yml                     # Prometheus scrape config

~/.local/share/containers/storage/volumes/
├── systemd-prometheus-data/_data/     # Prometheus TSDB (15-day retention)
└── systemd-perses-data/_data/         # Perses projects, dashboards, datasources

~/.justfile                            # Personal ujust recipes
~/.bashrc                              # ujust() shell function

~/src/bluefin-instrumentation/
├── playwright.config.ts
├── tests/dashboard.spec.ts            # 3-layer test suite (21 tests)
└── bluefin-instrumentation.py         # Dashboard builder (rebuilds all 9 dashboards)
```

---

## Setup — Step by Step

### 1. Create the config directory

```bash
mkdir -p ~/.config/containers/systemd
mkdir -p ~/.config/monitoring
```

### 2. Write Quadlet files

See [Quadlet Units](#quadlet-units) below for exact file contents.

### 3. Write Prometheus config

```bash
cat > ~/.config/monitoring/prometheus.yml << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets: ['node_exporter:9100']
EOF
```

### 4. Reload systemd and start services

```bash
systemctl --user daemon-reload
systemctl --user start monitoring-network.service
systemctl --user start node-exporter.service
systemctl --user start podman-exporter.service
systemctl --user start prometheus.service
systemctl --user start perses.service
```

### 5. Enable linger (boot without login)

```bash
loginctl enable-linger $USER
loginctl show-user $USER | grep Linger  # should say Linger=yes
```

### 6. Configure Perses datasource

```bash
curl -s -X POST http://localhost:8080/api/v1/globaldatasources \
  -H "Content-Type: application/json" \
  -d '{
    "kind": "GlobalDatasource",
    "metadata": { "name": "prometheus" },
    "spec": {
      "display": { "name": "Prometheus" },
      "default": true,
      "plugin": {
        "kind": "PrometheusDatasource",
        "spec": {
          "proxy": { "kind": "HTTPProxy", "spec": { "url": "http://prometheus:9090" } }
        }
      }
    }
  }'
```

### 7. Create the project and dashboards

```bash
# Create project
curl -s -X POST http://localhost:8080/api/v1/projects \
  -H "Content-Type: application/json" \
  -d '{"kind":"Project","metadata":{"name":"laptop"},"spec":{}}'

# Build all 8 dashboards
cd ~/src/bluefin-instrumentation
python3 bluefin-instrumentation.py
```

### 8. Install Playwright tests

```bash
cd ~/src/bluefin-instrumentation
PATH=/home/linuxbrew/.linuxbrew/bin:$PATH npm install
PATH=/home/linuxbrew/.linuxbrew/bin:$PATH npx playwright install chromium
npm test
```

### 9. Install ujust recipe

See [ujust Integration](#ujust-integration) below.

---

## Quadlet Units

### `~/.config/containers/systemd/monitoring.network`

```ini
[Unit]
Description=Monitoring stack network

[Network]
Label=app=monitoring
```

### `~/.config/containers/systemd/prometheus-data.volume`

```ini
[Unit]
Description=Prometheus TSDB volume

[Volume]
Label=app=monitoring
```

### `~/.config/containers/systemd/perses-data.volume`

```ini
[Volume]

[Install]
WantedBy=default.target
```

> ⚠️ **Critical:** Without a named volume here, Perses uses an anonymous volume and loses all dashboards on every restart.

### `~/.config/containers/systemd/node-exporter.container`

```ini
[Unit]
Description=Node Exporter — hardware and OS metrics collector
After=monitoring-network.service
Wants=monitoring-network.service

[Container]
Image=quay.io/prometheus/node-exporter:latest
ContainerName=node_exporter
Network=monitoring.network

Volume=/sys:/host/sys:ro
Volume=/:/rootfs:ro,rslave

Exec=--path.rootfs=/rootfs \
     --path.sysfs=/host/sys \
     --collector.filesystem.mount-points-exclude=^/(dev|proc|sys|run/credentials/.+|var/lib/docker/.+|var/lib/containers/.+)($$|/) \
     --collector.filesystem.fs-types-exclude=^(autofs|binfmt_misc|bpf|cgroup2?|configfs|debugfs|devpts|devtmpfs|fusectl|fuse.lxcfs|hugetlbfs|iso9660|mqueue|nsfs|overlay|proc|procfs|pstore|rpc_pipefs|securityfs|selinuxfs|squashfs|sysfs|tracefs)$$ \
     --no-collector.thermal_zone

[Service]
Restart=on-failure
TimeoutStartSec=120

[Install]
WantedBy=default.target
```

**Notes:**
- `/proc` is intentionally **not** mounted. Rootless containers cannot read `/proc/1/mountinfo` (host PID 1's namespace). node_exporter uses its own `/proc/self` instead.
- `--no-collector.thermal_zone` suppresses the collector that needs kernel debugfs (returns errors otherwise).
- `$$` in Exec= is how quadlet escapes `$` in shell patterns (systemd unit syntax).
- Failed collectors (not applicable on laptop): `bonding`, `fibrechannel`, `infiniband`, `ipvs`, `nfs`, `nfsd`, `tapestats`, `rapl`

### `~/.config/containers/systemd/podman-exporter.container`

```ini
[Unit]
Description=Podman Exporter — container metrics for Prometheus
After=monitoring-network.service
Wants=monitoring-network.service

[Container]
Image=quay.io/navidys/prometheus-podman-exporter:latest
ContainerName=podman_exporter
Network=monitoring.network

Volume=/run/user/1000/podman/podman.sock:/run/podman/podman.sock:ro
Environment=CONTAINER_HOST=unix:///run/podman/podman.sock

UserNS=keep-id
SecurityLabelDisable=true

Exec=--collector.enable-all \
     --collector.cache_duration=30

[Service]
Restart=always
RestartSec=5
TimeoutStartSec=60

[Install]
WantedBy=default.target
```

**Notes:**
- `Volume=/run/user/1000/podman/podman.sock` — mounts the user Podman socket (UID 1000). Adjust if your UID differs.
- `UserNS=keep-id` — runs as your login UID so socket ownership matches.
- `SecurityLabelDisable=true` — the socket is on tmpfs; `:z` SELinux relabeling would fail.
- `--collector.enable-all` — enables all Podman collectors (container, image, network, pod, volume, system).
- Exposes metrics at `:9882` on the `monitoring` network; Prometheus scrapes it as the `podman` job.

### `~/.config/containers/systemd/prometheus.container`

```ini
[Unit]
Description=Prometheus — time series database
After=monitoring-network.service node-exporter.service prometheus-data-volume.service
Wants=monitoring-network.service node-exporter.service prometheus-data-volume.service

[Container]
Image=quay.io/prometheus/prometheus:latest
ContainerName=prometheus
Network=monitoring.network

PublishPort=9090:9090

Volume=%h/.config/monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro,z
Volume=prometheus-data.volume:/prometheus:z

Exec=--config.file=/etc/prometheus/prometheus.yml \
     --storage.tsdb.path=/prometheus \
     --web.enable-lifecycle

[Service]
Restart=on-failure
TimeoutStartSec=120

[Install]
WantedBy=default.target
```

**Notes:**
- `%h` expands to `$HOME` in quadlet syntax.
- `:z` sets the SELinux label for volume mounts (required on Fedora/RHEL-based systems).
- `--web.enable-lifecycle` enables `POST /-/reload` for config reloads without restart.
- Default retention: 15 days, ~5–50 MB/day depending on metric count.

### `~/.config/containers/systemd/perses.container`

```ini
[Unit]
Description=Perses — metrics visualization dashboard
After=monitoring-network.service prometheus.service
Wants=monitoring-network.service prometheus.service

[Container]
Image=docker.io/persesdev/perses:latest
ContainerName=perses
Network=monitoring.network

PublishPort=8080:8080

Volume=perses-data.volume:/perses:z

[Service]
Restart=on-failure
TimeoutStartSec=120

[Install]
WantedBy=default.target
```

---

## Prometheus Configuration

`~/.config/monitoring/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets: ['node_exporter:9100']

  - job_name: 'podman'
    static_configs:
      - targets: ['podman_exporter:9882']
```

**Important:** All targets use container names (`node_exporter:9100`, `podman_exporter:9882`), not `localhost`. Both exporters and Prometheus are on the `systemd-monitoring` bridge network which provides DNS resolution by container name.

To reload after editing (without restarting Prometheus):
```bash
curl -s -X POST http://localhost:9090/-/reload
```

---

## Perses Datasource

The global datasource must be configured **after** Perses starts. It uses server-side HTTPProxy mode so Perses routes all queries internally — the browser never needs direct access to Prometheus.

```json
{
  "kind": "GlobalDatasource",
  "metadata": { "name": "prometheus" },
  "spec": {
    "display": { "name": "Prometheus" },
    "default": true,
    "plugin": {
      "kind": "PrometheusDatasource",
      "spec": {
        "proxy": {
          "kind": "HTTPProxy",
          "spec": { "url": "http://prometheus:9090" }
        }
      }
    }
  }
}
```

**Verify the proxy works:**
```bash
curl -s "http://localhost:8080/proxy/globaldatasources/prometheus/api/v1/query?query=node_load1"
# Should return: {"status":"success","data":{"resultType":"vector","result":[...]}}
```

**Common pitfall:** If you see `{"message":"no proxy kind found"}` in Perses logs, the datasource spec has lost its proxy configuration (e.g., was accidentally set to `directUrl` mode). Re-apply the correct spec above.

---

## Dashboard Suite

All dashboards live in the `laptop` project. Rebuild at any time:

```bash
cd ~/src/bluefin-instrumentation
python3 bluefin-instrumentation.py
```

| Dashboard | Name | Panels | Sections | URL |
|---|---|---|---|---|
| `summary` | Bluefin Instrumentation | 26 | 5 | `/projects/laptop/dashboards/summary` |
| `cpu` | Bluefin Instrumentation — CPU | 12 | 5 | `/projects/laptop/dashboards/cpu` |
| `memory` | Bluefin Instrumentation — Memory | 13 | 5 | `/projects/laptop/dashboards/memory` |
| `disk` | Bluefin Instrumentation — Disk | 14 | 6 | `/projects/laptop/dashboards/disk` |
| `network` | Bluefin Instrumentation — Network | 12 | 5 | `/projects/laptop/dashboards/network` |
| `battery-power` | Bluefin Instrumentation — Battery & Power | 13 | 5 | `/projects/laptop/dashboards/battery-power` |
| `thermals` | Bluefin Instrumentation — Thermals | 11 | 6 | `/projects/laptop/dashboards/thermals` |
| `health` | Bluefin Instrumentation — Health | 14 | 6 | `/projects/laptop/dashboards/health` |
| `containers` | Bluefin Instrumentation — Containers | 17 | 7 | `/projects/laptop/dashboards/containers` |

### Summary dashboard panels

- **Gauge row:** CPU Used %, Memory Used %, Disk Busy %, Battery % — colour-coded green→amber→red at 70/90%
- **Key indicators:** CPU temp, fan RPM, load average, uptime, OOM kills, battery health, entropy, NTP sync
- **Trend row:** CPU %, Memory %, Network RX/TX, Disk Read/Write — 1-hour sparklines
- **Health at a glance:** PSI (all resources), temperature trends, swap activity
- **Containers:** Running/stopped counts, combined memory + CPU stats, per-container CPU/memory/network time series

### Containers dashboard panels

Podman container metrics (via `prometheus-podman-exporter`): CPU usage per container, memory RSS, network RX/TX bytes and packets, network errors, block I/O, process count, uptime, rootfs and read-write layer sizes, container state table.

### What each sub-dashboard covers

**CPU:** utilisation by mode (user/system/iowait/irq/softirq/steal), per-core frequency, load average, thermal throttle events, context switches, interrupts, process queue, CPU run-queue wait time (schedstat), guest VM time, PSI

**Memory:** total/used/available/cached, slab reclaimable/unreclaimable, dirty pages & writeback, memory commitment vs limit, HugePages, swap I/O, minor + major page faults, page I/O, hardware corrupted bytes, OOM kills, PSI

**Disk:** throughput per device, IOPS, disk utilisation %, saturation (weighted IO time), inflight operations, read/write latency, filesystem space %, filesystem free bytes, inode usage %, TRIM/discard activity, XFS read/write calls, XFS inode cache, PSI

**Network:** traffic per interface, packets/sec, errors & drops, softnet backlog (kernel receive queue), TCP established + active/passive opens, TCP retransmits + errors + resets, TCP extended health (listen drops, SYN retrans, timeouts), UDP activity, conntrack fill %, socket usage by protocol, carrier changes

**Battery & Power:** charge %, voltage×current power draw, charger input per USB-C port, voltage/current over time, battery health %, cycle count, full charge capacity, AC online status, hwmon power rails/current/voltages

**Thermals:** CPU package temperature with throttle/critical thresholds, per-core temperatures (min/avg/max spread), temperature headroom to throttle/critical, NVMe composite temp, EC temperatures (CPU/DDR/battery/PECI via CrOS EC), ACPI thermal zones, fan speed + target, temperature alarm flags

**Health:** PSI for all 6 resources (CPU waiting, IO stalled, IO waiting, memory stalled, memory waiting, IRQ stalled), EDAC correctable + uncorrectable memory errors, OOM kills + process queue, file descriptor usage %, entropy pool, NTP clock offset + sync status + frequency correction, SELinux enforcement mode, filesystem device errors, conntrack fill %

### Valid Perses unit strings

Not all units work. Verified valid values for `yAxis.format.unit` and `StatChart.format.unit`:

| Category | Valid values |
|---|---|
| Time | `nanoseconds` `microseconds` `milliseconds` `seconds` `minutes` `hours` `days` `weeks` `months` `years` |
| Percentage | `percent` `percent-decimal` |
| Decimal | `decimal` |
| Bytes | `bytes` `decbytes` `bits` `decbits` |
| Throughput | `bytes/sec` `decbytes/sec` `bits/sec` `decbits/sec` `counts/sec` `events/sec` `ops/sec` `packets/sec` `reads/sec` `writes/sec` `messages/sec` `records/sec` `rows/sec` `requests/sec` |
| Currency | `usd` `eur` `gbp` `jpy` etc. |

❌ **Not valid:** `hertz` `watts` `volts` `amps` — use `decimal` for these and put the unit in the panel name.

### GaugeChart threshold format

The first threshold step value must be `0`, not `null`:
```json
"thresholds": {
  "steps": [
    {"color": "#3d9970", "value": 0},
    {"color": "#ff851b", "value": 70},
    {"color": "#ff4136", "value": 90}
  ]
}
```

---

## ujust Integration

`ujust` is hardcoded to `just --justfile /usr/share/ublue-os/just/00-entry.just` and `/usr/share` is read-only on ostree. The workaround is a shell function in `~/.bashrc` that intercepts known personal recipes before falling through to the real `ujust`.

### `~/.bashrc` addition

```bash
# Personal ujust extension — merges ~/.justfile recipes into ujust
ujust() {
    if [[ -f "${HOME}/.justfile" ]] && \
       just --justfile "${HOME}/.justfile" --summary 2>/dev/null | tr ' ' '\n' | grep -qx "${1:-__no_match__}"; then
        just --justfile "${HOME}/.justfile" "$@"
    else
        /usr/bin/ujust "$@"
    fi
}
```

### `~/.justfile` — `toggle-instrumentation` recipe

```just
# Toggle laptop performance instrumentation (Prometheus + node_exporter + podman-exporter + Perses)
[group('System')]
toggle-instrumentation:
    #!/usr/bin/env bash
    set -euo pipefail

    SERVICES=(node-exporter.service podman-exporter.service prometheus.service perses.service)
    LINGER=$(loginctl show-user "$USER" 2>/dev/null | grep "^Linger=" | cut -d= -f2)

    ALL_RUNNING=true
    for svc in "${SERVICES[@]}"; do
        if ! systemctl --user is-active --quiet "$svc" 2>/dev/null; then
            ALL_RUNNING=false
            break
        fi
    done

    if $ALL_RUNNING; then
        gum confirm \
            --prompt.foreground="212" \
            "Stop instrumentation? (Prometheus, node_exporter, podman-exporter, Perses will be shut down)" \
            || exit 0
        echo "Stopping instrumentation services..."
        for svc in "${SERVICES[@]}"; do
            systemctl --user stop "$svc" && echo "  stopped $svc" || true
        done
        if [ "$LINGER" = "yes" ]; then
            if gum confirm "Also disable linger (services won't auto-start at boot)?"; then
                loginctl disable-linger "$USER"
                echo "  linger disabled"
            fi
        fi
        echo ""
        gum style --foreground="212" "Instrumentation stopped."
    else
        gum confirm \
            --prompt.foreground="85" \
            "Start instrumentation? (Prometheus, node_exporter, podman-exporter, Perses)" \
            || exit 0
        echo "Starting instrumentation services..."
        for svc in "${SERVICES[@]}"; do
            systemctl --user start "$svc" && echo "  started $svc" || true
        done
        if [ "$LINGER" != "yes" ]; then
            if gum confirm "Enable linger so services survive reboot without login?"; then
                loginctl enable-linger "$USER"
                echo "  linger enabled — services will start at boot"
            fi
        fi
        sleep 2
        ALL_OK=true
        for svc in "${SERVICES[@]}"; do
            if systemctl --user is-active --quiet "$svc"; then
                echo "  ✅ $svc"
            else
                echo "  ❌ $svc (check: systemctl --user status $svc)"
                ALL_OK=false
            fi
        done
        if $ALL_OK; then
            gum style --foreground="85" "Instrumentation running."
            echo "  Perses dashboard: http://localhost:8080/projects/laptop/dashboards/summary"
            echo "  Prometheus:       http://localhost:9090"
        else
            gum style --foreground="196" "Some services failed to start."
        fi
    fi
```

---

## Playwright Test Suite

Located at `~/src/bluefin-instrumentation/tests/dashboard.spec.ts`. Three layers verify the full data path:

### Layer 1 — Prometheus data availability (9 tests)
- Prometheus health check
- node_exporter target is UP
- CPU, memory, load, disk, filesystem, network, uptime metrics exist

### Layer 2 — Perses server-side proxy (8 tests)
- Perses health check
- Datasource configured correctly
- CPU and memory data through proxy
- Range query returns multiple data points
- Dashboard exists with panels
- All 9 dashboards exist
- Podman exporter target is UP

### Layer 3 — Browser renders charts (4 tests)
- Dashboard page loads without HTTP errors
- Browser makes Prometheus proxy requests (not direct calls)
- SVG/canvas chart elements are rendered
- Zero "No data" messages visible

**Run tests:**
```bash
cd ~/src/bluefin-instrumentation
npm test
```

> **Note:** `tests/debug-network.spec.ts` was a development debug utility targeting the old `laptop-performance` monolith dashboard (now deleted). It has been removed.

**Key gotcha found during development:** Layer 3's console error test was what finally caught the root bug — dashboard panels used `"kind": "GlobalDatasource"` but the Perses plugin SDK requires `"kind": "PrometheusDatasource"`. This caused silent "No data" with no server-side error. The browser console showed: `Error: No datasource found for kind 'GlobalDatasource' and name 'prometheus'`.

---

## Data Persistence & Backup

### Where data lives

```bash
# Prometheus time-series data (15-day rolling window)
~/.local/share/containers/storage/volumes/systemd-prometheus-data/_data/

# Perses dashboards, datasources, projects (filesystem-backed)
~/.local/share/containers/storage/volumes/systemd-perses-data/_data/
```

### Prometheus TSDB layout

```
_data/
├── <ULID>/          # Compacted 2-hour blocks
│   ├── chunks/      # Binary compressed samples
│   ├── index        # Series index
│   ├── meta.json    # Block metadata (time range, sample count)
│   └── tombstones   # Deletion markers
├── chunks_head/     # Current (uncompacted) chunk data
└── wal/             # Write-ahead log (most recent ~2 hours)
```

The TSDB is binary format — not directly human-readable. Query via the HTTP API:

```bash
# Instant query
curl 'http://localhost:9090/api/v1/query?query=node_load1'

# Range query (last hour, 1-minute steps)
START=$(date -d "1 hour ago" +%s)
END=$(date +%s)
curl "http://localhost:9090/api/v1/query_range?query=node_load1&start=$START&end=$END&step=60"

# All metric names
curl 'http://localhost:9090/api/v1/label/__name__/values'
```

### Restoring Perses after data loss

If Perses loses its volume, run:
```bash
# Re-configure datasource
curl -s -X POST http://localhost:8080/api/v1/globaldatasources \
  -H "Content-Type: application/json" \
  -d '{"kind":"GlobalDatasource","metadata":{"name":"prometheus"},"spec":{"display":{"name":"Prometheus"},"default":true,"plugin":{"kind":"PrometheusDatasource","spec":{"proxy":{"kind":"HTTPProxy","spec":{"url":"http://prometheus:9090"}}}}}}'

# Re-create project
curl -s -X POST http://localhost:8080/api/v1/projects \
  -H "Content-Type: application/json" \
  -d '{"kind":"Project","metadata":{"name":"laptop"},"spec":{}}'

# Rebuild all dashboards
cd ~/src/bluefin-instrumentation && python3 bluefin-instrumentation.py
```

---

## GPU Monitoring — Status & Roadmap

### Hardware
- **GPU:** Intel Arc Graphics (Meteor Lake-P, PCI `8086:7d55`)
- **Active driver:** `i915` (with `xe` module also loaded)
- **i915 PMU events available:** `actual-frequency-gt0/gt1`, `requested-frequency-gt0/gt1`, `rc6-residency-gt0/gt1`, `rcs0-busy`, `bcs0-busy`, `ccs0-busy`, `vcs0/vcs1-busy`, `vecs0-busy`, `interrupts`

### Current blocker

All viable exporters wrap `intel_gpu_top` from `igt-gpu-tools`. The versions available in Ubuntu 24.04 (1.28) and Alpine edge (1.27.1) both fail on Meteor Lake MTL iGPU with:

```
intel_gpu_top: ../tools/intel_gpu_top.c:557: get_num_gts: Assertion `!errno || errno == ENOENT' failed.
```

MTL iGPU support requires `igt-gpu-tools` ≥ 1.29 (upstream git).

Additionally, `kernel.perf_event_paranoid=2` (Bluefin default) blocks unprivileged perf access. `intel_gpu_top` needs either `CAP_PERFMON` (sufficient with paranoid ≤ 1) or `perf_event_paranoid` lowered to ≤ 0 for full access.

### Options evaluated

| Option | Notes |
|---|---|
| `mike1808/igpu-exporter` | Best metrics (per-process, IMC bandwidth, power). Blocked by igt-gpu-tools 1.28 (too old for MTL). Needs `--pid=host`. |
| `clambin/intel-gpu-exporter` | Cleaner code, Alpine image. Same igt-gpu-tools 1.27.1 issue. |
| `ehlerst/xe-exporter` | Reads xe debugfs directly (no intel_gpu_top). Requires root + debugfs access. Tested on Arc B60 only, not MTL iGPU. |
| Intel `gputop` | Archived 2019. |
| Intel `metrics-discovery` | Archived. Was the MD API backend. |
| DIY textfile collector | Shell script + `perf stat` → node_exporter textfile. No container needed but brittle. |

### Path forward

Build a custom container image with `igt-gpu-tools` from git source on Fedora 42 base (which has newer dependencies than Ubuntu 24.04). Combined with setting `kernel.perf_event_paranoid=1` via a `sysctl.d` drop-in.

```bash
# Test paranoid level needed
cat /proc/sys/kernel/perf_event_paranoid  # currently 2

# Persistent fix (requires ostree overlay or /etc/sysctl.d/)
echo 'kernel.perf_event_paranoid = 1' | sudo tee /etc/sysctl.d/99-perf.conf
```

---

## Lessons Learned

### 1. Anonymous volumes are silent data loss
Podman Quadlets without an explicit `Volume=name.volume:/path` will create a new anonymous volume (random hash name) on every restart. All Perses configuration is lost. Always define a `.volume` quadlet file and reference it explicitly.

### 2. Container DNS, not localhost
`localhost` inside a container refers to the container itself, not the host. Use container names (`prometheus:9090`, `node_exporter:9100`) which resolve via Podman's internal DNS on named networks.

### 3. Perses datasource kind is the plugin kind, not the resource kind
`"kind": "GlobalDatasource"` in a dashboard panel datasource reference means "look in global datasources". The name field (`"name": "prometheus"`) identifies which one. But confusingly, the Perses plugin SDK's `getGlobalDatasource()` checks `selector.kind !== datasource.spec.plugin.kind` — so `selector.kind` must equal `"PrometheusDatasource"`, not `"GlobalDatasource"`. Using `"GlobalDatasource"` produces silent "No data" with no server error. Only the browser console reveals the bug.

### 4. Test the browser, not just the API
Layers 1 and 2 (Prometheus API + Perses proxy) were both working correctly throughout. The actual failure was only visible in the browser/DOM layer. The Playwright Layer 3 tests caught the datasource kind bug that API testing missed.

### 5. Rootless containers can't read `/proc/1/mountinfo`
The node_exporter filesystem collector reads `/proc/1/mountinfo` (host init's mount namespace). Rootless containers don't have access to other PID namespaces. Bind-mounting `/proc/self/mountinfo` → `/host/proc/1/mountinfo` fails (`EPERM` on the mount). Solution: omit the `/proc` volume mount and let node_exporter use its own `/proc/self/mountinfo` (the container's namespace, not the host's). This means filesystem mount discovery may be incomplete in some edge cases but works correctly in practice.

### 6. The `$$` escape in Quadlet `Exec=`
In systemd unit files, `$` must be escaped as `$$`. In Quadlet `[Container]` `Exec=` lines, the same rule applies. The regex `^/(dev|proc|...)($|/)` becomes `^/(dev|proc|...)($$|/)` in the quadlet file.

### 7. Perses GaugeChart threshold first value must be `0`, not `null`
The Perses CUE schema validates that threshold step values are numbers. Passing `null` for the first step (the baseline color) fails validation with a cryptic CUE disjunction error. Use `0`.

### 8. Perses HTTPProxy path is `/proxy/globaldatasources/{name}/...`
The Perses browser UI builds proxy URLs as `${apiPrefix}/proxy/globaldatasources/${name}/api/v1/...`. This is the correct server-side route. The 404 errors seen earlier with `/api/v1/proxy/...` were Perses trying to serve a static file (`.gz`) for that path and falling through to a 404 — not an API routing issue.

---

## Troubleshooting

### Services won't start

```bash
systemctl --user status node-exporter.service
systemctl --user status prometheus.service
systemctl --user status perses.service
podman logs node_exporter
podman logs prometheus
podman logs perses
```

### Prometheus target is DOWN

```bash
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool
# Check 'health' field — should be 'up'
# If 'down': check node_exporter is running and on the monitoring network
podman inspect node_exporter | python3 -c "import json,sys; d=json.load(sys.stdin)[0]; print(d['NetworkSettings']['Networks'].keys())"
```

### Perses shows "No data"

```bash
# 1. Verify proxy works
curl -s "http://localhost:8080/proxy/globaldatasources/prometheus/api/v1/query?query=up"

# 2. Check datasource config
curl -s http://localhost:8080/api/v1/globaldatasources/prometheus | python3 -m json.tool

# 3. Check Perses logs for errors
podman logs perses 2>&1 | grep -v "Unable to open the file"

# 4. Rebuild dashboards (fixes datasource kind issues)
cd ~/src/bluefin-instrumentation && python3 bluefin-instrumentation.py
```

### Perses data lost after restart

```bash
# Confirm the volume is named (not anonymous)
podman volume ls | grep perses
# Should show: systemd-perses-data
# If missing: the perses-data.volume quadlet wasn't applied

# Rebuild everything
systemctl --user daemon-reload
systemctl --user restart perses.service
# Then re-run setup from step 6
```

### Filesystem metrics missing

```bash
curl -s http://localhost:9090/api/v1/query?query=node_scrape_collector_success \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print([r for r in d['data']['result'] if r['metric']['collector']=='filesystem'])"
# value '0' means the collector failed
# Check node_exporter logs for the specific error
podman logs node_exporter 2>&1 | grep filesystem
```

### Collector failures that are expected on a laptop

These collectors fail and can be safely ignored (hardware not present):
- `bonding` — no bonded network interfaces
- `fibrechannel` — no FC HBAs
- `infiniband` — no IB hardware
- `ipvs` — not using IPVS load balancer
- `nfs` / `nfsd` — not an NFS server
- `tapestats` — no tape drives
- `rapl` — Intel RAPL blocked at `perf_event_paranoid=2` in a rootless container

### Run full test suite

```bash
cd ~/src/bluefin-instrumentation
npm test
# All 21 tests should pass
```
