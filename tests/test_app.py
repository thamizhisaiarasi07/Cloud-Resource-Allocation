import os
import tempfile
import unittest

from app import app
from database import initialize_database


class SimulatorApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.temp_dir.name, "test.db")
        initialize_database(self.database_path)
        app.config.update(TESTING=True, DATABASE=self.database_path)
        self.client = app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_workload(self, **overrides):
        payload = {
            "name": "Demo processing job",
            "cpu_required": 4,
            "ram_required": 8,
            "storage_required": 100,
            "network_required": 200,
            "priority": "HIGH",
        }
        payload.update(overrides)
        return self.client.post("/api/workloads", json=payload)

    def test_dashboard_seeds_five_servers_and_real_capacity(self):
        initialize_database(self.database_path)
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["servers"], 5)
        self.assertEqual(data["resources"]["cpu"]["capacity"], 80)
        self.assertEqual(data["resources"]["ram"]["capacity"], 160)
        self.assertEqual(len(data["servers_data"]), 5)

    def test_workload_allocation_completion_and_history(self):
        response = self.create_workload()
        self.assertEqual(response.status_code, 201)
        workload = response.get_json()["workload"]
        self.assertEqual(workload["status"], "ALLOCATED")
        self.assertEqual(workload["assigned_server"], "cloud-server-02")
        before = self.client.get("/api/dashboard").get_json()["resources"]["cpu"]["allocated"]
        self.assertEqual(before, 4)

        completed = self.client.post(f"/api/workloads/{workload['workload_id']}/complete")
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(
            self.client.get("/api/dashboard").get_json()["resources"]["cpu"]["allocated"],
            0,
        )
        self.assertGreaterEqual(len(self.client.get("/api/history").get_json()), 2)

    def test_insufficient_resources_creates_rejected_workload_and_alert(self):
        response = self.create_workload(
            name="Too large",
            cpu_required=1000,
            ram_required=1000,
            storage_required=1000,
            network_required=1000,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["workload"]["status"], "PENDING")
        alerts = self.client.get("/api/alerts?status=ACTIVE").get_json()
        self.assertTrue(any(alert["alert_type"] == "ALLOCATION_FAILED" for alert in alerts))
        self.assertTrue(any(alert["alert_type"] == "HIGH_PRIORITY_WAITING" for alert in alerts))

    def test_server_failure_and_recovery(self):
        workload = self.create_workload().get_json()["workload"]
        assigned_server = workload["assigned_server"]
        failure = self.client.post(f"/api/servers/{assigned_server}/fail")
        self.assertEqual(failure.status_code, 200)
        self.assertEqual(self.client.get(f"/api/servers/{assigned_server}").get_json()["status"], "OFFLINE")
        self.assertEqual(
            self.client.get("/api/dashboard").get_json()["resources"]["cpu"]["allocated"],
            4,
        )
        alerts = self.client.get("/api/alerts?status=ACTIVE").get_json()
        self.assertTrue(any(alert["alert_type"] == "SERVER_FAILURE" for alert in alerts))
        recovered = self.client.post(f"/api/servers/{assigned_server}/recover")
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(self.client.get(f"/api/servers/{assigned_server}").get_json()["status"], "ACTIVE")

    def test_server_create_delete_and_search(self):
        created = self.client.post(
            "/api/servers",
            json={
                "name": "Test node",
                "region": "Delhi",
                "cpu_capacity": 4,
                "ram_capacity": 8,
                "storage_capacity": 100,
                "network_capacity": 250,
            },
        )
        self.assertEqual(created.status_code, 201)
        server_id = created.get_json()["server"]["server_id"]
        search = self.client.get("/api/servers?search=test%20node")
        self.assertEqual(len(search.get_json()), 1)
        removed = self.client.delete(f"/api/servers/{server_id}")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get(f"/api/servers/{server_id}").status_code, 404)

    def test_priority_reallocation_moves_lower_priority_workload(self):
        low = self.create_workload(
            name="Background batch",
            cpu_required=1,
            ram_required=1,
            storage_required=20,
            network_required=20,
            priority="LOW",
        ).get_json()["workload"]
        old_server = low["assigned_server"]
        moved = self.client.post(f"/api/workloads/{low['workload_id']}/reallocate")
        self.assertEqual(moved.status_code, 200)
        self.assertNotEqual(moved.get_json()["server_id"], old_server)

    def test_high_priority_allocation_reclaims_lower_priority_capacity(self):
        low = self.create_workload(
            name="Low priority full-fleet blocker",
            cpu_required=32,
            ram_required=64,
            storage_required=2000,
            network_required=2000,
            priority="LOW",
        ).get_json()["workload"]
        self.assertEqual(low["assigned_server"], "cloud-server-05")
        high_response = self.create_workload(
            name="Critical capacity request",
            cpu_required=17,
            ram_required=33,
            storage_required=1001,
            network_required=1001,
            priority="HIGH",
        )
        high = high_response.get_json()["workload"]
        self.assertEqual(high["status"], "ALLOCATED")
        self.assertEqual(high["assigned_server"], "cloud-server-05")
        low_after = self.client.get(f"/api/workloads/{low['workload_id']}").get_json()
        self.assertEqual(low_after["status"], "PENDING")
        alerts = self.client.get("/api/alerts?status=ACTIVE").get_json()
        self.assertTrue(any(alert["alert_type"] == "WORKLOAD_WAITING" for alert in alerts))

    def test_input_validation_and_filter(self):
        bad = self.create_workload(cpu_required=-1)
        self.assertEqual(bad.status_code, 400)
        self.create_workload()
        allocated = self.client.get("/api/workloads?status=ALLOCATED").get_json()
        self.assertEqual(len(allocated), 1)
        self.assertEqual(self.client.get("/api/events").status_code, 200)

    def test_critical_utilization_alert_and_resolution(self):
        created = self.create_workload(
            cpu_required=15,
            ram_required=30,
            storage_required=100,
            network_required=100,
        )
        self.assertEqual(created.get_json()["workload"]["status"], "ALLOCATED")
        alert = next(
            item
            for item in self.client.get("/api/alerts?status=ACTIVE").get_json()
            if item["alert_type"] == "SERVER_OVERLOADED"
        )
        self.assertEqual(alert["severity"], "CRITICAL")
        self.assertEqual(self.client.post(f"/api/alerts/{alert['id']}/resolve").status_code, 200)

    def test_non_json_and_non_finite_values_are_rejected(self):
        response = self.client.post("/api/workloads", data="{invalid")
        self.assertEqual(response.status_code, 400)
        response = self.create_workload(cpu_required=float("nan"))
        self.assertEqual(response.status_code, 400)

    def test_waiting_high_priority_workload_can_retry_after_capacity_is_added(self):
        created = self.create_workload(
            name="Waiting high priority job",
            cpu_required=100,
            ram_required=100,
            storage_required=100,
            network_required=100,
        ).get_json()["workload"]
        self.assertEqual(created["status"], "PENDING")
        self.client.post(
            "/api/servers",
            json={
                "name": "Rescue server",
                "region": "Chennai",
                "cpu_capacity": 128,
                "ram_capacity": 128,
                "storage_capacity": 256,
                "network_capacity": 256,
            },
        )
        allocated = self.client.post(f"/api/workloads/{created['workload_id']}/allocate")
        self.assertEqual(allocated.status_code, 200)
        active_alerts = self.client.get("/api/alerts?status=ACTIVE").get_json()
        self.assertFalse(
            any(
                alert["workload_id"] == created["workload_id"]
                and alert["alert_type"] in ("ALLOCATION_FAILED", "HIGH_PRIORITY_WAITING")
                for alert in active_alerts
            )
        )


if __name__ == "__main__":
    unittest.main()
