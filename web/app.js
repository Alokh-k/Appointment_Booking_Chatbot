const sessionId = getOrCreateSessionId();
const messages = document.querySelector("#messages");
const result = document.querySelector("#result");
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const session = document.querySelector("#sessionId");
const services = document.querySelector("#services");
const doctors = document.querySelector("#doctors");
const clinicName = document.querySelector("#clinicName");

session.textContent = sessionId;

document.querySelectorAll("[data-example]").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.dataset.example;
    input.focus();
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;

  appendMessage("user", message);
  input.value = "";
  setBusy(true);
  hideResult();

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Request failed");
    }
    appendMessage("assistant", payload.message);
    showStructuredResult(payload);
  } catch (error) {
    appendMessage("error", error.message);
  } finally {
    setBusy(false);
  }
});

loadConfig();
appendMessage("assistant", "Send a booking, reschedule, cancellation, or availability request.");

async function loadConfig() {
  try {
    const response = await fetch("/config");
    const config = await response.json();
    clinicName.textContent = config.clinic.name;
    services.innerHTML = config.services.map(renderService).join("");
    doctors.innerHTML = config.providers.map(renderDoctor).join("");
  } catch (error) {
    services.innerHTML = renderInfo("Configuration unavailable", error.message);
  }
}

function renderService(service) {
  return renderInfo(service.name, `${service.duration_minutes} minutes`);
}

function renderDoctor(doctor) {
  return renderInfo(doctor.name, `Services: ${doctor.service_ids.join(", ")}`);
}

function renderInfo(title, detail) {
  return `<div class="infoItem"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div>`;
}

function appendMessage(type, text) {
  const node = document.createElement("div");
  node.className = `message ${type}`;
  node.textContent = text;
  messages.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
}

function showStructuredResult(payload) {
  const structured = payload.appointment || payload.data || null;
  if (!structured) {
    hideResult();
    return;
  }
  result.hidden = false;
  result.textContent = JSON.stringify(structured, null, 2);
}

function hideResult() {
  result.hidden = true;
  result.textContent = "";
}

function setBusy(isBusy) {
  const button = form.querySelector("button");
  button.disabled = isBusy;
  button.querySelector("span").textContent = isBusy ? "Sending" : "Send";
}

function getOrCreateSessionId() {
  const key = "appointment_chat_session_id";
  const existing = window.localStorage.getItem(key);
  if (existing) return existing;
  const created = `session-${crypto.randomUUID()}`;
  window.localStorage.setItem(key, created);
  return created;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
