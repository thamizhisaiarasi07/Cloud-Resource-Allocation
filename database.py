"""SQLite setup and connection helpers for the local cloud simulation."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


DATABASE_PATH = os.environ.get("RESOURCE_DB_PATH", "resource_allocation.db")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_connection(path=None):
    connection = sqlite3.connect(path or DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(path=None):
    with get_connection(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                region TEXT NOT NULL,
                cpu_capacity REAL NOT NULL CHECK(cpu_capacity > 0),
                ram_capacity REAL NOT NULL CHECK(ram_capacity > 0),
                storage_capacity REAL NOT NULL CHECK(storage_capacity > 0),
                network_capacity REAL NOT NULL CHECK(network_capacity > 0),
                cpu_allocated REAL NOT NULL DEFAULT 0,
                ram_allocated REAL NOT NULL DEFAULT 0,
                storage_allocated REAL NOT NULL DEFAULT 0,
                network_allocated REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'IDLE',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workload_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                cpu_required REAL NOT NULL CHECK(cpu_required > 0),
                ram_required REAL NOT NULL CHECK(ram_required > 0),
                storage_required REAL NOT NULL CHECK(storage_required > 0),
                network_required REAL NOT NULL CHECK(network_required > 0),
                priority TEXT NOT NULL CHECK(priority IN ('LOW','MEDIUM','HIGH','CRITICAL')),
                status TEXT NOT NULL CHECK(status IN ('PENDING','ALLOCATED','REJECTED','COMPLETED')),
                assigned_server_id INTEGER REFERENCES servers(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workload_id INTEGER REFERENCES workloads(id) ON DELETE SET NULL,
                server_id INTEGER REFERENCES servers(id) ON DELETE SET NULL,
                cpu_allocated REAL NOT NULL,
                ram_allocated REAL NOT NULL,
                storage_allocated REAL NOT NULL,
                network_allocated REAL NOT NULL,
                allocated_at TEXT NOT NULL,
                released_at TEXT,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER REFERENCES servers(id) ON DELETE SET NULL,
                workload_id INTEGER REFERENCES workloads(id) ON DELETE SET NULL,
                severity TEXT NOT NULL CHECK(severity IN ('INFO','WARNING','CRITICAL')),
                alert_type TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS resource_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER REFERENCES servers(id) ON DELETE SET NULL,
                workload_id INTEGER REFERENCES workloads(id) ON DELETE SET NULL,
                cpu_value REAL NOT NULL,
                ram_value REAL NOT NULL,
                storage_value REAL NOT NULL,
                network_value REAL NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                server_id INTEGER REFERENCES servers(id) ON DELETE SET NULL,
                workload_id INTEGER REFERENCES workloads(id) ON DELETE SET NULL,
                description TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workloads_status ON workloads(status);
            CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
            CREATE INDEX IF NOT EXISTS idx_history_server ON resource_history(server_id);
            """
        )
        count = connection.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
        if count == 0:
            now = utc_now()
            initial_servers = [
                ("cloud-server-01", "Cloud Server 01", "Chennai", 16, 32, 500, 1000),
                ("cloud-server-02", "Cloud Server 02", "Mumbai", 8, 16, 250, 500),
                ("cloud-server-03", "Cloud Server 03", "Bangalore", 16, 32, 1000, 1000),
                ("cloud-server-04", "Cloud Server 04", "Delhi", 8, 16, 500, 500),
                ("cloud-server-05", "Cloud Server 05", "Hyderabad", 32, 64, 2000, 2000),
            ]
            connection.executemany(
                """
                INSERT INTO servers (
                    server_id, name, region, cpu_capacity, ram_capacity,
                    storage_capacity, network_capacity, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'IDLE', ?, ?)
                """,
                [(*server, now, now) for server in initial_servers],
            )
