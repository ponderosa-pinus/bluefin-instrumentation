#!/usr/bin/env python3
"""
Bluefin Instrumentation — dashboard builder
Creates a full suite of Perses dashboards for laptop health monitoring.
"""
import json, requests, sys

BASE = "http://localhost:8080"
PROJECT = "laptop"
DS = {"kind": "PrometheusDatasource", "name": "prometheus"}

# ── Primitives ────────────────────────────────────────────────────────────────

def prom(query, label=""):
    return {"kind": "TimeSeriesQuery", "spec": {"plugin": {"kind": "PrometheusTimeSeriesQuery", "spec": {
        "datasource": DS, "query": query,
        **({"seriesNameFormat": label} if label else {})
    }}}}

def ts(name, desc, queries, unit, min_=None, max_=None):
    yAxis = {"format": {"unit": unit, "decimalPlaces": 1 if unit in ("percent","bytes/sec","counts/sec") else 2}}
    if min_ is not None: yAxis["min"] = min_
    if max_ is not None: yAxis["max"] = max_
    return {"kind": "Panel", "spec": {
        "display": {"name": name, "description": desc},
        "plugin": {"kind": "TimeSeriesChart", "spec": {
            "legend": {"position": "bottom", "mode": "list"}, "yAxis": yAxis
        }},
        "queries": queries
    }}

def stat(name, desc, query, label, unit, decimals=1):
    return {"kind": "Panel", "spec": {
        "display": {"name": name, "description": desc},
        "plugin": {"kind": "StatChart", "spec": {
            "format": {"unit": unit, "decimalPlaces": decimals}, "sparkline": {}
        }},
        "queries": [prom(query, label)]
    }}

def gauge(name, desc, query, label, unit, min_=0, max_=100):
    return {"kind": "Panel", "spec": {
        "display": {"name": name, "description": desc},
        "plugin": {"kind": "GaugeChart", "spec": {
            "calculation": "last",
            "format": {"unit": unit, "decimalPlaces": 1},
            "thresholds": {
                "steps": [
                    {"color": "#3d9970", "value": 0},
                    {"color": "#ff851b", "value": 70},
                    {"color": "#ff4136", "value": 90},
                ]
            }
        }},
        "queries": [prom(query, label)]
    }}

def grid(title, items, open_=True):
    return {"kind": "Grid", "spec": {
        "display": {"title": title, "collapse": {"open": open_}},
        "items": items
    }}

def item(x, y, w, h, ref):
    return {"x": x, "y": y, "width": w, "height": h, "content": {"$ref": f"#/spec/panels/{ref}"}}

def dashboard(name, display_name, desc, panels, layouts):
    return {
        "kind": "Dashboard",
        "metadata": {"name": name, "project": PROJECT},
        "spec": {
            "display": {"name": display_name, "description": desc},
            "duration": "1h",
            "refreshInterval": "30s",
            "panels": panels,
            "layouts": layouts,
        }
    }

# ── API helpers ───────────────────────────────────────────────────────────────

def create_or_update(dash):
    name = dash["metadata"]["name"]
    # Try GET first
    r = requests.get(f"{BASE}/api/v1/projects/{PROJECT}/dashboards/{name}")
    if r.status_code == 200:
        existing = r.json()
        dash["metadata"]["version"] = existing["metadata"]["version"]
        r2 = requests.put(f"{BASE}/api/v1/projects/{PROJECT}/dashboards/{name}", json=dash)
        verb = "updated"
    else:
        r2 = requests.post(f"{BASE}/api/v1/projects/{PROJECT}/dashboards", json=dash)
        verb = "created"

    if r2.status_code in (200, 201):
        d = r2.json()
        print(f"  ✅ {verb} '{d['spec']['display']['name']}' — {len(dash['spec']['panels'])} panels")
        return True
    else:
        print(f"  ❌ failed: {r2.status_code} {r2.text[:200]}")
        return False

def delete_dashboard(name):
    r = requests.delete(f"{BASE}/api/v1/projects/{PROJECT}/dashboards/{name}")
    if r.status_code in (200, 204):
        print(f"  🗑  deleted '{name}'")
    else:
        print(f"  (not found: {name})")

# ══════════════════════════════════════════════════════════════════════════════
# 1. SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

def build_summary():
    panels = {
        # Row 1 — gauges
        "s_cpu_gauge": gauge("CPU Used", "Overall CPU utilisation",
            '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)',
            "CPU %", "percent"),
        "s_mem_gauge": gauge("Memory Used", "RAM utilisation",
            "(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes * 100",
            "RAM %", "percent"),
        "s_disk_gauge": gauge("Disk Busy", "Max disk utilisation across all devices",
            'max(rate(node_disk_io_time_seconds_total{device!~"loop.+"}[2m]))*100',
            "Disk %", "percent"),
        "s_bat_gauge": gauge("Battery", "BAT1 state of charge",
            'node_power_supply_capacity{power_supply="BAT1"}',
            "BAT %", "percent"),

        # Row 2 — key stats
        "s_cpu_temp": stat("CPU Temp", "Package temperature",
            'node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor="temp1"}',
            "°C", "decimal", 1),
        "s_fan": stat("Fan", "Fan speed",
            'node_hwmon_fan_rpm{chip="platform_pnp0c0b:00"}',
            "RPM", "decimal", 0),
        "s_load": stat("Load (1m)", "1-minute load average",
            "node_load1", "load", "decimal", 2),
        "s_uptime": stat("Uptime", "Time since last boot",
            "time() - node_boot_time_seconds", "uptime", "seconds", 0),
        "s_oom": stat("OOM Kills", "Out-of-memory kills since boot",
            "node_vmstat_oom_kill", "kills", "decimal", 0),
        "s_bat_health": stat("Battery Health", "Charge capacity vs design",
            'node_power_supply_charge_full{power_supply="BAT1"} / node_power_supply_charge_full_design{power_supply="BAT1"} * 100',
            "health %", "percent", 1),
        "s_entropy": stat("Entropy", "Available entropy bits",
            "node_entropy_available_bits", "bits", "decimal", 0),
        "s_ntp": stat("NTP Sync", "1 = synced",
            "node_timex_sync_status", "synced", "decimal", 0),

        # Row 3 — sparklines
        "s_cpu_spark": ts("CPU Usage %", "CPU over time", [
            prom('100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)', "CPU"),
        ], "percent", 0, 100),
        "s_mem_spark": ts("Memory Used %", "RAM over time", [
            prom("(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes * 100", "RAM"),
        ], "percent", 0, 100),
        "s_net_spark": ts("Network", "Total RX/TX", [
            prom('sum(rate(node_network_receive_bytes_total{device!~"lo|veth.+|docker.+|br-.+"}[2m]))', "RX"),
            prom('sum(rate(node_network_transmit_bytes_total{device!~"lo|veth.+|docker.+|br-.+"}[2m]))', "TX"),
        ], "bytes/sec"),
        "s_disk_spark": ts("Disk I/O", "Total read/write", [
            prom('sum(rate(node_disk_read_bytes_total{device!~"loop.+"}[2m]))', "Read"),
            prom('sum(rate(node_disk_written_bytes_total{device!~"loop.+"}[2m]))', "Write"),
        ], "bytes/sec"),

        # Row 4 — PSI summary
        "s_psi": ts("Pressure (PSI)", "Resource stall fractions", [
            prom("rate(node_pressure_cpu_waiting_seconds_total[5m])*100", "CPU"),
            prom("rate(node_pressure_io_stalled_seconds_total[5m])*100", "IO"),
            prom("rate(node_pressure_memory_stalled_seconds_total[5m])*100", "Memory"),
            prom("rate(node_pressure_irq_stalled_seconds_total[5m])*100", "IRQ"),
        ], "percent", 0),
        "s_temp_spark": ts("Temperatures", "CPU package + NVMe", [
            prom('node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor="temp1"}', "CPU Package"),
            prom('max(node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor!="temp1"})', "CPU Max Core"),
            prom('node_hwmon_temp_celsius{chip="nvme_nvme0", sensor="temp1"}', "NVMe"),
        ], "decimal"),
        "s_swap_spark": ts("Swap Activity", "Pages swapped in/out", [
            prom("rate(node_vmstat_pswpin[2m])", "Swap In"),
            prom("rate(node_vmstat_pswpout[2m])", "Swap Out"),
        ], "counts/sec"),
    }

    layouts = [
        grid("Bluefin Instrumentation — System Overview", [
            item(0,0,6,8,"s_cpu_gauge"), item(6,0,6,8,"s_mem_gauge"),
            item(12,0,6,8,"s_disk_gauge"), item(18,0,6,8,"s_bat_gauge"),
        ]),
        grid("Key Indicators", [
            item(0,0,3,4,"s_cpu_temp"), item(3,0,3,4,"s_fan"),
            item(6,0,3,4,"s_load"),   item(9,0,3,4,"s_uptime"),
            item(12,0,3,4,"s_oom"),   item(15,0,3,4,"s_bat_health"),
            item(18,0,3,4,"s_entropy"), item(21,0,3,4,"s_ntp"),
        ]),
        grid("Trends", [
            item(0,0,12,7,"s_cpu_spark"), item(12,0,12,7,"s_mem_spark"),
            item(0,7,12,7,"s_net_spark"), item(12,7,12,7,"s_disk_spark"),
        ]),
        grid("Health at a Glance", [
            item(0,0,10,7,"s_psi"), item(10,0,8,7,"s_temp_spark"),
            item(18,0,6,7,"s_swap_spark"),
        ]),
    ]

    return dashboard("summary", "Bluefin Instrumentation",
        "System overview — CPU, Memory, Disk, Network, Battery, Thermals, Health",
        panels, layouts)

# ══════════════════════════════════════════════════════════════════════════════
# 2. CPU
# ══════════════════════════════════════════════════════════════════════════════

def build_cpu():
    panels = {
        "cpu_used": ts("CPU Utilisation %", "Total CPU usage — user, system, iowait", [
            prom('100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)', "Total Used"),
            prom('avg(rate(node_cpu_seconds_total{mode="user"}[2m]))*100', "User"),
            prom('avg(rate(node_cpu_seconds_total{mode="system"}[2m]))*100', "System"),
            prom('avg(rate(node_cpu_seconds_total{mode="iowait"}[2m]))*100', "IOWait"),
            prom('avg(rate(node_cpu_seconds_total{mode="irq"}[2m]))*100', "IRQ"),
            prom('avg(rate(node_cpu_seconds_total{mode="softirq"}[2m]))*100', "SoftIRQ"),
            prom('avg(rate(node_cpu_seconds_total{mode="steal"}[2m]))*100', "Steal"),
        ], "percent", 0, 100),
        "cpu_mode_stacked": ts("CPU Mode Breakdown", "All modes except idle", [
            prom('avg by(mode) (rate(node_cpu_seconds_total{mode!="idle"}[2m]))*100', "{{mode}}"),
        ], "percent", 0),
        "cpu_load": ts("Load Average", "1m / 5m / 15m vs 22 logical cores", [
            prom("node_load1", "1m"),
            prom("node_load5", "5m"),
            prom("node_load15", "15m"),
        ], "decimal"),
        "cpu_freq": ts("Scaling Frequency", "Per-CPU current frequency (Hz)", [
            prom("node_cpu_scaling_frequency_hertz", "cpu{{cpu}}"),
        ], "decimal"),
        "cpu_freq_avg": ts("Average Frequency", "Average across all cores", [
            prom("avg(node_cpu_scaling_frequency_hertz)", "Avg Freq (Hz)"),
            prom("avg(node_cpu_scaling_frequency_max_hertz)", "Max Allowed"),
            prom("avg(node_cpu_scaling_frequency_min_hertz)", "Min Allowed"),
        ], "decimal"),
        "cpu_throttle": ts("Thermal Throttle Events/sec", "Non-zero = hitting thermal limits", [
            prom("sum(rate(node_cpu_core_throttles_total[2m]))", "Core Throttles/s"),
            prom("sum(rate(node_cpu_package_throttles_total[2m]))", "Package Throttles/s"),
        ], "counts/sec"),
        "cpu_ctx": ts("Context Switches & Forks/sec", "Kernel scheduler activity", [
            prom("rate(node_context_switches_total[2m])", "Context Switches/s"),
            prom("rate(node_forks_total[2m])", "Forks/s"),
        ], "counts/sec"),
        "cpu_intr": ts("Interrupts/sec", "Hardware + software interrupts", [
            prom("rate(node_intr_total[2m])", "HW Interrupts/s"),
        ], "counts/sec"),
        "cpu_procs": ts("Processes — Running & Blocked", "Blocked > 0 sustained = IO/lock contention", [
            prom("node_procs_running", "Running"),
            prom("node_procs_blocked", "Blocked"),
        ], "decimal", 0),
        "cpu_schedstat": ts("CPU Run Queue Wait Time", "Time tasks wait in run queue — better saturation signal than load avg", [
            prom("rate(node_schedstat_waiting_seconds_total[2m])", "Wait Time Rate"),
        ], "decimal"),
        "cpu_guest": ts("Guest CPU Time", "CPU time spent in VMs/containers", [
            prom('avg(rate(node_cpu_guest_seconds_total{mode="user"}[2m]))*100', "Guest User %"),
            prom('avg(rate(node_cpu_guest_seconds_total{mode="nice"}[2m]))*100', "Guest Nice %"),
        ], "percent", 0),
        "psi_cpu": ts("CPU Pressure (PSI)", "Fraction of time tasks stalled waiting for CPU", [
            prom("rate(node_pressure_cpu_waiting_seconds_total[5m])*100", "CPU Waiting %"),
            prom("rate(node_pressure_irq_stalled_seconds_total[5m])*100", "IRQ Stalled %"),
        ], "percent", 0),
    }
    layouts = [
        grid("Utilisation", [
            item(0,0,16,9,"cpu_used"), item(16,0,8,9,"cpu_mode_stacked"),
        ]),
        grid("Frequency & Throttling", [
            item(0,0,8,8,"cpu_freq_avg"), item(8,0,8,8,"cpu_throttle"),
            item(16,0,8,8,"cpu_freq"),
        ]),
        grid("Scheduler & Pressure", [
            item(0,0,8,8,"cpu_load"), item(8,0,8,8,"cpu_procs"),
            item(16,0,8,8,"cpu_schedstat"),
        ]),
        grid("Interrupts & System Activity", [
            item(0,0,8,7,"cpu_ctx"), item(8,0,8,7,"cpu_intr"),
            item(16,0,8,7,"psi_cpu"),
        ]),
        grid("Guest / VM", [
            item(0,0,12,7,"cpu_guest"),
        ]),
    ]
    return dashboard("cpu", "Bluefin Instrumentation — CPU",
        "Detailed CPU utilisation, frequency, throttling, scheduler, and pressure",
        panels, layouts)

# ══════════════════════════════════════════════════════════════════════════════
# 3. MEMORY
# ══════════════════════════════════════════════════════════════════════════════

def build_memory():
    panels = {
        "mem_overview": ts("Memory Overview", "Total, used, available, cached+buffers", [
            prom("node_memory_MemTotal_bytes", "Total"),
            prom("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", "Used"),
            prom("node_memory_MemAvailable_bytes", "Available"),
            prom("node_memory_Cached_bytes + node_memory_Buffers_bytes", "Cached+Buffers"),
        ], "bytes"),
        "mem_pct": ts("Memory & Swap Used %", "Utilisation as percentage", [
            prom("(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes * 100", "RAM Used %"),
            prom("(node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes) / node_memory_SwapTotal_bytes * 100", "Swap Used %"),
        ], "percent", 0, 100),
        "mem_detail": ts("Memory Detail", "Active, inactive, mapped, shmem, slab", [
            prom("node_memory_Active_bytes", "Active"),
            prom("node_memory_Inactive_bytes", "Inactive"),
            prom("node_memory_Mapped_bytes", "Mapped"),
            prom("node_memory_Shmem_bytes", "Shared"),
            prom("node_memory_Slab_bytes", "Slab"),
            prom("node_memory_KernelStack_bytes", "Kernel Stack"),
            prom("node_memory_PageTables_bytes", "Page Tables"),
        ], "bytes"),
        "mem_slab": ts("Slab Allocator", "Reclaimable vs unreclaimable slab memory", [
            prom("node_memory_SReclaimable_bytes", "Reclaimable"),
            prom("node_memory_SUnreclaim_bytes", "Unreclaimable"),
        ], "bytes"),
        "mem_dirty": ts("Dirty Pages & Writeback", "Pending filesystem writes — spikes = write pressure", [
            prom("node_memory_Dirty_bytes", "Dirty"),
            prom("node_memory_Writeback_bytes", "Writeback"),
        ], "bytes"),
        "mem_commit": ts("Memory Commitment", "How much has been allocated vs available", [
            prom("node_memory_CommitLimit_bytes", "Commit Limit"),
            prom("node_memory_Committed_AS_bytes", "Committed"),
        ], "bytes"),
        "mem_huge": ts("HugePages", "HugePage allocation", [
            prom("node_memory_HugePages_Total * node_memory_Hugepagesize_bytes", "Total HugePages"),
            prom("node_memory_HugePages_Free * node_memory_Hugepagesize_bytes", "Free HugePages"),
        ], "bytes"),
        "mem_swap_io": ts("Swap I/O", "Pages swapped in/out — active = memory pressure", [
            prom("rate(node_vmstat_pswpin[2m])", "Swap In/s"),
            prom("rate(node_vmstat_pswpout[2m])", "Swap Out/s"),
        ], "counts/sec"),
        "mem_pagefaults": ts("Page Faults", "Minor and major page faults per second", [
            prom("rate(node_vmstat_pgmajfault[2m])", "Major Faults/s (disk fetch)"),
            prom("rate(node_vmstat_pgfault[2m])", "Minor Faults/s (soft)"),
        ], "counts/sec"),
        "mem_paging": ts("Paging I/O", "Pages read/written to disk", [
            prom("rate(node_vmstat_pgpgin[2m])", "Pages In/s"),
            prom("rate(node_vmstat_pgpgout[2m])", "Pages Out/s"),
        ], "counts/sec"),
        "mem_hardware_corrupted": stat("Hardware Corrupted", "RAM pages marked bad by hardware",
            "node_memory_HardwareCorrupted_bytes", "bytes", "bytes", 0),
        "mem_oom": stat("OOM Kills", "Processes killed by OOM — any non-zero is bad",
            "node_vmstat_oom_kill", "kills", "decimal", 0),
        "psi_mem": ts("Memory Pressure (PSI)", "Time tasks stalled waiting for memory", [
            prom("rate(node_pressure_memory_stalled_seconds_total[5m])*100", "Stalled %"),
            prom("rate(node_pressure_memory_waiting_seconds_total[5m])*100", "Waiting %"),
        ], "percent", 0),
    }
    layouts = [
        grid("Overview", [
            item(0,0,16,8,"mem_overview"), item(16,0,8,8,"mem_pct"),
        ]),
        grid("Composition", [
            item(0,0,12,8,"mem_detail"), item(12,0,12,8,"mem_slab"),
        ]),
        grid("Write Pressure & Commitment", [
            item(0,0,12,8,"mem_dirty"), item(12,0,12,8,"mem_commit"),
        ]),
        grid("Swap & Paging", [
            item(0,0,8,8,"mem_swap_io"), item(8,0,8,8,"mem_pagefaults"),
            item(16,0,8,8,"mem_paging"),
        ]),
        grid("Pressure & Health", [
            item(0,0,12,7,"psi_mem"), item(12,0,6,7,"mem_huge"),
            item(18,0,3,7,"mem_hardware_corrupted"), item(21,0,3,7,"mem_oom"),
        ]),
    ]
    return dashboard("memory", "Bluefin Instrumentation — Memory",
        "RAM utilisation, composition, swap, paging, dirty pages, OOM, and pressure",
        panels, layouts)

# ══════════════════════════════════════════════════════════════════════════════
# 4. DISK
# ══════════════════════════════════════════════════════════════════════════════

def build_disk():
    panels = {
        "disk_throughput": ts("Disk Throughput", "Read/write bytes per second by device", [
            prom('rate(node_disk_read_bytes_total{device!~"loop.+"}[2m])', "Read {{device}}"),
            prom('rate(node_disk_written_bytes_total{device!~"loop.+"}[2m])', "Write {{device}}"),
        ], "bytes/sec"),
        "disk_iops": ts("IOPS", "I/O operations per second by device", [
            prom('rate(node_disk_reads_completed_total{device!~"loop.+"}[2m])', "Read {{device}}"),
            prom('rate(node_disk_writes_completed_total{device!~"loop.+"}[2m])', "Write {{device}}"),
        ], "counts/sec"),
        "disk_util": ts("Disk Utilisation %", "Time disk was busy — >80% sustained = saturation", [
            prom('rate(node_disk_io_time_seconds_total{device!~"loop.+"}[2m])*100', "{{device}}"),
        ], "percent", 0, 100),
        "disk_saturation": ts("Disk Saturation", "Weighted IO time — measures queue depth", [
            prom('rate(node_disk_io_time_weighted_seconds_total{device!~"loop.+"}[2m])', "{{device}}"),
        ], "decimal"),
        "disk_io_now": ts("Inflight I/O", "Operations currently in progress", [
            prom('node_disk_io_now{device!~"loop.+"}', "{{device}}"),
        ], "decimal", 0),
        "disk_latency_r": ts("Read Latency", "Average read latency per operation", [
            prom('rate(node_disk_read_time_seconds_total{device!~"loop.+"}[2m]) / rate(node_disk_reads_completed_total{device!~"loop.+"}[2m])', "{{device}}"),
        ], "seconds"),
        "disk_latency_w": ts("Write Latency", "Average write latency per operation", [
            prom('rate(node_disk_write_time_seconds_total{device!~"loop.+"}[2m]) / rate(node_disk_writes_completed_total{device!~"loop.+"}[2m])', "{{device}}"),
        ], "seconds"),
        "disk_space": ts("Filesystem Space Used %", "Utilisation by mountpoint", [
            prom('(node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs"} - node_filesystem_free_bytes{fstype!~"tmpfs|overlay|squashfs"}) / node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs"} * 100', "{{mountpoint}}"),
        ], "percent", 0, 100),
        "disk_space_bytes": ts("Filesystem Space Available", "Free bytes by mountpoint", [
            prom('node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs"}', "{{mountpoint}}"),
        ], "bytes"),
        "disk_inodes": ts("Inode Usage %", "Inode exhaustion can make a disk appear full", [
            prom('(node_filesystem_files{fstype!~"tmpfs|overlay|squashfs"} - node_filesystem_files_free{fstype!~"tmpfs|overlay|squashfs"}) / node_filesystem_files{fstype!~"tmpfs|overlay|squashfs"} * 100', "{{mountpoint}}"),
        ], "percent", 0, 100),
        "disk_discards": ts("Discards (TRIM)", "SSD TRIM activity", [
            prom('rate(node_disk_discards_completed_total{device!~"loop.+"}[2m])', "Discards/s {{device}}"),
            prom('rate(node_disk_discarded_sectors_total{device!~"loop.+"}[2m])', "Sectors/s {{device}}"),
        ], "counts/sec"),
        "xfs_ops": ts("XFS Operations", "XFS filesystem read/write calls", [
            prom("rate(node_xfs_read_calls_total[2m])", "Reads/s"),
            prom("rate(node_xfs_write_calls_total[2m])", "Writes/s"),
        ], "counts/sec"),
        "xfs_inode": ts("XFS Inode Activity", "Inode cache hits/misses", [
            prom("rate(node_xfs_inode_operation_found_total[2m])", "Found/s"),
            prom("rate(node_xfs_inode_operation_missed_total[2m])", "Missed/s"),
            prom("rate(node_xfs_inode_operation_reclaims_total[2m])", "Reclaimed/s"),
        ], "counts/sec"),
        "psi_io": ts("IO Pressure (PSI)", "Time tasks stalled waiting for IO", [
            prom("rate(node_pressure_io_stalled_seconds_total[5m])*100", "IO Stalled %"),
            prom("rate(node_pressure_io_waiting_seconds_total[5m])*100", "IO Waiting %"),
        ], "percent", 0),
    }
    layouts = [
        grid("Throughput & IOPS", [
            item(0,0,12,8,"disk_throughput"), item(12,0,12,8,"disk_iops"),
        ]),
        grid("Utilisation & Saturation", [
            item(0,0,8,8,"disk_util"), item(8,0,8,8,"disk_saturation"),
            item(16,0,8,8,"disk_io_now"),
        ]),
        grid("Latency", [
            item(0,0,12,8,"disk_latency_r"), item(12,0,12,8,"disk_latency_w"),
        ]),
        grid("Filesystem Space & Inodes", [
            item(0,0,8,8,"disk_space"), item(8,0,8,8,"disk_space_bytes"),
            item(16,0,8,8,"disk_inodes"),
        ]),
        grid("XFS & Pressure", [
            item(0,0,8,8,"xfs_ops"), item(8,0,8,8,"xfs_inode"),
            item(16,0,8,8,"psi_io"),
        ]),
        grid("TRIM / Discards", [
            item(0,0,12,7,"disk_discards"),
        ]),
    ]
    return dashboard("disk", "Bluefin Instrumentation — Disk",
        "Disk throughput, IOPS, latency, utilisation, filesystem space, inodes, XFS, and IO pressure",
        panels, layouts)

# ══════════════════════════════════════════════════════════════════════════════
# 5. NETWORK
# ══════════════════════════════════════════════════════════════════════════════

def build_network():
    panels = {
        "net_traffic": ts("Network Traffic", "RX/TX bytes/sec by interface", [
            prom('rate(node_network_receive_bytes_total{device!~"lo|veth.+|docker.+|br-.+"}[2m])', "RX {{device}}"),
            prom('rate(node_network_transmit_bytes_total{device!~"lo|veth.+|docker.+|br-.+"}[2m])', "TX {{device}}"),
        ], "bytes/sec"),
        "net_packets": ts("Packets/sec", "Received and transmitted packets", [
            prom('rate(node_network_receive_packets_total{device!~"lo|veth.+|docker.+|br-.+"}[2m])', "RX Packets {{device}}"),
            prom('rate(node_network_transmit_packets_total{device!~"lo|veth.+|docker.+|br-.+"}[2m])', "TX Packets {{device}}"),
        ], "counts/sec"),
        "net_errors": ts("Errors & Drops/sec", "Non-zero = driver or link issues", [
            prom('rate(node_network_receive_errs_total{device!~"lo|veth.+"}[2m])', "RX Errors {{device}}"),
            prom('rate(node_network_receive_drop_total{device!~"lo|veth.+"}[2m])', "RX Drops {{device}}"),
            prom('rate(node_network_transmit_errs_total{device!~"lo|veth.+"}[2m])', "TX Errors {{device}}"),
            prom('rate(node_network_transmit_drop_total{device!~"lo|veth.+"}[2m])', "TX Drops {{device}}"),
        ], "counts/sec"),
        "net_softnet": ts("Kernel Network Backlog", "Softnet processed, dropped, squeezed", [
            prom("rate(node_softnet_processed_total[2m])", "Processed/s"),
            prom("rate(node_softnet_dropped_total[2m])", "Dropped/s"),
            prom("rate(node_softnet_times_squeezed_total[2m])", "Squeezed/s"),
            prom("node_softnet_backlog_len", "Backlog Length"),
        ], "counts/sec"),
        "tcp_states": ts("TCP Connections", "Established connections and activity", [
            prom("node_netstat_Tcp_CurrEstab", "Established"),
            prom("rate(node_netstat_Tcp_ActiveOpens[2m])", "Active Opens/s"),
            prom("rate(node_netstat_Tcp_PassiveOpens[2m])", "Passive Opens/s"),
        ], "decimal", 0),
        "tcp_errors": ts("TCP Errors & Retransmits", "Connection quality indicators", [
            prom("rate(node_netstat_Tcp_RetransSegs[2m])", "Retransmits/s"),
            prom("rate(node_netstat_Tcp_InErrs[2m])", "In Errors/s"),
            prom("rate(node_netstat_Tcp_OutRsts[2m])", "Resets Sent/s"),
        ], "counts/sec"),
        "tcp_ext": ts("TCP Extended Health", "Listen drops, syn retransmits, timeouts", [
            prom("rate(node_netstat_TcpExt_ListenDrops[2m])", "Listen Drops/s"),
            prom("rate(node_netstat_TcpExt_ListenOverflows[2m])", "Listen Overflows/s"),
            prom("rate(node_netstat_TcpExt_TCPSynRetrans[2m])", "SYN Retrans/s"),
            prom("rate(node_netstat_TcpExt_TCPTimeouts[2m])", "Timeouts/s"),
        ], "counts/sec"),
        "udp_stats": ts("UDP Activity", "UDP datagrams and errors", [
            prom("rate(node_netstat_Udp_InDatagrams[2m])", "In/s"),
            prom("rate(node_netstat_Udp_OutDatagrams[2m])", "Out/s"),
            prom("rate(node_netstat_Udp_InErrors[2m])", "Errors/s"),
        ], "counts/sec"),
        "conntrack": ts("Connection Tracking", "Conntrack table fill — 100% = all new connections fail", [
            prom("node_nf_conntrack_entries", "Entries"),
            prom("node_nf_conntrack_entries_limit", "Limit"),
        ], "decimal"),
        "conntrack_pct": ts("Conntrack Fill %", "How full the conntrack table is", [
            prom("node_nf_conntrack_entries / node_nf_conntrack_entries_limit * 100", "Fill %"),
        ], "percent", 0, 100),
        "sockstat": ts("Socket Usage", "Open sockets by protocol", [
            prom("node_sockstat_TCP_inuse", "TCP In Use"),
            prom("node_sockstat_UDP_inuse", "UDP In Use"),
            prom("node_sockstat_TCP_tw", "TCP Time Wait"),
            prom("node_sockstat_TCP_orphan", "TCP Orphan"),
            prom("node_sockstat_sockets_used", "Total Sockets"),
        ], "decimal", 0),
        "net_carrier": ts("Carrier Changes", "Link flaps by interface", [
            prom('rate(node_network_carrier_changes_total{device!~"lo"}[5m])', "Changes/s {{device}}"),
        ], "counts/sec"),
    }
    layouts = [
        grid("Traffic", [
            item(0,0,12,8,"net_traffic"), item(12,0,12,8,"net_packets"),
        ]),
        grid("Errors & Kernel Backlog", [
            item(0,0,12,8,"net_errors"), item(12,0,12,8,"net_softnet"),
        ]),
        grid("TCP", [
            item(0,0,8,8,"tcp_states"), item(8,0,8,8,"tcp_errors"),
            item(16,0,8,8,"tcp_ext"),
        ]),
        grid("UDP & Sockets", [
            item(0,0,8,8,"udp_stats"), item(8,0,8,8,"sockstat"),
            item(16,0,8,8,"net_carrier"),
        ]),
        grid("Connection Tracking", [
            item(0,0,12,7,"conntrack"), item(12,0,12,7,"conntrack_pct"),
        ]),
    ]
    return dashboard("network", "Bluefin Instrumentation — Network",
        "Traffic, errors, TCP/UDP health, conntrack, socket usage, and kernel backlog",
        panels, layouts)

# ══════════════════════════════════════════════════════════════════════════════
# 6. BATTERY & POWER
# ══════════════════════════════════════════════════════════════════════════════

def build_battery():
    panels = {
        "bat_level": ts("Battery Level %", "BAT1 state of charge", [
            prom('node_power_supply_capacity{power_supply="BAT1"}', "Charge %"),
        ], "percent", 0, 100),
        "bat_power": ts("Power (W)", "Battery draw vs charger input (V × A)", [
            prom('node_power_supply_voltage_volt{power_supply="BAT1"} * node_power_supply_current_ampere{power_supply="BAT1"}', "BAT1 Power (W)"),
            prom('node_power_supply_voltage_volt{power_supply="ucsi-source-psy-USBC000:001"} * node_power_supply_current_ampere{power_supply="ucsi-source-psy-USBC000:001"}', "Charger Input (W)"),
            prom('node_power_supply_voltage_volt{power_supply="ucsi-source-psy-USBC000:002"} * node_power_supply_current_ampere{power_supply="ucsi-source-psy-USBC000:002"}', "USB-C 2 (W)"),
        ], "decimal"),
        "bat_voltage": ts("Battery Voltage (V)", "BAT1 terminal voltage", [
            prom('node_power_supply_voltage_volt{power_supply="BAT1"}', "Voltage (V)"),
            prom('node_power_supply_voltage_min{power_supply="BAT1"}', "Min Design (V)"),
        ], "decimal"),
        "bat_current": ts("Battery Current (A)", "Charge/discharge current", [
            prom('node_power_supply_current_ampere{power_supply="BAT1"}', "Current (A)"),
        ], "decimal"),
        "charger_voltage": ts("USB-C Port Voltages (V)", "Voltage on each USB-C port", [
            prom('node_power_supply_voltage_volt{power_supply=~"ucsi-source-psy-USBC000:.+"}', "{{power_supply}}"),
        ], "decimal"),
        "charger_current": ts("USB-C Port Currents (A)", "Current on each USB-C port", [
            prom('node_power_supply_current_ampere{power_supply=~"ucsi-source-psy-USBC000:.+"}', "{{power_supply}}"),
        ], "decimal"),
        "bat_health": stat("Battery Health %", "charge_full / charge_full_design",
            'node_power_supply_charge_full{power_supply="BAT1"} / node_power_supply_charge_full_design{power_supply="BAT1"} * 100',
            "Health %", "percent", 1),
        "bat_cycles": stat("Charge Cycles", "Total full charge cycles",
            'node_power_supply_cyclecount{power_supply="BAT1"}', "Cycles", "decimal", 0),
        "bat_capacity_wh": stat("Full Charge Capacity", "Current maximum capacity",
            'node_power_supply_charge_full{power_supply="BAT1"} * node_power_supply_voltage_volt{power_supply="BAT1"}',
            "Wh (approx)", "decimal", 2),
        "ac_online": stat("AC Online", "1 = plugged in, 0 = on battery",
            'node_power_supply_online{power_supply="ACAD"}', "AC", "decimal", 0),
        "hwmon_power": ts("hwmon Power Rail (W)", "ACPI/EC reported power consumption", [
            prom('node_hwmon_power_watt', "{{chip}} {{sensor}}"),
        ], "decimal"),
        "hwmon_curr": ts("hwmon Current (A)", "EC reported current readings", [
            prom('node_hwmon_curr_amps', "{{chip}} {{sensor}}"),
        ], "decimal"),
        "hwmon_volts": ts("hwmon Voltages (V)", "EC reported voltage rails", [
            prom('node_hwmon_in_volts', "{{chip}} {{sensor}}"),
        ], "decimal"),
    }
    layouts = [
        grid("Charge State", [
            item(0,0,12,8,"bat_level"), item(12,0,12,8,"bat_power"),
        ]),
        grid("Voltage & Current", [
            item(0,0,8,8,"bat_voltage"), item(8,0,8,8,"bat_current"),
            item(16,0,8,8,"charger_voltage"),
        ]),
        grid("USB-C Ports", [
            item(0,0,12,7,"charger_voltage"), item(12,0,12,7,"charger_current"),
        ]),
        grid("Battery Health", [
            item(0,0,4,6,"bat_health"), item(4,0,4,6,"bat_cycles"),
            item(8,0,4,6,"bat_capacity_wh"), item(12,0,4,6,"ac_online"),
        ]),
        grid("hwmon Power Rails", [
            item(0,0,8,7,"hwmon_power"), item(8,0,8,7,"hwmon_curr"),
            item(16,0,8,7,"hwmon_volts"),
        ]),
    ]
    return dashboard("battery-power", "Bluefin Instrumentation — Battery & Power",
        "Battery charge, voltage, current, USB-C ports, health, cycles, and power rails",
        panels, layouts)

# ══════════════════════════════════════════════════════════════════════════════
# 7. THERMALS
# ══════════════════════════════════════════════════════════════════════════════

def build_thermals():
    panels = {
        "temp_cpu_pkg": ts("CPU Package Temperature", "Package temp with throttle/critical thresholds", [
            prom('node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor="temp1"}', "Package"),
            prom('node_hwmon_temp_max_celsius{chip="platform_coretemp_0", sensor="temp1"}', "Max (throttle)"),
            prom('node_hwmon_temp_crit_celsius{chip="platform_coretemp_0", sensor="temp1"}', "Critical"),
        ], "decimal"),
        "temp_cpu_cores": ts("CPU Core Temperatures", "Per-core temperatures", [
            prom('node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor!="temp1"}', "{{sensor}}"),
        ], "decimal"),
        "temp_cpu_summary": ts("CPU Temp — Min / Avg / Max", "Core temperature spread", [
            prom('min(node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor!="temp1"})', "Min Core"),
            prom('avg(node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor!="temp1"})', "Avg Core"),
            prom('max(node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor!="temp1"})', "Max Core"),
            prom('node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor="temp1"}', "Package"),
        ], "decimal"),
        "temp_nvme": ts("NVMe Drive Temperature", "NVMe composite and sensor temps", [
            prom('node_hwmon_temp_celsius{chip="nvme_nvme0", sensor="temp1"}', "Composite"),
            prom('node_hwmon_temp_celsius{chip="nvme_nvme0", sensor="temp2"}', "Sensor 1"),
            prom('node_hwmon_temp_celsius{chip="nvme_nvme0", sensor="temp3"}', "Sensor 2"),
        ], "decimal"),
        "temp_ec": ts("EC Temperatures", "Embedded controller sensors — CPU, DDR, battery, PECI", [
            prom('node_hwmon_temp_celsius{chip="cros_ec_dev_2_auto_cros_ec_hwmon_11_auto", sensor="temp1"}', "Local (F75397)"),
            prom('node_hwmon_temp_celsius{chip="cros_ec_dev_2_auto_cros_ec_hwmon_11_auto", sensor="temp2"}', "CPU (F75303)"),
            prom('node_hwmon_temp_celsius{chip="cros_ec_dev_2_auto_cros_ec_hwmon_11_auto", sensor="temp3"}', "Battery"),
            prom('node_hwmon_temp_celsius{chip="cros_ec_dev_2_auto_cros_ec_hwmon_11_auto", sensor="temp4"}', "DDR"),
            prom('node_hwmon_temp_celsius{chip="cros_ec_dev_2_auto_cros_ec_hwmon_11_auto", sensor="temp5"}', "PECI"),
        ], "decimal"),
        "temp_thermal_zones": ts("ACPI Thermal Zones", "Kernel thermal zone temperatures", [
            prom('node_thermal_zone_temp', "zone{{zone}}"),
        ], "decimal"),
        "fan_rpm": ts("Fan Speed (RPM)", "System fan speed over time", [
            prom('node_hwmon_fan_rpm{chip="platform_pnp0c0b:00"}', "Fan"),
            prom('node_hwmon_fan_target_rpm{chip="platform_pnp0c0b:00"}', "Target"),
        ], "decimal"),
        "fan_fault": ts("Fan Fault", "1 = fan fault detected", [
            prom('node_hwmon_fan_fault', "{{chip}} {{sensor}}"),
        ], "decimal"),
        "cpu_throttle_temp": ts("CPU Throttle Events/sec", "Thermal throttling — non-zero = temp limit hit", [
            prom("sum(rate(node_cpu_core_throttles_total[2m]))", "Core Throttles/s"),
            prom("sum(rate(node_cpu_package_throttles_total[2m]))", "Package Throttles/s"),
        ], "counts/sec"),
        "temp_headroom": ts("CPU Temp Headroom", "Distance from critical threshold", [
            prom('node_hwmon_temp_crit_celsius{chip="platform_coretemp_0", sensor="temp1"} - node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor="temp1"}', "°C to Critical"),
            prom('node_hwmon_temp_max_celsius{chip="platform_coretemp_0", sensor="temp1"} - node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor="temp1"}', "°C to Max"),
        ], "decimal", 0),
        "temp_alarms": ts("Temperature Alarms", "1 = threshold breached", [
            prom('node_hwmon_temp_alarm', "Alarm {{chip}} {{sensor}}"),
            prom('node_hwmon_temp_crit_alarm_celsius', "Crit Alarm {{chip}} {{sensor}}"),
            prom('node_hwmon_temp_max_alarm_celsius', "Max Alarm {{chip}} {{sensor}}"),
        ], "decimal"),
    }
    layouts = [
        grid("CPU Temperatures", [
            item(0,0,8,8,"temp_cpu_pkg"), item(8,0,8,8,"temp_cpu_summary"),
            item(16,0,8,8,"temp_cpu_cores"),
        ]),
        grid("Throttling & Headroom", [
            item(0,0,12,8,"cpu_throttle_temp"), item(12,0,12,8,"temp_headroom"),
        ]),
        grid("Storage & Platform", [
            item(0,0,12,8,"temp_nvme"), item(12,0,12,8,"temp_ec"),
        ]),
        grid("ACPI Thermal Zones", [
            item(0,0,24,7,"temp_thermal_zones"),
        ]),
        grid("Fan", [
            item(0,0,12,7,"fan_rpm"), item(12,0,8,7,"fan_fault"),
        ]),
        grid("Alarms", [
            item(0,0,24,7,"temp_alarms"),
        ]),
    ]
    return dashboard("thermals", "Bluefin Instrumentation — Thermals",
        "CPU core/package temperatures, throttling, headroom, NVMe, EC sensors, fan, and ACPI zones",
        panels, layouts)

# ══════════════════════════════════════════════════════════════════════════════
# 8. HEALTH
# ══════════════════════════════════════════════════════════════════════════════

def build_health():
    panels = {
        "psi_all": ts("Pressure Stall Index — All", "Fraction of time tasks stalled on each resource", [
            prom("rate(node_pressure_cpu_waiting_seconds_total[5m])*100", "CPU Waiting"),
            prom("rate(node_pressure_io_stalled_seconds_total[5m])*100", "IO Stalled"),
            prom("rate(node_pressure_io_waiting_seconds_total[5m])*100", "IO Waiting"),
            prom("rate(node_pressure_memory_stalled_seconds_total[5m])*100", "Memory Stalled"),
            prom("rate(node_pressure_memory_waiting_seconds_total[5m])*100", "Memory Waiting"),
            prom("rate(node_pressure_irq_stalled_seconds_total[5m])*100", "IRQ Stalled"),
        ], "percent", 0),
        "edac": ts("Memory Hardware Errors (EDAC)", "Correctable and uncorrectable RAM errors — any UE = replace RAM", [
            prom("rate(node_edac_correctable_errors_total[5m])", "CE Rate/s"),
            prom("rate(node_edac_uncorrectable_errors_total[5m])", "UE Rate/s ⚠️"),
            prom("rate(node_edac_csrow_correctable_errors_total[5m])", "CE (csrow) Rate/s"),
        ], "counts/sec"),
        "oom_procs": ts("OOM & Process Health", "Out-of-memory kills and process queue", [
            prom("node_vmstat_oom_kill", "OOM Kills (total)"),
            prom("node_procs_blocked", "Procs Blocked"),
            prom("node_procs_running", "Procs Running"),
        ], "decimal", 0),
        "fds": ts("File Descriptors", "System-wide FD usage vs limit", [
            prom("node_filefd_allocated", "Allocated"),
            prom("node_filefd_maximum", "Maximum"),
        ], "decimal"),
        "fd_pct": ts("FD Exhaustion %", "How close to the FD limit", [
            prom("node_filefd_allocated / node_filefd_maximum * 100", "FD Used %"),
        ], "percent", 0, 100),
        "entropy": ts("Entropy Pool", "Available entropy — low values may stall crypto ops", [
            prom("node_entropy_available_bits", "Available"),
            prom("node_entropy_pool_size_bits", "Pool Size"),
        ], "decimal"),
        "ntp": ts("NTP Clock Quality", "Clock synchronisation offset and error bounds", [
            prom("node_timex_sync_status", "Sync Status (1=synced)"),
            prom("node_timex_offset_seconds * 1000", "Offset (ms)"),
            prom("node_timex_maxerror_seconds * 1000", "Max Error (ms)"),
            prom("node_timex_estimated_error_seconds * 1000", "Est Error (ms)"),
        ], "decimal"),
        "ntp_freq": ts("NTP Frequency Adjustment", "PLL frequency correction ratio", [
            prom("node_timex_frequency_adjustment_ratio", "Freq Adj Ratio"),
        ], "decimal"),
        "selinux": ts("SELinux Mode", "Current enforcement mode (1=enforcing, 0=permissive)", [
            prom("node_selinux_current_mode", "Current Mode"),
            prom("node_selinux_config_mode", "Config Mode"),
        ], "decimal"),
        "mem_corrupted": ts("Hardware Corrupted Memory", "Pages marked bad by hardware — any non-zero is bad", [
            prom("node_memory_HardwareCorrupted_bytes", "Corrupted Bytes"),
        ], "bytes"),
        "net_drops": ts("Network Drops & Errors", "Interface-level errors and drops", [
            prom('rate(node_network_receive_drop_total{device!~"lo|veth.+"}[2m])', "RX Drop {{device}}"),
            prom('rate(node_network_transmit_drop_total{device!~"lo|veth.+"}[2m])', "TX Drop {{device}}"),
            prom('rate(node_network_receive_errs_total{device!~"lo|veth.+"}[2m])', "RX Err {{device}}"),
            prom('rate(node_softnet_dropped_total[2m])', "Softnet Drop"),
        ], "counts/sec"),
        "conntrack_health": ts("Conntrack Fill %", "Connection table saturation — 100% = new connections refused", [
            prom("node_nf_conntrack_entries / node_nf_conntrack_entries_limit * 100", "Fill %"),
        ], "percent", 0, 100),
        "disk_errors": ts("Filesystem Errors", "Device errors reported by filesystem", [
            prom('node_filesystem_device_error{fstype!~"tmpfs|overlay|squashfs"}', "{{mountpoint}} errors"),
        ], "decimal"),
        "boot_info": stat("Last Boot", "Time since last boot",
            "time() - node_boot_time_seconds", "uptime", "seconds", 0),
    }
    layouts = [
        grid("Resource Pressure (PSI)", [
            item(0,0,24,8,"psi_all"),
        ]),
        grid("Memory Health", [
            item(0,0,12,8,"edac"), item(12,0,8,8,"mem_corrupted"),
            item(20,0,4,8,"oom_procs"),
        ]),
        grid("File Descriptors & Entropy", [
            item(0,0,8,7,"fds"), item(8,0,8,7,"fd_pct"),
            item(16,0,8,7,"entropy"),
        ]),
        grid("Clock & Time Sync", [
            item(0,0,16,7,"ntp"), item(16,0,8,7,"ntp_freq"),
        ]),
        grid("Network & Connection Health", [
            item(0,0,12,7,"net_drops"), item(12,0,12,7,"conntrack_health"),
        ]),
        grid("System Integrity", [
            item(0,0,8,7,"selinux"), item(8,0,8,7,"disk_errors"),
            item(16,0,4,7,"boot_info"),
        ]),
    ]
    return dashboard("health", "Bluefin Instrumentation — Health",
        "PSI, EDAC, OOM, FDs, entropy, NTP, SELinux, network errors, and conntrack health",
        panels, layouts)

# ══════════════════════════════════════════════════════════════════════════════
# 9. CONTAINERS
# ══════════════════════════════════════════════════════════════════════════════

# Shared join fragment — enriches id-keyed metrics with the container name label
_INFO = "* on(id) group_left(name) podman_container_info"

def build_containers():
    panels = {
        # ── Fleet status row ────────────────────────────────────────────────
        "ct_running": stat("Running", "Containers currently in running state",
            "count(podman_container_state == 2)", "containers", "decimal", 0),
        "ct_stopped": stat("Stopped / Exited", "Containers not in running state",
            "count(podman_container_state != 2)", "containers", "decimal", 0),
        "ct_total_mem": stat("Total Memory", "Combined RSS across all containers",
            "sum(podman_container_mem_usage_bytes)", "bytes", "bytes", 1),
        "ct_total_cpu": stat("Total CPU", "Combined CPU rate across all containers",
            "sum(rate(podman_container_cpu_seconds_total[2m])) * 100", "%", "percent", 2),
        "ct_images": stat("Images", "Total container images stored locally",
            "count(podman_image_info)", "images", "decimal", 0),
        "ct_volumes": stat("Volumes", "Named volumes managed by Podman",
            "count(podman_volume_info)", "volumes", "decimal", 0),

        # ── Per-container CPU ────────────────────────────────────────────────
        "ct_cpu": ts("CPU Usage % — per container", "User + system CPU time rate per container", [
            prom(f"rate(podman_container_cpu_seconds_total[2m]) {_INFO} * 100", "{{name}} user"),
            prom(f"rate(podman_container_cpu_system_seconds_total[2m]) {_INFO} * 100", "{{name}} sys"),
        ], "percent", 0),
        "ct_cpu_total": ts("CPU Usage % — combined", "Total CPU for each container (user + system)", [
            prom(f"(rate(podman_container_cpu_seconds_total[2m]) + rate(podman_container_cpu_system_seconds_total[2m])) {_INFO} * 100", "{{name}}"),
        ], "percent", 0),

        # ── Per-container memory ─────────────────────────────────────────────
        "ct_mem": ts("Memory Usage — per container", "RSS memory in bytes per container", [
            prom(f"podman_container_mem_usage_bytes {_INFO}", "{{name}}"),
        ], "bytes", 0),
        "ct_mem_pct": ts("Memory % of Host — per container", "Container RSS as % of total host RAM", [
            prom(f"podman_container_mem_usage_bytes {_INFO} / node_memory_MemTotal_bytes * 100", "{{name}}"),
        ], "percent", 0),

        # ── Per-container network ────────────────────────────────────────────
        "ct_net_bytes": ts("Network Throughput — per container", "RX and TX bytes/sec per container", [
            prom(f"rate(podman_container_net_input_total[2m]) {_INFO}", "{{name}} RX"),
            prom(f"rate(podman_container_net_output_total[2m]) {_INFO}", "{{name}} TX"),
        ], "bytes/sec", 0),
        "ct_net_packets": ts("Network Packets — per container", "RX and TX packets/sec per container", [
            prom(f"rate(podman_container_net_input_packets_total[2m]) {_INFO}", "{{name}} RX pkt"),
            prom(f"rate(podman_container_net_output_packets_total[2m]) {_INFO}", "{{name}} TX pkt"),
        ], "counts/sec", 0),
        "ct_net_errors": ts("Network Errors & Drops — per container", "Input/output errors and drops per container", [
            prom(f"rate(podman_container_net_input_errors_total[2m]) {_INFO}", "{{name}} RX err"),
            prom(f"rate(podman_container_net_output_errors_total[2m]) {_INFO}", "{{name}} TX err"),
            prom(f"rate(podman_container_net_input_dropped_total[2m]) {_INFO}", "{{name}} RX drop"),
            prom(f"rate(podman_container_net_output_dropped_total[2m]) {_INFO}", "{{name}} TX drop"),
        ], "counts/sec", 0),

        # ── Per-container block I/O ──────────────────────────────────────────
        "ct_block": ts("Block I/O — per container", "Disk read/write bytes per container", [
            prom(f"rate(podman_container_block_input_total[2m]) {_INFO}", "{{name}} read"),
            prom(f"rate(podman_container_block_output_total[2m]) {_INFO}", "{{name}} write"),
        ], "bytes/sec", 0),

        # ── Per-container process count ──────────────────────────────────────
        "ct_pids": ts("Process Count — per container", "Number of processes (PIDs) in each container", [
            prom(f"podman_container_pids {_INFO}", "{{name}}"),
        ], "decimal", 0),

        # ── Per-container uptime ─────────────────────────────────────────────
        "ct_uptime": ts("Container Uptime", "Seconds since each container started", [
            prom(f"(time() - podman_container_started_seconds) {_INFO}", "{{name}}"),
        ], "seconds", 0),

        # ── Image disk footprint ─────────────────────────────────────────────
        "ct_rootfs": ts("Image Layer Size — per container", "Read-only image layer footprint on disk", [
            prom(f"podman_container_rootfs_size_bytes {_INFO}", "{{name}}"),
        ], "bytes", 0),
        "ct_rw": ts("Writable Layer Size — per container", "Data written by container since creation", [
            prom(f"podman_container_rw_size_bytes {_INFO}", "{{name}}"),
        ], "bytes", 0),
        "ct_image_sizes": ts("All Image Sizes on Disk", "Size of every image stored locally", [
            prom("podman_image_size", "{{repository}}:{{tag}}"),
        ], "bytes", 0),

        # ── State timeline ───────────────────────────────────────────────────
        "ct_state": ts("Container State", "State code over time (2=running, 5=exited, 0=created)", [
            prom(f"podman_container_state {_INFO}", "{{name}}"),
        ], "decimal", 0),
    }

    layouts = [
        grid("Fleet Overview", [
            item(0,0,4,4,"ct_running"), item(4,0,4,4,"ct_stopped"),
            item(8,0,4,4,"ct_total_mem"), item(12,0,4,4,"ct_total_cpu"),
            item(16,0,4,4,"ct_images"), item(20,0,4,4,"ct_volumes"),
        ]),
        grid("CPU", [
            item(0,0,12,8,"ct_cpu_total"), item(12,0,12,8,"ct_cpu"),
        ]),
        grid("Memory", [
            item(0,0,12,8,"ct_mem"), item(12,0,12,8,"ct_mem_pct"),
        ]),
        grid("Network", [
            item(0,0,12,8,"ct_net_bytes"), item(12,0,12,8,"ct_net_packets"),
        ]),
        grid("Network Errors & Block I/O", [
            item(0,0,12,8,"ct_net_errors"), item(12,0,12,8,"ct_block"),
        ]),
        grid("Processes & Uptime", [
            item(0,0,12,7,"ct_pids"), item(12,0,12,7,"ct_uptime"),
        ]),
        grid("Disk Footprint", [
            item(0,0,8,8,"ct_rootfs"), item(8,0,8,8,"ct_rw"),
            item(16,0,8,8,"ct_image_sizes"),
        ]),
        grid("State", [
            item(0,0,12,6,"ct_state"),
        ], open_=False),
    ]

    return dashboard("containers", "Bluefin Instrumentation — Containers",
        "Podman container CPU, memory, network, block I/O, processes, uptime, and image sizes",
        panels, layouts)


# ── Summary additions ─────────────────────────────────────────────────────────
# Patch build_summary to include a containers row. We rebuild it here so the
# original function signature stays intact and the main() list controls ordering.

def build_summary():
    panels = {
        # ── Row 1: system gauges ─────────────────────────────────────────────
        "s_cpu_gauge": gauge("CPU Used", "Overall CPU utilisation",
            '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)',
            "CPU %", "percent"),
        "s_mem_gauge": gauge("Memory Used", "RAM utilisation",
            "(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes * 100",
            "RAM %", "percent"),
        "s_disk_gauge": gauge("Disk Busy", "Max disk utilisation across all devices",
            'max(rate(node_disk_io_time_seconds_total{device!~"loop.+"}[2m]))*100',
            "Disk %", "percent"),
        "s_bat_gauge": gauge("Battery", "BAT1 state of charge",
            'node_power_supply_capacity{power_supply="BAT1"}',
            "BAT %", "percent"),

        # ── Row 2: key stats ─────────────────────────────────────────────────
        "s_cpu_temp": stat("CPU Temp", "Package temperature",
            'node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor="temp1"}',
            "°C", "decimal", 1),
        "s_fan": stat("Fan", "Fan speed",
            'node_hwmon_fan_rpm{chip="platform_pnp0c0b:00"}',
            "RPM", "decimal", 0),
        "s_load": stat("Load (1m)", "1-minute load average",
            "node_load1", "load", "decimal", 2),
        "s_uptime": stat("Uptime", "Time since last boot",
            "time() - node_boot_time_seconds", "uptime", "seconds", 0),
        "s_oom": stat("OOM Kills", "Out-of-memory kills since boot",
            "node_vmstat_oom_kill", "kills", "decimal", 0),
        "s_bat_health": stat("Battery Health", "Charge capacity vs design",
            'node_power_supply_charge_full{power_supply="BAT1"} / node_power_supply_charge_full_design{power_supply="BAT1"} * 100',
            "health %", "percent", 1),
        "s_entropy": stat("Entropy", "Available entropy bits",
            "node_entropy_available_bits", "bits", "decimal", 0),
        "s_ntp": stat("NTP Sync", "1 = synced",
            "node_timex_sync_status", "synced", "decimal", 0),

        # ── Row 3: system sparklines ─────────────────────────────────────────
        "s_cpu_spark": ts("CPU Usage %", "CPU over time", [
            prom('100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)', "CPU"),
        ], "percent", 0, 100),
        "s_mem_spark": ts("Memory Used %", "RAM over time", [
            prom("(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes * 100", "RAM"),
        ], "percent", 0, 100),
        "s_net_spark": ts("Network", "Total RX/TX", [
            prom('sum(rate(node_network_receive_bytes_total{device!~"lo|veth.+|docker.+|br-.+"}[2m]))', "RX"),
            prom('sum(rate(node_network_transmit_bytes_total{device!~"lo|veth.+|docker.+|br-.+"}[2m]))', "TX"),
        ], "bytes/sec"),
        "s_disk_spark": ts("Disk I/O", "Total read/write", [
            prom('sum(rate(node_disk_read_bytes_total{device!~"loop.+"}[2m]))', "Read"),
            prom('sum(rate(node_disk_written_bytes_total{device!~"loop.+"}[2m]))', "Write"),
        ], "bytes/sec"),

        # ── Row 4: system health ─────────────────────────────────────────────
        "s_psi": ts("Pressure (PSI)", "Resource stall fractions", [
            prom("rate(node_pressure_cpu_waiting_seconds_total[5m])*100", "CPU"),
            prom("rate(node_pressure_io_stalled_seconds_total[5m])*100", "IO"),
            prom("rate(node_pressure_memory_stalled_seconds_total[5m])*100", "Memory"),
            prom("rate(node_pressure_irq_stalled_seconds_total[5m])*100", "IRQ"),
        ], "percent", 0),
        "s_temp_spark": ts("Temperatures", "CPU package + NVMe", [
            prom('node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor="temp1"}', "CPU Package"),
            prom('max(node_hwmon_temp_celsius{chip="platform_coretemp_0", sensor!="temp1"})', "CPU Max Core"),
            prom('node_hwmon_temp_celsius{chip="nvme_nvme0", sensor="temp1"}', "NVMe"),
        ], "decimal"),
        "s_swap_spark": ts("Swap Activity", "Pages swapped in/out", [
            prom("rate(node_vmstat_pswpin[2m])", "Swap In"),
            prom("rate(node_vmstat_pswpout[2m])", "Swap Out"),
        ], "counts/sec"),

        # ── Row 5: containers summary ────────────────────────────────────────
        "s_ct_running": stat("Containers Running", "Containers currently in running state",
            "count(podman_container_state == 2)", "containers", "decimal", 0),
        "s_ct_stopped": stat("Containers Stopped", "Containers not in running state",
            "count(podman_container_state != 2)", "containers", "decimal", 0),
        "s_ct_total_mem": stat("Container Memory", "Combined RSS across all containers",
            "sum(podman_container_mem_usage_bytes)", "bytes", "bytes", 1),
        "s_ct_total_cpu": stat("Container CPU", "Combined CPU % across all containers",
            "sum(rate(podman_container_cpu_seconds_total[2m])) * 100", "%", "percent", 2),
        "s_ct_cpu_ts": ts("Container CPU %", "CPU usage per container over time", [
            prom(f"(rate(podman_container_cpu_seconds_total[2m]) + rate(podman_container_cpu_system_seconds_total[2m])) {_INFO} * 100", "{{name}}"),
        ], "percent", 0),
        "s_ct_mem_ts": ts("Container Memory", "Memory usage per container over time", [
            prom(f"podman_container_mem_usage_bytes {_INFO}", "{{name}}"),
        ], "bytes", 0),
        "s_ct_net_ts": ts("Container Network", "Total RX+TX bytes/sec per container", [
            prom(f"rate(podman_container_net_input_total[2m]) {_INFO}", "{{name}} RX"),
            prom(f"rate(podman_container_net_output_total[2m]) {_INFO}", "{{name}} TX"),
        ], "bytes/sec", 0),
    }

    layouts = [
        grid("Bluefin Instrumentation — System Overview", [
            item(0,0,6,8,"s_cpu_gauge"), item(6,0,6,8,"s_mem_gauge"),
            item(12,0,6,8,"s_disk_gauge"), item(18,0,6,8,"s_bat_gauge"),
        ]),
        grid("Key Indicators", [
            item(0,0,3,4,"s_cpu_temp"), item(3,0,3,4,"s_fan"),
            item(6,0,3,4,"s_load"),    item(9,0,3,4,"s_uptime"),
            item(12,0,3,4,"s_oom"),    item(15,0,3,4,"s_bat_health"),
            item(18,0,3,4,"s_entropy"), item(21,0,3,4,"s_ntp"),
        ]),
        grid("Trends", [
            item(0,0,12,7,"s_cpu_spark"), item(12,0,12,7,"s_mem_spark"),
            item(0,7,12,7,"s_net_spark"), item(12,7,12,7,"s_disk_spark"),
        ]),
        grid("Health at a Glance", [
            item(0,0,10,7,"s_psi"), item(10,0,8,7,"s_temp_spark"),
            item(18,0,6,7,"s_swap_spark"),
        ]),
        grid("Containers", [
            item(0,0,3,4,"s_ct_running"), item(3,0,3,4,"s_ct_stopped"),
            item(6,0,6,4,"s_ct_total_mem"), item(12,0,6,4,"s_ct_total_cpu"),
            item(0,4,8,7,"s_ct_cpu_ts"), item(8,4,8,7,"s_ct_mem_ts"),
            item(16,4,8,7,"s_ct_net_ts"),
        ]),
    ]

    return dashboard("summary", "Bluefin Instrumentation",
        "System overview — CPU, Memory, Disk, Network, Battery, Thermals, Health, Containers",
        panels, layouts)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🔵 Bluefin Instrumentation — building dashboard suite\n")

    # Ensure project exists
    r = requests.get(f"{BASE}/api/v1/projects/{PROJECT}")
    if r.status_code != 200:
        requests.post(f"{BASE}/api/v1/projects", json={
            "kind": "Project",
            "metadata": {"name": PROJECT},
            "spec": {"display": {"name": "Bluefin Instrumentation"}}
        })
        print(f"  ✅ created project '{PROJECT}'")

    # Delete old monolith
    print("\n🗑  Removing old dashboard...")
    delete_dashboard("laptop-performance")

    # Build all dashboards
    dashboards = [
        ("Summary",        build_summary()),
        ("CPU",            build_cpu()),
        ("Memory",         build_memory()),
        ("Disk",           build_disk()),
        ("Network",        build_network()),
        ("Battery & Power",build_battery()),
        ("Thermals",       build_thermals()),
        ("Health",         build_health()),
        ("Containers",     build_containers()),
    ]

    print("\n📊 Creating dashboards...")
    ok = 0
    for label, dash in dashboards:
        print(f"  → {label}")
        if create_or_update(dash):
            ok += 1

    print(f"\n{'✅' if ok == len(dashboards) else '⚠️ '} {ok}/{len(dashboards)} dashboards created")
    print(f"\n🔗 Open: http://localhost:8080/projects/{PROJECT}")
