"""Server lifecycle and resource validation."""

import math

from database import utc_now
from resource_allocator import RESOURCE_FIELDS, refresh_server_status


REGIONS = ("Chennai", "Mumbai", "Bangalore", "Delhi", "Hyderabad")
CAPACITY_COLUMNS = {
    "cpu": "cpu_capacity",
    "ram": "ram_capacity",
    "storage": "storage_capacity",
    "network": "network_capacity",
}


def validate_server(data):
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")
    name = data.get("name")
    region = data.get("region")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("Server name must contain 1 to 80 characters.")
    if region not in REGIONS:
        raise ValueError(f"Region must be one of: {', '.join(REGIONS)}.")
    capacities = {}
    for resource in CAPACITY_COLUMNS:
        value = data.get(f"{resource}_capacity")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value <= 0
            or value > 1_000_000
            or not math.isfinite(value)
        ):
            raise ValueError(f"{resource.title()} capacity must be a positive number no greater than 1,000,000.")
        capacities[resource] = float(value)
    return name.strip(), region, capacities


def create_server(connection, data):
    name, region, capacities = validate_server(data)
    now = utc_now()
    number = connection.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM servers").fetchone()[0]
    server_id = f"cloud-server-{number:02d}"
    connection.execute(
        """
        INSERT INTO servers
            (server_id, name, region, cpu_capacity, ram_capacity, storage_capacity,
             network_capacity, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'IDLE', ?, ?)
        """,
        (
            server_id,
            name,
            region,
            capacities["cpu"],
            capacities["ram"],
            capacities["storage"],
            capacities["network"],
            now,
            now,
        ),
    )
    internal_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        "INSERT INTO events (event_type, server_id, description, timestamp) VALUES ('SERVER_ADDED', ?, ?, ?)",
        (internal_id, f"Server {server_id} was added in {region}.", now),
    )
    return server_id


def delete_server(connection, server_id):
    server = connection.execute("SELECT * FROM servers WHERE server_id = ?", (server_id,)).fetchone()
    if not server:
        raise LookupError("Server not found.")
    assigned = connection.execute(
        "SELECT COUNT(*) FROM workloads WHERE assigned_server_id = ? AND status = 'ALLOCATED'",
        (server["id"],),
    ).fetchone()[0]
    if assigned:
        raise ValueError("Cannot remove a server while it has allocated workloads. Complete or reallocate them first.")
    connection.execute(
        "INSERT INTO events (event_type, server_id, description, timestamp) VALUES ('SERVER_REMOVED', ?, ?, ?)",
        (server["id"], f"Server {server_id} was removed.", utc_now()),
    )
    connection.execute("DELETE FROM servers WHERE id = ?", (server["id"],))


def fail_server(connection, server_id):
    server = connection.execute("SELECT * FROM servers WHERE server_id = ?", (server_id,)).fetchone()
    if not server:
        raise LookupError("Server not found.")
    if server["status"] == "OFFLINE":
        raise ValueError("Server is already offline.")
    connection.execute(
        "UPDATE servers SET status = 'OFFLINE', updated_at = ? WHERE id = ?",
        (utc_now(), server["id"]),
    )
    connection.execute(
        """
        INSERT INTO alerts (server_id, severity, alert_type, message, status, created_at)
        VALUES (?, 'CRITICAL', 'SERVER_FAILURE', ?, 'ACTIVE', ?)
        """,
        (server["id"], f"{server_id} is offline. Existing workload allocations are preserved.", utc_now()),
    )
    connection.execute(
        "INSERT INTO events (event_type, server_id, description, timestamp) VALUES ('SERVER_FAILURE', ?, ?, ?)",
        (server["id"], f"Failure simulated for {server_id}; allocations were preserved.", utc_now()),
    )
    return server_id


def recover_server(connection, server_id):
    server = connection.execute("SELECT * FROM servers WHERE server_id = ?", (server_id,)).fetchone()
    if not server:
        raise LookupError("Server not found.")
    if server["status"] != "OFFLINE":
        raise ValueError("Server is not offline.")
    connection.execute(
        "UPDATE servers SET status = 'IDLE', updated_at = ? WHERE id = ?",
        (utc_now(), server["id"]),
    )
    connection.execute(
        """
        UPDATE alerts SET status = 'RESOLVED', resolved_at = ?
        WHERE server_id = ? AND alert_type = 'SERVER_FAILURE' AND status = 'ACTIVE'
        """,
        (utc_now(), server["id"]),
    )
    refresh_server_status(connection, server["id"])
    connection.execute(
        "INSERT INTO events (event_type, server_id, description, timestamp) VALUES ('SERVER_RECOVERED', ?, ?, ?)",
        (server["id"], f"{server_id} is back online; preserved allocations were restored.", utc_now()),
    )
    return server_id
