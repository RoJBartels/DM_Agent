// The stage: renders the typed event stream. Unknown event types are ignored,
// so the server can grow the vocabulary (sfx, ambience, map_update…) without
// breaking this client.

const log = document.getElementById("log");
const form = document.getElementById("form");
const input = document.getElementById("input");
const send = document.getElementById("send");
const status = document.getElementById("status");
const actorSelect = document.getElementById("actor");

let ws = null;
let dmBlock = null; // current streaming narration <p>
let party = []; // characters for the "who is acting" selector (M2e)

function scroll() {
  log.scrollTop = log.scrollHeight;
}

function addBlock(cls, text) {
  const p = document.createElement("p");
  p.className = cls;
  p.textContent = text;
  log.appendChild(p);
  scroll();
  return p;
}

function setBusy(busy) {
  input.disabled = busy;
  send.disabled = busy;
  if (actorSelect) actorSelect.disabled = busy;
  if (!busy) input.focus();
}

// Shared visual builders so live events and history replay render identically.
function renderDice(ev) {
  const chip = document.createElement("span");
  chip.className = "chip";
  const purpose = ev.purpose ? `${ev.purpose}: ` : "";
  chip.textContent = `🎲 ${purpose}${ev.expression} → ${ev.total} [${(ev.rolls || []).join(", ")}]`;
  const holder = document.createElement("p");
  holder.className = "notice";
  holder.appendChild(chip);
  log.appendChild(holder);
  dmBlock = null; // next narration starts a fresh paragraph after the chip
  scroll();
}

function renderState(ev) {
  const changes = Object.entries(ev.changes || {})
    .map(([k, v]) => `${k} → ${JSON.stringify(v)}`)
    .join(", ");
  addBlock("notice", `⚙ ${ev.entity}: ${changes}`);
  dmBlock = null;
}

const handlers = {
  turn_start() {
    dmBlock = null;
  },
  narration_delta(ev) {
    if (!dmBlock) dmBlock = addBlock("dm", "");
    dmBlock.textContent += ev.text;
    scroll();
  },
  dice_roll: renderDice,
  state_update: renderState,
  turn_end() {
    dmBlock = null;
    setBusy(false);
  },
  error(ev) {
    addBlock("notice error", `⚠ ${ev.message}`);
    setBusy(false);
  },
};

function clearLog() {
  log.innerHTML = "";
  dmBlock = null;
}

// Replay the prior transcript (M2d) so a reconnecting player sees where they
// left off. Best-effort: a fetch failure just leaves the log empty, never blocks
// connecting. Display-only — it never sends anything to the server.
async function replayTranscript(sessionId) {
  let items;
  try {
    const res = await fetch(`/api/sessions/${sessionId}/transcript`);
    if (!res.ok) return;
    items = await res.json();
  } catch (_) {
    return;
  }
  if (!Array.isArray(items) || items.length === 0) return;
  for (const it of items) {
    if (it.kind === "player") addBlock("player", it.text);
    else if (it.kind === "narration") addBlock("dm", it.text);
    else if (it.kind === "dice") renderDice(it);
    else if (it.kind === "state") renderState(it);
    else if (it.kind === "error") addBlock("notice error", `⚠ ${it.message}`);
  }
  addBlock("notice", "— resumed —");
  dmBlock = null;
}

// The "who is acting" selector (M2e): populate from the party, PCs first (the
// server already orders them that way). Hidden when there are fewer than two
// characters — single-PC play needs no chooser.
function setParty(chars) {
  party = Array.isArray(chars) ? chars : [];
  if (!actorSelect) return;
  if (party.length < 2) {
    actorSelect.hidden = true;
    actorSelect.innerHTML = "";
    return;
  }
  const prev = actorSelect.value;
  actorSelect.innerHTML = party
    .map((c) => {
      const tag = c.is_pc ? "" : " (NPC)";
      const name = String(c.name ?? "");
      return `<option value="${name}">${name}${tag}</option>`;
    })
    .join("");
  if (party.some((c) => c.name === prev)) actorSelect.value = prev;
  actorSelect.hidden = false;
}

// Connect (or switch) to a session. Closing an existing socket first, without
// letting its onclose flip the UI into the "disconnected" state — a deliberate
// switch is not a dropped connection.
async function connect(sessionId) {
  if (ws) {
    ws.onclose = null;
    ws.close();
  }
  clearLog();
  status.textContent = "loading…";
  await replayTranscript(sessionId);
  status.textContent = "connecting…";
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/session/${sessionId}`);
  ws.onopen = () => {
    status.textContent = "connected";
    setBusy(false);
  };
  ws.onclose = () => {
    status.textContent = "disconnected — reload to reconnect";
    setBusy(true);
  };
  ws.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    const handler = handlers[ev.type];
    if (handler) handler(ev);
  };
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const raw = input.value.trim();
  if (!raw || !ws || ws.readyState !== WebSocket.OPEN) return;
  // With a multi-character party, prefix the acting character so the narrator
  // resolves the right sheet and voice (M2e). Single-PC play sends raw text.
  const actor = actorSelect && !actorSelect.hidden ? actorSelect.value : "";
  const text = actor ? `As ${actor}: ${raw}` : raw;
  addBlock("player", text);
  ws.send(JSON.stringify({ type: "player_action", text }));
  input.value = "";
  setBusy(true);
});

// The management layer (manage.js) decides which session to connect to and
// keeps the party selector in sync.
window.Stage = { connect, clearLog, setParty };
