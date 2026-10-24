"""Workload validation, allocation, completion, and reallocation."""

import math
import uuid

from database import utc_now
from resource_allocator import (
    RESOURCE_FIELDS,
    _record_event,
    allocate_to_server,
    find_best_fit,
    reject_workload,
    release_workload,
)


PRIORITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
PRIORITY_RANK = {name: rank for rank, name in enumerate(PRIORITIES)}


def validate_workload(data):
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")
    name = data.get("name")
    priority = data.get("priority")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise ValueError("Workload name must contain 1 to 100 characters.")
    if priority not in PRIORITIES:
        raise ValueError(f"Priority must be one of: {', '.join(PRIORITIES)}.")
    requirements = {}
    for resource, (_, _, required) in RESOURCE_FIELDS.items():
        value = data.get(required)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value <= 0
            or value > 1_000_000
            or not math.isfinite(value)
        ):
            raise ValueError(f"{required.replace('_', ' ').title()} must be a positive number no greater than 1,000,000.")
        requirements[resource] = float(value)
    return name.strip(), priority, requirements


def create_workload(connection, data):
    name, priority, requirements = validate_workload(data)
    workload_id = f"WL-{uuid.uuid4().hex[:10].upper()}"
    now = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO workloads
            (workload_id, name, cpu_required, ram_required, storage_required,
             network_required, priority, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
        """,
        (
            workload_id,
            name,
            requirements["cpu"],
            requirements["ram"],
            requirements["storage"],
            requirements["network"],
            priority,
            now,
        ),
    )
    workload = connection.execute("SELECT * FROM workloads WHERE id = ?", (cursor.lastrowid,)).fetchone()
    _record_event(connection, "WORKLOAD_CREATED", None, workload["id"], f"Workload {workload_id} was created.")
    allocate_workload(connection, workload)
    return workload_id


def _try_reallocation(connection, workload):
    lower_priority = connection.execute(
        """
        SELECT w.*, s.server_id AS assigned_server_name
        FROM workloads w
        JOIN servers s ON s.id = w.assigned_server_id
        WHERE w.status = 'ALLOCATED' AND w.priority IN ('LOW', 'MEDIUM')
        ORDER BY CASE w.priority WHEN 'LOW' THEN 0 ELSE 1 END, w.created_at
        """
    ).fetchall()
    connection.execute("SAVEPOINT priority_reallocation")
    moved = []
    for candidate in lower_priority:
        old_server_id = candidate["assigned_server_id"]
        release_workload(connection, candidate, "REALLOCATED")
        connection.execute(
            "UPDATE workloads SET status = 'PENDING', assigned_server_id = NULL WHERE id = ?",
            (candidate["id"],),
        )
        moved.append((candidate, old_server_id))
        server = find_best_fit(connection, workload)
        if server:
            allocated_to = allocate_to_server(connection, workload, server, "REALLOCATED")
            _record_event(
                connection,
                "PRIORITY_REALLOCATION",
                server["id"],
                workload["id"],
                f"Moved {len(moved)} lower-priority workload(s) to allocate {workload['workload_id']}.",
            )
            for displaced, old_id in moved:
                _place_displaced_workload(connection, displaced, old_id)
            connection.execute("RELEASE priority_reallocation")
            return allocated_to
    connection.execute("ROLLBACK TO priority_reallocation")
    connection.execute("RELEASE priority_reallocation")
    return None


def _place_displaced_workload(connection, workload, old_server_id):
    preferred = connection.execute(
        "SELECT * FROM servers WHERE id = ? AND status != 'OFFLINE'",
        (old_server_id,),
    ).fetchone()
    server = None
    if preferred:
        fits = all(
            preferred[capacity] - preferred[allocated] >= workload[required]
            for allocated, capacity, required in RESOURCE_FIELDS.values()
        )
        if fits:
            server = preferred
    if not server:
        server = find_best_fit(connection, workload)
    if server:
        allocate_to_server(connection, workload, server, "REALLOCATED")
    else:
        connection.execute(
            "UPDATE workloads SET status = 'PENDING', assigned_server_id = NULL WHERE id = ?",
            (workload["id"],),
        )
        _record_event(
            connection,
            "REALLOCATION_WAITING",
            old_server_id,
            workload["id"],
            f"{workload['workload_id']} is pending after priority reallocation.",
        )
        from alert_manager import create_alert

        create_alert(
            connection,
            "WARNING",
            "WORKLOAD_WAITING",
            f"Lower-priority workload {workload['workload_id']} is waiting after a priority reallocation.",
            workload_id=workload["id"],
        )


def allocate_workload(connection, workload, permit_reallocation=True):
    server = find_best_fit(connection, workload)
    if server:
        return allocate_to_server(connection, workload, server)
    if permit_reallocation and workload["priority"] in ("HIGH", "CRITICAL"):
        moved_allocation = _try_reallocation(connection, workload)
        if moved_allocation:
            return moved_allocation
    reject_workload(connection, workload, "insufficient CPU, RAM, storage, or network resources.")
    return None


def allocate_existing_workload(connection, workload_id):
    workload = connection.execute("SELECT * FROM workloads WHERE workload_id = ?", (workload_id,)).fetchone()
    if not workload:
        raise LookupError("Workload not found.")
    if workload["status"] not in ("PENDING", "REJECTED"):
        raise ValueError("Only pending or rejected workloads can be allocated.")
    connection.execute("UPDATE workloads SET status = 'PENDING' WHERE id = ?", (workload["id"],))
    workload = connection.execute("SELECT * FROM workloads WHERE id = ?", (workload["id"],)).fetchone()
    return allocate_workload(connection, workload)


def complete_workload(connection, workload_id):
    workload = connection.execute("SELECT * FROM workloads WHERE workload_id = ?", (workload_id,)).fetchone()
    if not workload:
        raise LookupError("Workload not found.")
    if workload["status"] != "ALLOCATED":
        raise ValueError("Only allocated workloads can be completed.")
    release_workload(connection, workload)
    now = utc_now()
    connection.execute(
        "UPDATE workloads SET status = 'COMPLETED', assigned_server_id = NULL, completed_at = ? WHERE id = ?",
        (now, workload["id"]),
    )
    return workload_id


def reallocate_workload(connection, workload_id):
    workload = connection.execute("SELECT * FROM workloads WHERE workload_id = ?", (workload_id,)).fetchone()
    if not workload:
        raise LookupError("Workload not found.")
    if workload["status"] != "ALLOCATED":
        if workload["status"] in ("PENDING", "REJECTED"):
            return allocate_existing_workload(connection, workload_id)
        raise ValueError("Only active or waiting workloads can be reallocated.")
    old_server_id = workload["assigned_server_id"]
    previous = connection.execute("SELECT * FROM servers WHERE id = ?", (old_server_id,)).fetchone()
    connection.execute("SAVEPOINT manual_reallocation")
    release_workload(connection, workload, "REALLOCATED")
    connection.execute(
        "UPDATE workloads SET status = 'PENDING', assigned_server_id = NULL WHERE id = ?",
        (workload["id"],),
    )
    server = find_best_fit(connection, workload, exclude_server_ids={old_server_id})
    if server:
        result = allocate_to_server(connection, workload, server, "REALLOCATED")
        _record_event(
            connection,
            "REALLOCATION",
            server["id"],
            workload["id"],
            f"{workload_id} moved from {previous['server_id']} to {server['server_id']}.",
        )
        connection.execute("RELEASE manual_reallocation")
        return result
    connection.execute("ROLLBACK TO manual_reallocation")
    connection.execute("RELEASE manual_reallocation")
    raise ValueError("No other online server has sufficient resources; the existing allocation was preserved.")


def delete_workload(connection, workload_id):
    workload = connection.execute("SELECT * FROM workloads WHERE workload_id = ?", (workload_id,)).fetchone()
    if not workload:
        raise LookupError("Workload not found.")
    if workload["status"] == "ALLOCATED":
        raise ValueError("Complete or reallocate an active workload before deleting it.")
    connection.execute(
        "INSERT INTO events (event_type, workload_id, description, timestamp) VALUES ('WORKLOAD_DELETED', ?, ?, ?)",
        (workload["id"], f"Workload {workload_id} was deleted.", utc_now()),
    )
    connection.execute("DELETE FROM workloads WHERE id = ?", (workload["id"],))
