/* AuraFS – frontend application logic */

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return (bytes / Math.pow(1024, i)).toFixed(2) + " " + units[i];
}

function formatDate(iso) {
  if (!iso) return "Never";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const text = await res.text();
    let msg;
    try { msg = JSON.parse(text).error || text; } catch { msg = text; }
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Section toggle ────────────────────────────────────────────────────────────

function toggleSection(name) {
  const body = document.getElementById("body-" + name);
  const icon = document.getElementById("toggle-" + name);
  const isHidden = body.classList.toggle("hidden");
  icon.classList.toggle("collapsed", isHidden);
}

// ── Disk Info ─────────────────────────────────────────────────────────────────

function fileRowId(path) {
  return "file-row-" + btoa(unescape(encodeURIComponent(path))).replace(/[^a-zA-Z0-9]/g, "");
}
  try {
    const data = await apiFetch("/api/disk-info");
    if (data.error) throw new Error(data.error);
    document.getElementById("target-folder").textContent = data.target_folder;
    document.getElementById("val-folder-size").textContent = formatBytes(data.folder_size);
    document.getElementById("val-disk-total").textContent = formatBytes(data.disk_total);
    document.getElementById("val-disk-free").textContent = formatBytes(data.disk_free);
    const usedPct = ((data.disk_used / data.disk_total) * 100).toFixed(1);
    document.getElementById("val-disk-used").textContent =
      formatBytes(data.disk_used) + " (" + usedPct + "%)";
    document.getElementById("disk-progress").style.width = usedPct + "%";
  } catch (e) {
    document.getElementById("val-folder-size").textContent = "Error";
    console.error("Disk info error:", e);
  }
}

// ── Smart Deletion ────────────────────────────────────────────────────────────

async function loadUnusedFiles() {
  const period = document.getElementById("deletion-period").value;
  const loading = document.getElementById("deletion-loading");
  const empty = document.getElementById("deletion-empty");
  const list = document.getElementById("deletion-list");

  loading.classList.remove("hidden");
  empty.classList.add("hidden");
  list.innerHTML = "";

  try {
    const files = await apiFetch("/api/unused-files?period=" + period);
    loading.classList.add("hidden");
    if (!Array.isArray(files) || files.length === 0) {
      empty.classList.remove("hidden");
      return;
    }
    files.forEach(f => {
      const row = document.createElement("div");
      row.className = "file-row";
      row.id = fileRowId(f.path);
      row.innerHTML = `
        <div class="file-info">
          <div class="file-name">${escHtml(f.name)}</div>
          <div class="file-meta">${escHtml(f.path)}</div>
          <div class="file-meta">Last used: ${formatDate(f.last_used)}</div>
        </div>
        <span class="file-size">${formatBytes(f.size)}</span>
        <button class="btn-delete" onclick="deleteFile(${JSON.stringify(f.path)}, this)">Delete</button>
      `;
      list.appendChild(row);
    });
  } catch (e) {
    loading.classList.add("hidden");
    list.innerHTML = `<div class="error-msg">Error: ${escHtml(e.message)}</div>`;
  }
}

async function deleteFile(path, btn) {
  if (!confirm("Delete file?\n" + path)) return;
  btn.disabled = true;
  try {
    const data = await apiFetch("/api/delete-file", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (data.error) throw new Error(data.error);
    const rowId = fileRowId(path);
    const row = document.getElementById(rowId);
    if (row) row.remove();
  } catch (e) {
    alert("Delete failed: " + e.message);
    btn.disabled = false;
  }
}

// ── Smart Structure ───────────────────────────────────────────────────────────

async function loadDirectoryTree() {
  const treeEl = document.getElementById("dir-tree");
  const loadingEl = document.getElementById("tree-loading");
  try {
    const tree = await apiFetch("/api/directory-tree");
    loadingEl.classList.add("hidden");
    if (tree.error) { treeEl.innerHTML = `<div class="error-msg">${escHtml(tree.error)}</div>`; return; }
    treeEl.innerHTML = "";
    treeEl.appendChild(buildTreeNode(tree));
  } catch (e) {
    loadingEl.classList.add("hidden");
    treeEl.innerHTML = `<div class="error-msg">Error: ${escHtml(e.message)}</div>`;
  }
}

function buildTreeNode(node) {
  const ul = document.createElement("ul");
  ul.className = "tree-node";
  const li = document.createElement("li");
  li.className = node.type === "directory" ? "tree-dir" : "tree-file";
  li.textContent = node.name;
  ul.appendChild(li);
  if (node.children && node.children.length > 0) {
    node.children.forEach(child => {
      const childUl = buildTreeNode(child);
      childUl.style.paddingLeft = "1rem";
      ul.appendChild(childUl);
    });
  }
  return ul;
}

let structurePollTimer = null;

async function triggerStructure() {
  await apiFetch("/api/trigger-structure", { method: "POST" });
  pollStructureStatus();
}

async function pollStructureStatus() {
  clearInterval(structurePollTimer);
  const spinner = document.getElementById("structure-spinner");
  const statusMsg = document.getElementById("structure-status-msg");
  spinner.classList.remove("hidden");
  statusMsg.textContent = "Generating recommendations…";

  structurePollTimer = setInterval(async () => {
    try {
      const data = await apiFetch("/api/structure-recommendations");
      if (data.status === "completed" || data.status === "error") {
        clearInterval(structurePollTimer);
        spinner.classList.add("hidden");
        if (data.status === "error") {
          statusMsg.textContent = "Error generating recommendations.";
        } else {
          statusMsg.textContent = data.recommendations && data.recommendations.length > 0
            ? data.recommendations.length + " recommendation(s) found."
            : "No recommendations.";
          renderStructureRecs(data.recommendations || []);
        }
      }
    } catch (e) {
      clearInterval(structurePollTimer);
      spinner.classList.add("hidden");
      statusMsg.textContent = "Poll error: " + e.message;
    }
  }, 1000);
}

function renderStructureRecs(recs) {
  const list = document.getElementById("structure-recs-list");
  list.innerHTML = "";
  if (recs.length === 0) return;
  recs.forEach((rec, i) => {
    const row = document.createElement("div");
    row.className = "rec-row";
    row.id = "rec-" + i;
    row.innerHTML = `
      <div class="rec-info">
        <div class="rec-display">${escHtml(rec.display)}</div>
        <div class="rec-reason">${escHtml(rec.reason || "")}</div>
      </div>
      <button class="btn-execute" onclick="executeRec(${i}, ${JSON.stringify(rec)}, this)">Execute</button>
    `;
    list.appendChild(row);
  });
}

async function executeRec(idx, rec, btn) {
  if (!confirm("Execute: " + rec.display)) return;
  btn.disabled = true;
  try {
    const data = await apiFetch("/api/execute-recommendation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(rec),
    });
    if (data.error) throw new Error(data.error);
    const row = document.getElementById("rec-" + idx);
    if (row) {
      row.style.opacity = "0.4";
      btn.textContent = "Done ✓";
    }
    // Reload tree
    loadDirectoryTree();
  } catch (e) {
    alert("Execute failed: " + e.message);
    btn.disabled = false;
  }
}

// ── Smart Connection ──────────────────────────────────────────────────────────

let connPollTimer = null;

async function triggerConnections() {
  const spinner = document.getElementById("conn-spinner");
  const statusMsg = document.getElementById("conn-status-msg");
  await apiFetch("/api/trigger-connections", { method: "POST" });
  spinner.classList.remove("hidden");
  statusMsg.textContent = "Analysing file connections…";

  clearInterval(connPollTimer);
  connPollTimer = setInterval(async () => {
    try {
      const data = await apiFetch("/api/connection-status");
      if (data.status === "completed" || data.status === "error") {
        clearInterval(connPollTimer);
        spinner.classList.add("hidden");
        if (data.status === "error") {
          statusMsg.textContent = "Error during analysis.";
        } else {
          statusMsg.textContent = "Analysis complete.";
          loadConnectionRecs();
        }
      }
    } catch (e) {
      clearInterval(connPollTimer);
      spinner.classList.add("hidden");
      statusMsg.textContent = "Poll error: " + e.message;
    }
  }, 1000);
}

async function loadConnectionRecs() {
  const list = document.getElementById("conn-recs-list");
  list.innerHTML = "";
  try {
    const recs = await apiFetch("/api/connection-recommendations");
    if (!Array.isArray(recs) || recs.length === 0) {
      list.innerHTML = "<div class='empty-msg'>No pending connection recommendations.</div>";
      return;
    }
    recs.forEach(rec => renderConnRec(rec, list));
  } catch (e) {
    list.innerHTML = `<div class="error-msg">Error: ${escHtml(e.message)}</div>`;
  }
}

function renderConnRec(rec, container) {
  const row = document.createElement("div");
  row.className = "rec-row";
  row.id = "conn-rec-" + rec.id;
  row.innerHTML = `
    <div class="rec-info">
      <div class="rec-display">
        ${escHtml(shortPath(rec.file1))}
        <span class="tag">${escHtml(rec.relation)}</span>
        ${escHtml(shortPath(rec.file2))}
      </div>
      <div class="rec-reason file-meta">${escHtml(rec.file1)}<br/>${escHtml(rec.file2)}</div>
    </div>
    <div class="btn-group">
      <button class="btn-accept" onclick="respondConn(${rec.id}, 'accepted', this)">Accept</button>
      <button class="btn-reject" onclick="respondConn(${rec.id}, 'rejected', this)">Reject</button>
    </div>
  `;
  container.appendChild(row);
}

async function respondConn(id, response, btn) {
  btn.disabled = true;
  try {
    const data = await apiFetch("/api/connection-response", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, response }),
    });
    if (data.error) throw new Error(data.error);
    const row = document.getElementById("conn-rec-" + id);
    if (row) {
      row.style.opacity = "0.4";
      row.querySelectorAll("button").forEach(b => b.disabled = true);
      const label = response === "accepted" ? "✓ Accepted" : "✕ Rejected";
      row.querySelector(".btn-group").innerHTML =
        `<span style="font-size:0.82rem;color:#a0aec0">${label}</span>`;
    }
  } catch (e) {
    alert("Error: " + e.message);
    btn.disabled = false;
  }
}

async function queryConnections() {
  const input = document.getElementById("conn-query");
  const resultsEl = document.getElementById("conn-query-results");
  const filename = input.value.trim();
  if (!filename) return;

  resultsEl.classList.remove("hidden");
  resultsEl.innerHTML = "Searching…";

  try {
    const data = await apiFetch("/api/connected-files?filename=" + encodeURIComponent(filename));
    if (data.error) { resultsEl.innerHTML = `<div class="error-msg">${escHtml(data.error)}</div>`; return; }
    if (!Array.isArray(data) || data.length === 0) {
      resultsEl.innerHTML = "No accepted connections found for <strong>" + escHtml(filename) + "</strong>.";
      return;
    }
    let html = `<strong>Connections for ${escHtml(filename)}:</strong>`;
    data.forEach(r => {
      html += `<div class="conn-result-row">${escHtml(r.file)} <span class="tag">${escHtml(r.relation)}</span></div>`;
    });
    resultsEl.innerHTML = html;
  } catch (e) {
    resultsEl.innerHTML = `<div class="error-msg">Error: ${escHtml(e.message)}</div>`;
  }
}

// ── Utility ───────────────────────────────────────────────────────────────────

function escHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function shortPath(p) {
  if (!p) return "";
  const parts = p.replace(/\\/g, "/").split("/");
  return parts.slice(-2).join("/");
}

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  await loadDiskInfo();
  loadUnusedFiles();
  loadDirectoryTree();

  // Check structure status: only poll if already running, otherwise wait for user action
  const structStatus = await apiFetch("/api/structure-status");
  if (structStatus.status === "running") {
    pollStructureStatus();
  } else if (structStatus.status === "completed") {
    const data = await apiFetch("/api/structure-recommendations");
    document.getElementById("structure-status-msg").textContent =
      data.recommendations && data.recommendations.length > 0
        ? data.recommendations.length + " recommendation(s) found."
        : "No recommendations. Click ↻ Analyse to generate.";
    renderStructureRecs(data.recommendations || []);
  } else {
    document.getElementById("structure-status-msg").textContent =
      "Click ↻ Analyse to generate structure recommendations.";
  }

  // Load existing connection recommendations
  loadConnectionRecs();

  // Enter key on connection query
  document.getElementById("conn-query").addEventListener("keydown", e => {
    if (e.key === "Enter") queryConnections();
  });
}

document.addEventListener("DOMContentLoaded", init);
