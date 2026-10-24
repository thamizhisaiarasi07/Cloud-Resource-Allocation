"""Flask API and dashboard for the local cloud resource simulator."""

import os
import re
from collections import Counter

from flask import Flask, jsonify, render_template, request

from alert_manager import resolve_alert
from database import get_connection, initialize_database
from resource_allocator import RESOURCE_FIELDS, available_resources, server_utilizations, utilization
from server_manager import create_server, delete_server, fail_server, recover_server
from workload_manager import (
    allocate_existing_workload,
    complete_workload,
    create_workload,
    delete_workload,
    reallocate_workload,
)


app = Flask(__name__)
app.config["DATABASE"] = "resource_allocation.db"
initialize_database(app.config["DATABASE"])

VALID_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _server_json(server):
    item = dict(server)
    item["available"] = available_resources(server)
    item["utilization"] = round(utilization(server), 1)
    item["resource_utilization"] = server_utilizations(server)
    return item


def _workload_json(connection, workload):
    item = dict(workload)
    if item.get("assigned_server_id"):
        server = connection.execute(
            "SELECT server_id, name FROM servers WHERE id = ?",
            (item["assigned_server_id"],),
        ).fetchone()
        item["assigned_server"] = server["server_id"] if server else None
        item["assigned_server_name"] = server["name"] if server else None
    else:
        item["assigned_server"] = None
        item["assigned_server_name"] = None
    return item


def _require_valid_id(value, kind):
    if not VALID_ID.fullmatch(value or ""):
        raise ValueError(f"Invalid {kind} ID.")


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")
    return data


@app.errorhandler(ValueError)
def handle_value_error(error):
    return jsonify({"error": str(error)}), 400


@app.errorhandler(LookupError)
def handle_lookup_error(error):
    return jsonify({"error": str(error)}), 404


@app.errorhandler(404)
def handle_not_found(_error):
    return jsonify({"error": "The requested resource was not found."}), 404


@app.errorhandler(500)
def handle_server_error(_error):
    app.logger.exception("Unexpected server error")
    return jsonify({"error": "An unexpected server error occurred."}), 500


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/dashboard")
def dashboard():
    with get_connection(app.config["DATABASE"]) as connection:
        servers = connection.execute("SELECT * FROM servers ORDER BY server_id").fetchall()
        workloads = connection.execute("SELECT status, COUNT(*) AS total FROM workloads GROUP BY status").fetchall()
        counts = {row["status"]: row["total"] for row in workloads}
        alerts = connection.execute(
            "SELECT status, severity, COUNT(*) AS total FROM alerts GROUP BY status, severity"
        ).fetchall()
        alert_counts = Counter()
        for row in alerts:
            if row["status"] == "ACTIVE":
                alert_counts[row["severity"]] += row["total"]
        totals = {
            "servers": len(servers),
            "active_servers": sum(server["status"] == "ACTIVE" for server in servers),
            "workloads": sum(counts.values()),
            "allocated_workloads": counts.get("ALLOCATED", 0),
            "pending_workloads": counts.get("PENDING", 0),
            "rejected_workloads": counts.get("REJECTED", 0),
            "completed_workloads": counts.get("COMPLETED", 0),
        }
        resources = {}
        for resource in RESOURCE_FIELDS:
            capacity = sum(server[f"{resource}_capacity"] for server in servers)
            allocated = sum(server[f"{resource}_allocated"] for server in servers)
            resources[resource] = {
                "capacity": round(capacity, 2),
                "allocated": round(allocated, 2),
                "available": round(capacity - allocated, 2),
                "utilization": round(allocated / capacity * 100, 1) if capacity else 0,
            }
        result = {
            **totals,
            "idle_servers": sum(server["status"] == "IDLE" for server in servers),
            "overloaded_servers": sum(server["status"] == "OVERLOADED" for server in servers),
            "offline_servers": sum(server["status"] == "OFFLINE" for server in servers),
            "overall_utilization": round(
                sum(utilization(server) for server in servers) / len(servers), 1
            ) if servers else 0,
            "resources": resources,
            "alerts": {
                "active": sum(alert_counts.values()),
                "warning": alert_counts["WARNING"],
                "critical": alert_counts["CRITICAL"],
                "resolved": sum(row["total"] for row in alerts if row["status"] == "RESOLVED"),
            },
            "servers_data": [_server_json(server) for server in servers],
        }
        return jsonify(result)


@app.get("/api/servers")
def list_servers():
    search = request.args.get("search", "").strip().lower()
    status = request.args.get("status", "ALL").upper()
    if status not in ("ALL", "ACTIVE", "IDLE", "OVERLOADED", "OFFLINE"):
        raise ValueError("Invalid server status filter.")
    with get_connection(app.config["DATABASE"]) as connection:
        rows = connection.execute("SELECT * FROM servers ORDER BY server_id").fetchall()
        items = [
            _server_json(row)
            for row in rows
            if (status == "ALL" or row["status"] == status)
            and (
                not search
                or search in row["name"].lower()
                or search in row["server_id"].lower()
                or search in row["region"].lower()
            )
        ]
        return jsonify(items)


@app.get("/api/servers/<server_id>")
def get_server(server_id):
    _require_valid_id(server_id, "server")
    with get_connection(app.config["DATABASE"]) as connection:
        server = connection.execute("SELECT * FROM servers WHERE server_id = ?", (server_id,)).fetchone()
        if not server:
            raise LookupError("Server not found.")
        return jsonify(_server_json(server))


@app.post("/api/servers")
def add_server():
    with get_connection(app.config["DATABASE"]) as connection:
        server_id = create_server(connection, _json_body())
        server = connection.execute("SELECT * FROM servers WHERE server_id = ?", (server_id,)).fetchone()
        return jsonify({"message": "Server added.", "server": _server_json(server)}), 201


@app.delete("/api/servers/<server_id>")
def remove_server(server_id):
    _require_valid_id(server_id, "server")
    with get_connection(app.config["DATABASE"]) as connection:
        delete_server(connection, server_id)
        return jsonify({"message": "Server removed."})


@app.post("/api/servers/<server_id>/fail")
def simulate_failure(server_id):
    _require_valid_id(server_id, "server")
    with get_connection(app.config["DATABASE"]) as connection:
        fail_server(connection, server_id)
        return jsonify({"message": f"{server_id} marked offline. Existing allocations are preserved."})


@app.post("/api/servers/<server_id>/recover")
def recover(server_id):
    _require_valid_id(server_id, "server")
    with get_connection(app.config["DATABASE"]) as connection:
        recover_server(connection, server_id)
        return jsonify({"message": f"{server_id} recovered."})


@app.get("/api/workloads")
def list_workloads():
    search = request.args.get("search", "").strip().lower()
    status = request.args.get("status", "ALL").upper()
    if status not in ("ALL", "PENDING", "ALLOCATED", "REJECTED", "COMPLETED"):
        raise ValueError("Invalid workload status filter.")
    with get_connection(app.config["DATABASE"]) as connection:
        rows = connection.execute("SELECT * FROM workloads ORDER BY created_at DESC, id DESC").fetchall()
        items = [
            _workload_json(connection, row)
            for row in rows
            if (status == "ALL" or row["status"] == status)
            and (
                not search
                or search in row["name"].lower()
                or search in row["workload_id"].lower()
            )
        ]
        return jsonify(items)


@app.get("/api/workloads/<workload_id>")
def get_workload(workload_id):
    _require_valid_id(workload_id, "workload")
    with get_connection(app.config["DATABASE"]) as connection:
        workload = connection.execute(
            "SELECT * FROM workloads WHERE workload_id = ?", (workload_id,)
        ).fetchone()
        if not workload:
            raise LookupError("Workload not found.")
        return jsonify(_workload_json(connection, workload))


@app.post("/api/workloads")
def add_workload():
    with get_connection(app.config["DATABASE"]) as connection:
        workload_id = create_workload(connection, _json_body())
        workload = connection.execute(
            "SELECT * FROM workloads WHERE workload_id = ?", (workload_id,)
        ).fetchone()
        return jsonify(
            {
                "message": "Workload created.",
                "workload": _workload_json(connection, workload),
            }
        ), 201


@app.post("/api/workloads/<workload_id>/allocate")
def allocate(workload_id):
    _require_valid_id(workload_id, "workload")
    with get_connection(app.config["DATABASE"]) as connection:
        server_id = allocate_existing_workload(connection, workload_id)
        if not server_id:
            workload = connection.execute(
                "SELECT status FROM workloads WHERE workload_id = ?", (workload_id,)
            ).fetchone()
            return jsonify(
                {
                    "message": "Insufficient resources. The workload could not be allocated.",
                    "workload_id": workload_id,
                    "status": workload["status"],
                }
            ), 409
        return jsonify({"message": "Workload allocated.", "workload_id": workload_id, "server_id": server_id})


@app.post("/api/workloads/<workload_id>/complete")
def complete(workload_id):
    _require_valid_id(workload_id, "workload")
    with get_connection(app.config["DATABASE"]) as connection:
        complete_workload(connection, workload_id)
        return jsonify({"message": "Workload completed; resources released."})


@app.post("/api/workloads/<workload_id>/reallocate")
def reallocate(workload_id):
    _require_valid_id(workload_id, "workload")
    with get_connection(app.config["DATABASE"]) as connection:
        server_id = reallocate_workload(connection, workload_id)
        return jsonify({"message": "Workload reallocated.", "workload_id": workload_id, "server_id": server_id})


@app.delete("/api/workloads/<workload_id>")
def remove_workload(workload_id):
    _require_valid_id(workload_id, "workload")
    with get_connection(app.config["DATABASE"]) as connection:
        delete_workload(connection, workload_id)
        return jsonify({"message": "Workload deleted."})


@app.get("/api/alerts")
def list_alerts():
    status = request.args.get("status", "ALL").upper()
    if status not in ("ALL", "ACTIVE", "RESOLVED"):
        raise ValueError("Invalid alert status filter.")
    with get_connection(app.config["DATABASE"]) as connection:
        rows = connection.execute(
            """
            SELECT a.*, s.server_id, w.workload_id
            FROM alerts a
            LEFT JOIN servers s ON s.id = a.server_id
            LEFT JOIN workloads w ON w.id = a.workload_id
            ORDER BY a.created_at DESC, a.id DESC
            """
        ).fetchall()
        return jsonify([dict(row) for row in rows if status == "ALL" or row["status"] == status])


@app.post("/api/alerts/<int:alert_id>/resolve")
def resolve(alert_id):
    with get_connection(app.config["DATABASE"]) as connection:
        if not resolve_alert(connection, alert_id):
            raise LookupError("Active alert not found.")
        return jsonify({"message": "Alert resolved."})


@app.get("/api/history")
def history():
    with get_connection(app.config["DATABASE"]) as connection:
        rows = connection.execute(
            """
            SELECT h.*, s.server_id, w.workload_id
            FROM resource_history h
            LEFT JOIN servers s ON s.id = h.server_id
            LEFT JOIN workloads w ON w.id = h.workload_id
            ORDER BY h.timestamp DESC, h.id DESC LIMIT 100
            """
        ).fetchall()
        return jsonify([dict(row) for row in rows])


@app.get("/api/history/<server_id>")
def server_history(server_id):
    _require_valid_id(server_id, "server")
    with get_connection(app.config["DATABASE"]) as connection:
        server = connection.execute("SELECT id FROM servers WHERE server_id = ?", (server_id,)).fetchone()
        if not server:
            raise LookupError("Server not found.")
        rows = connection.execute(
            """
            SELECT h.*, s.server_id, w.workload_id
            FROM resource_history h
            LEFT JOIN servers s ON s.id = h.server_id
            LEFT JOIN workloads w ON w.id = h.workload_id
            WHERE h.server_id = ? ORDER BY h.timestamp DESC, h.id DESC
            """,
            (server["id"],),
        ).fetchall()
        return jsonify([dict(row) for row in rows])


@app.get("/api/events")
def events():
    with get_connection(app.config["DATABASE"]) as connection:
        rows = connection.execute(
            """
            SELECT e.*, s.server_id, w.workload_id
            FROM events e
            LEFT JOIN servers s ON s.id = e.server_id
            LEFT JOIN workloads w ON w.id = e.workload_id
            ORDER BY e.timestamp DESC, e.id DESC LIMIT 100
            """
        ).fetchall()
        return jsonify([dict(row) for row in rows])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
