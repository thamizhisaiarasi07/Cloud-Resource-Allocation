"""Alert creation, resolution, and resource-threshold monitoring."""

from database import utc_now


def create_alert(connection, severity, alert_type, message, server_id=None, workload_id=None):
    existing = connection.execute(
        """
        SELECT id FROM alerts
        WHERE status = 'ACTIVE' AND alert_type = ?
          AND server_id IS ? AND workload_id IS ?
        LIMIT 1
        """,
        (alert_type, server_id, workload_id),
    ).fetchone()
    if existing:
        return existing["id"]
    cursor = connection.execute(
        """
        INSERT INTO alerts
            (server_id, workload_id, severity, alert_type, message, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?)
        """,
        (server_id, workload_id, severity, alert_type, message, utc_now()),
    )
    return cursor.lastrowid


def resolve_alert(connection, alert_id):
    cursor = connection.execute(
        "UPDATE alerts SET status = 'RESOLVED', resolved_at = ? WHERE id = ? AND status = 'ACTIVE'",
        (utc_now(), alert_id),
    )
    return cursor.rowcount > 0


def check_resource_thresholds(connection, server):
    dimensions = (
        ("CPU", "cpu_allocated", "cpu_capacity"),
        ("RAM", "ram_allocated", "ram_capacity"),
        ("Storage", "storage_allocated", "storage_capacity"),
        ("Network", "network_allocated", "network_capacity"),
    )
    ratios = [
        (label, server[allocated] / server[capacity])
        for label, allocated, capacity in dimensions
    ]
    critical = [label for label, ratio in ratios if ratio > 0.90]
    warning = [label for label, ratio in ratios if 0.80 < ratio <= 0.90]

    if critical:
        create_alert(
            connection,
            "CRITICAL",
            "RESOURCE_THRESHOLD",
            f"Critical utilization above 90% for {', '.join(critical)} on {server['server_id']}.",
            server["id"],
        )
        connection.execute(
            """
            UPDATE alerts SET status = 'RESOLVED', resolved_at = ?
            WHERE server_id = ? AND alert_type = 'RESOURCE_WARNING' AND status = 'ACTIVE'
            """,
            (utc_now(), server["id"]),
        )
    elif warning:
        connection.execute(
            """
            UPDATE alerts SET status = 'RESOLVED', resolved_at = ?
            WHERE server_id = ? AND alert_type = 'RESOURCE_THRESHOLD' AND status = 'ACTIVE'
            """,
            (utc_now(), server["id"]),
        )
        create_alert(
            connection,
            "WARNING",
            "RESOURCE_WARNING",
            f"Utilization above 80% for {', '.join(warning)} on {server['server_id']}.",
            server["id"],
        )
    else:
        connection.execute(
            """
            UPDATE alerts SET status = 'RESOLVED', resolved_at = ?
            WHERE server_id = ? AND alert_type IN ('RESOURCE_WARNING', 'RESOURCE_THRESHOLD')
              AND status = 'ACTIVE'
            """,
            (utc_now(), server["id"]),
        )
