const charts = {};
let refreshInProgress = false;
let toastTimer;

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[char]);
const format = (value) => Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 1 });
const dateTime = (value) => value ? new Date(value).toLocaleString() : "—";

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
  return data;
}

function notify(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast visible${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.className = "toast"; }, 4200);
}

function updateMetric(resource, data, unit) {
  const titleCase = resource[0].toUpperCase() + resource.slice(1);
  $(`#metric-${resource}`).textContent = `${format(data.capacity)} ${unit}`;
  $(`#metric-${resource}-detail`).textContent = `${format(data.allocated)} ${unit} allocated · ${format(data.available)} available`;
  const label = document.querySelector(`[data-resource="${resource}"]`);
  if (label) label.textContent = titleCase;
}

function renderDashboard(data) {
  $("#metric-servers").textContent = data.servers;
  $("#metric-server-detail").textContent = `${data.active_servers} active · ${data.idle_servers} idle · ${data.offline_servers} offline`;
  updateMetric("cpu", data.resources.cpu, "units");
  updateMetric("ram", data.resources.ram, "GB");
  updateMetric("storage", data.resources.storage, "GB");
  updateMetric("network", data.resources.network, "Mbps");
  $("#metric-utilization").textContent = `${data.overall_utilization}%`;
  $("#overall-track").style.width = `${Math.min(data.overall_utilization, 100)}%`;
  $("#fleet-summary").innerHTML = `
    <span class="summary-pill good">${data.active_servers} active</span>
    <span class="summary-pill">${data.idle_servers} idle</span>
    <span class="summary-pill danger">${data.overloaded_servers} overloaded</span>
    <span class="summary-pill">${data.offline_servers} offline</span>`;
  $("#workload-summary").innerHTML = [
    ["Total workloads", data.workloads],
    ["Allocated", data.allocated_workloads],
    ["Pending", data.pending_workloads],
    ["Rejected", data.rejected_workloads],
    ["Completed", data.completed_workloads]
  ].map(([label, value]) => `<article class="summary-card"><span>${label}</span><strong>${value}</strong></article>`).join("");
  $("#alert-counts").innerHTML = `
    <span class="summary-pill">${data.alerts.active} active</span>
    <span class="summary-pill warning">${data.alerts.warning} warning</span>
    <span class="summary-pill danger">${data.alerts.critical} critical</span>
    <span class="summary-pill good">${data.alerts.resolved} resolved</span>`;
  renderCharts(data.servers_data);
}

function resourceBar(label, used, capacity, unit, ratio) {
  const percent = Math.min(Number(ratio || 0), 100);
  return `<div class="resource-row">
    <div class="resource-heading"><span>${label}</span><span>${format(used)} / ${format(capacity)} ${unit}</span></div>
    <div class="progress-track"><span class="${percent > 90 ? "critical" : percent > 80 ? "warning" : ""}" style="width:${percent}%"></span></div>
  </div>`;
}

function renderServers(servers) {
  const container = $("#server-list");
  if (!servers.length) {
    container.innerHTML = '<div class="empty-state">No servers match this search.</div>';
    return;
  }
  container.innerHTML = servers.map((server) => {
    const action = server.status === "OFFLINE"
      ? `<button class="button button-small button-primary" data-action="recover-server" data-id="${escapeHtml(server.server_id)}">Recover</button>`
      : `<button class="button button-small button-warning" data-action="fail-server" data-id="${escapeHtml(server.server_id)}">Simulate failure</button>`;
    return `<article class="server-card">
      <div class="server-card-head">
        <div><span class="server-symbol">▦</span><h3>${escapeHtml(server.name)}</h3><span class="server-id">${escapeHtml(server.server_id)}</span></div>
        <span class="badge ${server.status.toLowerCase()}">${server.status}</span>
      </div>
      <div class="region-line">⌖ ${escapeHtml(server.region)} <span>·</span> Utilization <strong>${server.utilization}%</strong></div>
      <div class="resource-bars">
        ${resourceBar("CPU", server.cpu_allocated, server.cpu_capacity, "units", server.resource_utilization.cpu)}
        ${resourceBar("RAM", server.ram_allocated, server.ram_capacity, "GB", server.resource_utilization.ram)}
        ${resourceBar("Storage", server.storage_allocated, server.storage_capacity, "GB", server.resource_utilization.storage)}
        ${resourceBar("Network", server.network_allocated, server.network_capacity, "Mbps", server.resource_utilization.network)}
      </div>
      <div class="server-card-foot">${action}<button class="button button-small button-quiet" data-action="delete-server" data-id="${escapeHtml(server.server_id)}">Remove</button></div>
    </article>`;
  }).join("");
}

function renderWorkloads(workloads) {
  $("#workload-count").textContent = `${workloads.length} shown`;
  const body = $("#workload-rows");
  if (!workloads.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-cell">No workloads found.</td></tr>';
    return;
  }
  body.innerHTML = workloads.map((workload) => {
    let actions = "";
    if (workload.status === "ALLOCATED") {
      actions = `<button class="table-action" data-action="complete-workload" data-id="${escapeHtml(workload.workload_id)}">Complete</button>
        <button class="table-action" data-action="reallocate-workload" data-id="${escapeHtml(workload.workload_id)}">Reallocate</button>`;
    } else if (workload.status === "PENDING" || workload.status === "REJECTED") {
      actions = `<button class="table-action" data-action="allocate-workload" data-id="${escapeHtml(workload.workload_id)}">Allocate</button>`;
    }
    if (workload.status !== "ALLOCATED") {
      actions += `<button class="table-action danger-text" data-action="delete-workload" data-id="${escapeHtml(workload.workload_id)}">Delete</button>`;
    }
    return `<tr>
      <td><strong>${escapeHtml(workload.name)}</strong><small>${escapeHtml(workload.workload_id)}</small></td>
      <td><span class="priority ${workload.priority.toLowerCase()}">${workload.priority}</span></td>
      <td class="requirements">${format(workload.cpu_required)} CPU · ${format(workload.ram_required)} GB RAM<br>${format(workload.storage_required)} GB storage · ${format(workload.network_required)} Mbps</td>
      <td>${escapeHtml(workload.assigned_server || "—")}</td>
      <td><span class="badge ${workload.status.toLowerCase()}">${workload.status}</span></td>
      <td>${dateTime(workload.created_at)}</td>
      <td><div class="action-group">${actions || "—"}</div></td>
    </tr>`;
  }).join("");
}

function renderAlerts(alerts) {
  const active = alerts.filter((alert) => alert.status === "ACTIVE");
  const visible = active.concat(alerts.filter((alert) => alert.status === "RESOLVED")).slice(0, 24);
  $("#alert-list").innerHTML = visible.length ? visible.map((alert) => `
    <article class="alert-item ${alert.severity.toLowerCase()} ${alert.status === "RESOLVED" ? "resolved" : ""}">
      <span class="alert-indicator">${alert.severity === "CRITICAL" ? "!" : alert.severity === "WARNING" ? "△" : "i"}</span>
      <div class="alert-copy"><div><strong>${escapeHtml(alert.alert_type.replaceAll("_", " "))}</strong>
        <span class="badge ${alert.severity.toLowerCase()}">${alert.severity}</span>
        <span class="badge ${alert.status.toLowerCase()}">${alert.status}</span></div>
        <p>${escapeHtml(alert.message)}</p>
        <small>${dateTime(alert.created_at)}${alert.server_id ? ` · ${escapeHtml(alert.server_id)}` : ""}${alert.workload_id ? ` · ${escapeHtml(alert.workload_id)}` : ""}</small>
      </div>
      ${alert.status === "ACTIVE" ? `<button class="button button-small button-quiet" data-action="resolve-alert" data-id="${alert.id}">Resolve</button>` : ""}
    </article>`).join("") : '<div class="empty-state">No alerts yet. Threshold and allocation events will appear here.</div>';
}

function renderCharts(servers) {
  if (!window.Chart) return;
  const labels = servers.map((server) => server.server_id);
  const definitions = [
    ["cpu", "CPU units", "cpu_allocated", "cpu_capacity", "#5276f5"],
    ["ram", "RAM (GB)", "ram_allocated", "ram_capacity", "#8b5cf6"],
    ["storage", "Storage (GB)", "storage_allocated", "storage_capacity", "#0ea5a4"],
    ["network", "Network (Mbps)", "network_allocated", "network_capacity", "#f59e0b"]
  ];
  for (const [key, label, allocatedKey, capacityKey, color] of definitions) {
    const id = `${key}-chart`;
    const values = servers.map((server) => server[allocatedKey]);
    const capacity = servers.map((server) => server[capacityKey]);
    if (charts[key]) {
      charts[key].data.labels = labels;
      charts[key].data.datasets[0].data = values;
      charts[key].data.datasets[1].data = capacity;
      charts[key].update("none");
    } else {
      charts[key] = new Chart(document.getElementById(id), {
        type: "bar",
        data: { labels, datasets: [
          { label: "Allocated", data: values, backgroundColor: color, borderRadius: 5 },
          { label: "Capacity", data: capacity, backgroundColor: "#e8edf5", borderRadius: 5 }
        ] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } }, scales: { y: { beginAtZero: true } } }
      });
    }
    charts[key].data.datasets[0].label = `Allocated ${label}`;
  }
  const utilizationValues = servers.map((server) => server.utilization);
  if (charts.utilization) {
    charts.utilization.data.labels = labels;
    charts.utilization.data.datasets[0].data = utilizationValues;
    charts.utilization.update("none");
  } else {
    charts.utilization = new Chart(document.getElementById("utilization-chart"), {
      type: "bar",
      data: { labels, datasets: [{ label: "Average utilization (%)", data: utilizationValues, backgroundColor: "#5276f5", borderRadius: 5 }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, max: 100 } } }
    });
  }
}

function renderEvents(events) {
  $("#event-rows").innerHTML = events.length ? events.map((event) => `<tr>
    <td>${dateTime(event.timestamp)}</td><td><span class="event-type">${escapeHtml(event.event_type.replaceAll("_", " "))}</span></td>
    <td>${escapeHtml(event.workload_id || "—")}</td><td>${escapeHtml(event.server_id || "—")}</td>
    <td>${escapeHtml(event.description)}</td></tr>`).join("") : '<tr><td colspan="5" class="empty-cell">No events have been recorded.</td></tr>';
}

async function refreshDashboard() {
  if (refreshInProgress) return;
  refreshInProgress = true;
  try {
    const [dashboard, servers, workloads, alerts, events] = await Promise.all([
      api("/api/dashboard"),
      api(`/api/servers?search=${encodeURIComponent($("#server-search").value)}&status=${$("#server-filter").value}`),
      api(`/api/workloads?search=${encodeURIComponent($("#workload-search").value)}&status=${$("#workload-filter").value}`),
      api("/api/alerts"),
      api("/api/events")
    ]);
    renderDashboard(dashboard);
    renderServers(servers);
    renderWorkloads(workloads);
    renderAlerts(alerts);
    renderEvents(events);
    $("#last-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    $("#last-updated").textContent = "Update failed";
    notify(error.message, true);
  } finally {
    refreshInProgress = false;
  }
}

function formValues(form, numericFields) {
  const values = Object.fromEntries(new FormData(form).entries());
  for (const field of numericFields) values[field] = Number(values[field]);
  return values;
}

$("#workload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const result = await api("/api/workloads", {
      method: "POST",
      body: JSON.stringify(formValues(form, ["cpu_required", "ram_required", "storage_required", "network_required"]))
    });
    const workload = result.workload;
    if (workload.status === "ALLOCATED") {
      notify(`Allocated ${workload.workload_id} to ${workload.assigned_server}.`);
    } else {
      notify(`Insufficient resources. ${workload.workload_id} was rejected and an alert was created.`, true);
    }
    form.reset();
    form.elements.cpu_required.value = 4;
    form.elements.ram_required.value = 8;
    form.elements.storage_required.value = 100;
    form.elements.network_required.value = 200;
    form.elements.priority.value = "HIGH";
    await refreshDashboard();
  } catch (error) { notify(error.message, true); }
});

$("#server-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const result = await api("/api/servers", {
      method: "POST",
      body: JSON.stringify(formValues(form, ["cpu_capacity", "ram_capacity", "storage_capacity", "network_capacity"]))
    });
    notify(`Added ${result.server.server_id}.`);
    form.reset();
    $("#server-form-panel").hidden = true;
    await refreshDashboard();
  } catch (error) { notify(error.message, true); }
});

$("#open-server-form").addEventListener("click", () => { $("#server-form-panel").hidden = false; });
$("#close-server-form").addEventListener("click", () => { $("#server-form-panel").hidden = true; });
for (const input of ["#server-search", "#server-filter", "#workload-search", "#workload-filter"]) {
  $(input).addEventListener("input", refreshDashboard);
  $(input).addEventListener("change", refreshDashboard);
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const { action, id } = button.dataset;
  const actions = {
    "fail-server": async () => {
      if (confirm(`Simulate failure for ${id}? Existing workloads will stay allocated on the offline server.`))
        return api(`/api/servers/${encodeURIComponent(id)}/fail`, { method: "POST" });
    },
    "recover-server": () => api(`/api/servers/${encodeURIComponent(id)}/recover`, { method: "POST" }),
    "delete-server": async () => {
      if (confirm(`Remove ${id}? Servers with active workloads cannot be removed.`))
        return api(`/api/servers/${encodeURIComponent(id)}`, { method: "DELETE" });
    },
    "allocate-workload": () => api(`/api/workloads/${encodeURIComponent(id)}/allocate`, { method: "POST" }),
    "complete-workload": () => api(`/api/workloads/${encodeURIComponent(id)}/complete`, { method: "POST" }),
    "reallocate-workload": () => api(`/api/workloads/${encodeURIComponent(id)}/reallocate`, { method: "POST" }),
    "delete-workload": async () => {
      if (confirm(`Delete workload ${id}?`))
        return api(`/api/workloads/${encodeURIComponent(id)}`, { method: "DELETE" });
    },
    "resolve-alert": () => api(`/api/alerts/${id}/resolve`, { method: "POST" })
  };
  try {
    const result = await actions[action]();
    if (result) notify(result.message || `Action ${action.replaceAll("-", " ")} completed.`);
    await refreshDashboard();
  } catch (error) { notify(error.message, true); }
});

refreshDashboard();
setInterval(refreshDashboard, 2500);
