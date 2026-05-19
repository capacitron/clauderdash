"""Collect system metrics (CPU, RAM, disk, load) every 10 seconds."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from datetime import datetime, timedelta

import psutil

from ..db import connect as get_db

log = logging.getLogger("system-metrics")


def collect_system_metrics() -> dict:
    """Gather current system metrics."""
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    load_avg = os.getloadavg()[0]
    process_count = len(psutil.pids())

    return {
        "cpu_percent": cpu_percent,
        "memory_percent": memory.percent,
        "memory_available_mb": memory.available // (1024 * 1024),
        "disk_percent": (disk.used / disk.total) * 100 if disk.total else 0,
        "load_avg_1min": load_avg,
        "process_count": process_count,
    }


def get_top_processes(limit: int = 5) -> list[dict]:
    """Get top processes by RAM usage."""
    try:
        processes = []
        for proc in psutil.process_iter(
            ["pid", "name", "rss", "cpu_percent", "create_time"]
        ):
            try:
                info = proc.info
                if info["rss"] is None:
                    continue
                rss_mb = info["rss"] / (1024 * 1024)
                create_time = info["create_time"]
                uptime_hours = (time.time() - create_time) / 3600 if create_time else 0
                processes.append(
                    {
                        "pid": info["pid"],
                        "name": info["name"],
                        "rss_mb": round(rss_mb, 1),
                        "cpu_percent": info["cpu_percent"] or 0,
                        "uptime_hours": round(uptime_hours, 1),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # Sort by RAM, take top N
        processes.sort(key=lambda p: p["rss_mb"], reverse=True)
        return processes[:limit]
    except Exception as e:
        log.error("Failed to get top processes: %s", e)
        return []


def insert_system_metrics(db, metrics: dict) -> None:
    """Insert collected metrics into database."""
    timestamp = datetime.utcnow().isoformat()
    try:
        with db:
            db.execute(
                """
                INSERT OR REPLACE INTO system_metrics
                (timestamp, cpu_percent, memory_percent, memory_available_mb,
                 disk_percent, load_avg_1min, process_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    metrics["cpu_percent"],
                    metrics["memory_percent"],
                    metrics["memory_available_mb"],
                    metrics["disk_percent"],
                    metrics["load_avg_1min"],
                    metrics["process_count"],
                ),
            )
    except Exception as e:
        log.error("Failed to insert system metrics: %s", e)


def prune_old_metrics(db, days: int = 7) -> None:
    """Remove metrics older than N days."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        with db:
            result = db.execute(
                "DELETE FROM system_metrics WHERE timestamp < ?", (cutoff,)
            )
            if result.rowcount > 0:
                log.info("Pruned %d old metrics", result.rowcount)
    except Exception as e:
        log.error("Failed to prune old metrics: %s", e)


async def run_system_metrics_collector() -> None:
    """Background task: collect system metrics every 10 seconds."""
    log.info("Starting system metrics collector (10s interval)")
    try:
        while True:
            try:
                metrics = collect_system_metrics()
                db = get_db()
                insert_system_metrics(db, metrics)
                db.close()

                # Prune every hour
                if int(time.time()) % 3600 < 10:
                    db = get_db()
                    prune_old_metrics(db)
                    db.close()
            except Exception as e:
                log.error("Error in metrics collection loop: %s", e)

            await asyncio.sleep(10)
    except asyncio.CancelledError:
        log.info("System metrics collector cancelled")
