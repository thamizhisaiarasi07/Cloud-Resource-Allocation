# Cloud-Based Resource Allocation Simulator

## 1. Project Title

**Cloud-Based Resource Allocation Simulator**

## 2. Introduction

This medium-level college project demonstrates how a cloud control plane can place incoming workloads onto a pool of compute servers. A Flask application, SQLite database, and interactive web dashboard simulate CPU, RAM, storage, network capacity, resource usage, failures, alerts, and workload lifecycle operations.

This is a **local simulation of cloud resource allocation**. It does not connect to real cloud infrastructure or any cloud provider.

## 3. Problem Statement

Cloud platforms must match changing application demand to finite compute resources. Poor placement can waste capacity, overload servers, or reject workloads. This project makes the placement decision and its resource impact visible and repeatable in a small local environment.

## 4. Objectives

- Model a regional pool of cloud-like servers and their finite capacity.
- Accept and validate workloads with four resource requirements and a priority.
- Place workloads using an understandable best-fit strategy.
- Monitor utilization, record history, and notify users about failures and thresholds.
- Demonstrate completion, failure, recovery, and priority reallocation.

## 5. Proposed Solution

The browser dashboard calls a local Flask JSON API. The API invokes the manager modules, which update SQLite in transactions. The UI polls the API every 2.5 seconds and redraws dashboard metrics, server cards, alerts, history, and Chart.js visualizations without reloading the page.

## 6. Cloud Computing Concepts

- **Resource pooling:** five simulated servers form a shared resource pool.
- **Provisioning:** a workload request reserves capacity on one suitable server.
- **Multi-dimensional capacity:** CPU, RAM, storage, and network are checked together.
- **Monitoring and elasticity:** capacity use, thresholds, reallocation, completion, and recovery are observable.
- **Fault simulation:** servers can be made unavailable and brought back online.

## 7. Resource Allocation Concept

Each server tracks capacity and allocated amounts for CPU units, RAM (GB), storage (GB), and network (Mbps). A workload is placed only when all four available amounts meet or exceed its requirements. Allocation and release operations update the server, workload, allocation record, resource history, and event log together.

## 8. Best-Fit Allocation

The allocator filters out offline servers and servers that cannot satisfy every requirement. For each remaining candidate it calculates the sum of the four leftover-capacity ratios:

`score = Σ((available resource - required resource) / server resource capacity)`

The server with the smallest score is selected; server ID is the stable tie-breaker. This favors a capable server with less normalized capacity left over, while never ignoring a resource dimension. The formula and decision are intentionally simple enough to explain in a classroom demonstration.

## 9. Architecture

```text
Browser (HTML / CSS / JavaScript / Chart.js)
                     │ JSON over localhost
                     ▼
              Flask application
              ├── workload_manager.py
              ├── resource_allocator.py
              ├── server_manager.py
              └── alert_manager.py
                     │ parameterized SQLite statements
                     ▼
           resource_allocation.db
```

## 10. Technology Stack

- Python 3.10 or later
- Flask
- SQLite (Python standard library)
- HTML, CSS, and vanilla JavaScript
- Chart.js via CDN

Only Flask is an external Python dependency. Chart.js is loaded from its public CDN by the browser; charts require an internet connection unless the script is later hosted locally.

## 11. Features

- Database-backed overview and per-server resource cards with progress bars.
- Best-fit allocation at workload creation and manual allocation for waiting/rejected work.
- Priority-driven reallocation of lower-priority workloads for HIGH or CRITICAL requests.
- Workload completion and resource release.
- Search and status filters for servers and workloads.
- Server creation, safe removal, failure simulation, and recovery.
- Active warning/critical alerts and manual alert resolution.
- Resource allocation history, event audit trail, and five live charts.
- Responsive dashboard with no full-page reload during polling.

## 12. Server Simulation

The first database initialization creates the following servers only when the server table is empty:

| Server | Region | CPU | RAM | Storage | Network |
|---|---|---:|---:|---:|---:|
| cloud-server-01 | Chennai | 16 | 32 GB | 500 GB | 1000 Mbps |
| cloud-server-02 | Mumbai | 8 | 16 GB | 250 GB | 500 Mbps |
| cloud-server-03 | Bangalore | 16 | 32 GB | 1000 GB | 1000 Mbps |
| cloud-server-04 | Delhi | 8 | 16 GB | 500 GB | 500 Mbps |
| cloud-server-05 | Hyderabad | 32 | 64 GB | 2000 GB | 2000 Mbps |

Statuses are IDLE at up to 10% peak per-resource utilization, ACTIVE above 10% through 90%, OVERLOADED when any dimension is above 90%, and OFFLINE after simulated failure.

## 13. Workload Management

Workloads receive a generated `WL-...` ID and UTC timestamp. LOW, MEDIUM, HIGH, and CRITICAL priorities are supported. A workload that cannot be allocated is retained with REJECTED status for LOW/MEDIUM priority or PENDING status for HIGH/CRITICAL priority; an allocation-failure alert is always created, and a waiting alert is also created for high-priority workloads. Workloads can be retried after capacity changes. Allocated workloads can be completed or moved. Active workloads must be completed or reallocated before deletion.

## 14. Resource Allocation Process

1. Validate the workload name, priority, and positive resource requirements.
2. Find online servers that have enough capacity in all four dimensions.
3. Score candidates with the best-fit formula and choose the lowest score.
4. Atomically record the allocation and update server availability.
5. Refresh utilization, server status, threshold alerts, history, and events.
6. If no placement is possible, record a REJECTED workload and an alert instead of silently failing.

## 15. Reallocation Process

For a HIGH or CRITICAL workload that does not fit, the allocator considers allocated LOW workloads and then MEDIUM workloads, oldest first. It releases lower-priority reservations one at a time and retries placement. Once the requested workload fits, the displaced jobs are placed again where possible; any that cannot fit become PENDING and receive a waiting alert. If the requested workload cannot be placed, the SQLite savepoint rolls back the attempted moves so existing assignments remain unchanged. Manual reallocation moves a workload to another online server and preserves its existing assignment when no destination is available.

## 16. Alert System

Alerts are persisted in SQLite, duplicate active alerts of the same type and subject are suppressed, and users can resolve active alerts in the UI. A resource dimension above 80% triggers a WARNING; above 90% triggers a CRITICAL alert and OVERLOADED status. Allocation failures and server failures create alerts. Failure alerts state that allocations are preserved: they remain reserved while the server is offline and become usable again when recovered.

## 17. Database Design

`database.py` creates the tables and indexes on startup and seeds the initial fleet once:

- `servers`: capacity, allocated resources, status, and timestamps.
- `workloads`: requirements, priority, lifecycle status, and assigned server.
- `allocations`: active and released reservation records.
- `alerts`: severity, type, message, status, and resolution timestamps.
- `resource_history`: resource change values and event type.
- `events`: user-visible audit events.

Foreign keys and parameterized queries are enabled. Removed server references in historical records become NULL rather than deleting the history.

## 18. API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Dashboard page |
| GET | `/api/dashboard` | Database-backed aggregate metrics and chart data |
| GET | `/api/servers` | List/search/filter servers |
| GET | `/api/servers/<server_id>` | Server detail |
| POST | `/api/servers` | Add server |
| DELETE | `/api/servers/<server_id>` | Remove server without active workloads |
| POST | `/api/servers/<server_id>/fail` | Simulate server failure |
| POST | `/api/servers/<server_id>/recover` | Recover server |
| GET | `/api/workloads` | List/search/filter workloads |
| GET | `/api/workloads/<workload_id>` | Workload detail |
| POST | `/api/workloads` | Create and immediately attempt allocation |
| POST | `/api/workloads/<workload_id>/allocate` | Retry allocation |
| POST | `/api/workloads/<workload_id>/complete` | Complete and release resources |
| POST | `/api/workloads/<workload_id>/reallocate` | Move or retry a workload |
| DELETE | `/api/workloads/<workload_id>` | Delete a non-allocated workload |
| GET | `/api/alerts` | List active and resolved alerts |
| POST | `/api/alerts/<alert_id>/resolve` | Resolve active alert |
| GET | `/api/history` | Latest resource history |
| GET | `/api/history/<server_id>` | History for a server |
| GET | `/api/events` | Latest audit events |

Successful requests return JSON. Invalid values return a clear 400 response, missing resources return 404, and unhandled server errors return a generic 500 response without a traceback.

## 19. Project Structure

```text
Cloud-Resource-Allocation/
├── app.py
├── database.py
├── resource_allocator.py
├── workload_manager.py
├── server_manager.py
├── alert_manager.py
├── requirements.txt
├── README.md
├── .gitignore
├── templates/
│   └── index.html
├── static/
│   ├── css/style.css
│   └── js/app.js
└── tests/
    └── test_app.py
```

`resource_allocation.db` is created at runtime and excluded from version control.

## 20. Installation

Install Python 3.10+ and open this folder in VS Code. Create and activate a virtual environment and install the one Python requirement.

## 21. Windows PowerShell Commands

From the project folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run the virtual environment interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 22. How to Run

```powershell
python app.py
```

Open **http://127.0.0.1:5000**. The app binds to localhost only; stop it with Ctrl+C. The SQLite database and five initial servers are initialized automatically. If port 5000 is already in use, set an alternate port before starting, for example `$env:PORT=5050; python app.py`.

## 23. Testing

Run the automated API and workflow tests:

```powershell
python -m unittest discover -s tests -v
```

Tests use a temporary SQLite database and cover seed data, allocation, rejection/waiting alerts, completion, failure/recovery, manual reallocation, server add/remove, utilization alerts, input validation, filters, and event/history endpoints.

## 24. Demonstration Procedure

1. Open the dashboard and point out the five regional servers and total capacity.
2. Create the example HIGH workload (4 CPU, 8 GB RAM, 100 GB storage, 200 Mbps).
3. Show the selected server and updated resource bars and charts.
4. Add a few more workloads and observe resource utilization and alerts.
5. Submit a workload larger than the entire fleet and show its rejection and alert.
6. Complete an allocated workload and observe resources being released.
7. Simulate failure on a server, inspect its OFFLINE status and CRITICAL alert, then recover it.
8. Reallocate a workload and use the server/workload search and status filters.
9. Review resource charts and the event history.
10. Add a small server, then remove it after confirming it has no active workloads.

## 25. Expected Output

The page displays live resource totals, utilization, server status and progress bars, workload status and actions, alerts, five charts, and recent events. An allocation changes the values sourced from SQLite; failed allocations remain visible with a reason and alert.

## 26. Advantages

- Runs locally with no paid cloud account, credentials, Docker, or Kubernetes.
- Makes scheduling decisions and resource use visible for a classroom presentation.
- Uses transactions to keep allocation changes consistent.
- Separates routes, database setup, allocation, and manager responsibilities.

## 27. Limitations

- The servers and regions are simulated; this application provisions no real infrastructure.
- Chart.js is loaded from a CDN, so chart rendering needs internet access.
- The simple best-fit and priority rules are educational and not production schedulers.
- There is no authentication; keep the development server bound to localhost.
- Failure preserves reservations rather than migrating jobs automatically.

## 28. Future Enhancements

- Add configurable scheduling policies and a simulation timeline.
- Persist utilization snapshots for historical time-series charts.
- Add workload queues, retries, and user-defined server groups.
- Host chart assets locally and add accessibility/usability testing.
- Add authentication and role-based controls before any network exposure.

## Security and Privacy

API inputs are validated, resource values must be positive and bounded, SQL values use parameters, and user-supplied text is escaped in the dashboard. The project does not store passwords, credentials, or API keys.
