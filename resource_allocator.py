"""Best-fit resource allocation and release operations."""

from alert_manager import check_resource_thresholds, create_alert
from database import utc_now


RESOURCE_FIELDS = {
    "cpu": ("cpu_allocated", "cpu_capacity", "cpu_required"),
    "ram": ("ram_allocated", "ram_capacity", "ram_required"),
    "storage": ("storage_allocated", "storage_capacity", "storage_required"),
    "network": ("network_allocated", "network_capacity", "network_required"),
}


def utilization(server):
    values = [
        server[f"{resource}_allocated"] / server[f"{resource}_capacity"]
        for resource in RESOURCE_FIELDS
    ]
    return sum(values) / len(values) * 100


def server_utilizations(server):
    return {
        resource: round(server[f"{resource}_allocated"] / server[f"{resource}_capacity"] * 100, 1)
        for resource in RESOURCE_FIELDS
    }


def available_resources(server):
    return {
        resource: round(server[f"{resource}_capacity"] - server[f"{resource}_allocated"], 2)
        for resource in RESOURCE_FIELDS
    }


def find_best_fit(connection, workload, exclude_server_ids=None):
    excluded = set(exclude_server_ids or [])
    servers = connection.execute(
        "SELECT * FROM servers WHERE status != 'OFFLINE' ORDER BY server_id"
    ).fetchall()
    suitable = []
    for server in servers:
        if server["id"] in excluded:
            continue
        remaining_ratios = []
        fits = True
        for resource, (allocated, capacity, required) in RESOURCE_FIELDS.items():
            remaining = server[capacity] - server[allocated] - workload[required]
            if remaining < -1e-9:
                fits = False
                break
            remaining_ratios.append(max(0, remaining) / server[capacity])
        if fits:
            suitable.append((sum(remaining_ratios), server))
    return min(suitable, key=lambda item: (item[0], item[1]["server_id"]))[1] if suitable else None


def _record_history(connection, server_id, workload_id, values, event_type):
    connection.execute(
        """
        INSERT INTO resource_history
            (server_id, workload_id, cpu_value, ram_value, storage_value,
             network_value, event_type, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            server_id,
            workload_id,
            values["cpu"],
            values["ram"],
            values["storage"],
            values["network"],
            event_type,
            utc_now(),
        ),
    )


def _record_event(connection, event_type, server_id, workload_id, description):
    connection.execute(
        "INSERT INTO events (event_type, server_id, workload_id, description, timestamp) VALUES (?, ?, ?, ?, ?)",
        (event_type, server_id, workload_id, description, utc_now()),
    )


def refresh_server_status(connection, server_id):
    server = connection.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    if not server or server["status"] == "OFFLINE":
        return server
    ratios = [
        server[f"{resource}_allocated"] / server[f"{resource}_capacity"]
        for resource in RESOURCE_FIELDS
    ]
    peak = max(ratios)
    status = "OVERLOADED" if peak > 0.90 else ("IDLE" if peak <= 0.10 else "ACTIVE")
    connection.execute(
        "UPDATE servers SET status = ?, updated_at = ? WHERE id = ?",
        (status, utc_now(), server_id),
    )
    server = connection.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    check_resource_thresholds(connection, server)
    if status == "OVERLOADED":
        create_alert(
            connection,
            "CRITICAL",
            "SERVER_OVERLOADED",
            f"{server['server_id']} is overloaded; a resource exceeds 90% utilization.",
            server_id,
        )
    else:
        connection.execute(
            "UPDATE alerts SET status = 'RESOLVED', resolved_at = ? WHERE server_id = ? AND alert_type = 'SERVER_OVERLOADED' AND status = 'ACTIVE'",
            (utc_now(), server_id),
        )
    return server


def allocate_to_server(connection, workload, server, event_type="ALLOCATED"):
    now = utc_now()
    values = {
        "cpu": workload["cpu_required"],
        "ram": workload["ram_required"],
        "storage": workload["storage_required"],
        "network": workload["network_required"],
    }
    connection.execute(
        """
        UPDATE servers SET
            cpu_allocated = cpu_allocated + ?, ram_allocated = ram_allocated + ?,
            storage_allocated = storage_allocated + ?, network_allocated = network_allocated + ?,
            updated_at = ?
        WHERE id = ?
        """,
        (values["cpu"], values["ram"], values["storage"], values["network"], now, server["id"]),
    )
    connection.execute(
        """
        INSERT INTO allocations
            (workload_id, server_id, cpu_allocated, ram_allocated, storage_allocated,
             network_allocated, allocated_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
        """,
        (workload["id"], server["id"], values["cpu"], values["ram"], values["storage"], values["network"], now),
    )
    connection.execute(
        "UPDATE workloads SET status = 'ALLOCATED', assigned_server_id = ? WHERE id = ?",
        (server["id"], workload["id"]),
    )
    connection.execute(
        """
        UPDATE alerts SET status = 'RESOLVED', resolved_at = ?
        WHERE workload_id = ? AND alert_type IN (
            'HIGH_PRIORITY_WAITING', 'WORKLOAD_WAITING', 'ALLOCATION_FAILED'
        )
          AND status = 'ACTIVE'
        """,
        (now, workload["id"]),
    )
    _record_history(connection, server["id"], workload["id"], values, event_type)
    _record_event(
        connection,
        event_type,
        server["id"],
        workload["id"],
        f"{workload['workload_id']} allocated to {server['server_id']}.",
    )
    refresh_server_status(connection, server["id"])
    return server["server_id"]


def release_workload(connection, workload, event_type="RELEASED"):
    allocation = connection.execute(
        """
        SELECT * FROM allocations
        WHERE workload_id = ? AND status = 'ACTIVE'
        ORDER BY id DESC LIMIT 1
        """,
        (workload["id"],),
    ).fetchone()
    if not allocation:
        raise ValueError("No active allocation exists for this workload.")
    server_id = allocation["server_id"]
    server = (
        connection.execute("SELECT server_id FROM servers WHERE id = ?", (server_id,)).fetchone()
        if server_id is not None
        else None
    )
    if server_id is not None:
        connection.execute(
            """
            UPDATE servers SET
                cpu_allocated = MAX(0, cpu_allocated - ?),
                ram_allocated = MAX(0, ram_allocated - ?),
                storage_allocated = MAX(0, storage_allocated - ?),
                network_allocated = MAX(0, network_allocated - ?),
                updated_at = ?
            WHERE id = ?
            """,
            (
                allocation["cpu_allocated"],
                allocation["ram_allocated"],
                allocation["storage_allocated"],
                allocation["network_allocated"],
                utc_now(),
                server_id,
            ),
        )
    connection.execute(
        "UPDATE allocations SET status = 'RELEASED', released_at = ? WHERE id = ?",
        (utc_now(), allocation["id"]),
    )
    values = {
        "cpu": allocation["cpu_allocated"],
        "ram": allocation["ram_allocated"],
        "storage": allocation["storage_allocated"],
        "network": allocation["network_allocated"],
    }
    _record_history(connection, server_id, workload["id"], values, event_type)
    _record_event(
        connection,
        event_type,
        server_id,
        workload["id"],
        f"{workload['workload_id']} resources released from "
        f"{server['server_id'] if server else 'a removed server'}.",
    )
    if server_id is not None:
        refresh_server_status(connection, server_id)
    return server_id


def reject_workload(connection, workload, reason):
    is_waiting = workload["priority"] in ("HIGH", "CRITICAL")
    status = "PENDING" if is_waiting else "REJECTED"
    connection.execute(
        "UPDATE workloads SET status = ?, assigned_server_id = NULL WHERE id = ?",
        (status, workload["id"]),
    )
    create_alert(
        connection,
        "WARNING",
        "ALLOCATION_FAILED",
        f"Workload {workload['workload_id']} could not be allocated: {reason}",
        workload_id=workload["id"],
    )
    if is_waiting:
        create_alert(
            connection,
            "WARNING",
            "HIGH_PRIORITY_WAITING",
            f"High-priority workload {workload['workload_id']} is waiting: {reason}",
            workload_id=workload["id"],
        )
    _record_event(
        connection,
        "PRIORITY_WAITING" if is_waiting else "REJECTED",
        None,
        workload["id"],
        f"{workload['workload_id']} {('waiting' if is_waiting else 'rejected')}: {reason}",
    )
    _record_history(
        connection,
        None,
        workload["id"],
        {resource: workload[f"{resource}_required"] for resource in RESOURCE_FIELDS},
        "REJECTED",
    )
