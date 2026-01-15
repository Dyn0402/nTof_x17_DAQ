async function fetchStatus() {
  const res = await fetch("/api/status");
  const data = await res.json();
  updateStatus(data.running);
}

function updateStatus(running) {
  const badge = document.getElementById("status-badge");
  if (running) {
    badge.textContent = "Running";
    badge.className = "badge bg-success";
  } else {
    badge.textContent = "Stopped";
    badge.className = "badge bg-danger";
  }
}

async function sendCommand(endpoint) {
  const res = await fetch(endpoint, { method: "POST" });
  const data = await res.json();
  document.getElementById("message").textContent = data.message;
  fetchStatus();
}

document.getElementById("start-btn").onclick = () => sendCommand("/api/start");
document.getElementById("stop-btn").onclick = () => sendCommand("/api/stop");

// Auto-refresh every 2 seconds
setInterval(fetchStatus, 2000);
fetchStatus();
