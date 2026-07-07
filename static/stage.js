// The stage: renders the typed event stream. Unknown event types are ignored,
// so the server can grow the vocabulary (sfx, ambience, map_update…) without
// breaking this client.

const log = document.getElementById("log");
const form = document.getElementById("form");
const input = document.getElementById("input");
const send = document.getElementById("send");
const status = document.getElementById("status");

let ws = null;
let dmBlock = null; // current streaming narration <p>

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
  if (!busy) input.focus();
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
  dice_roll(ev) {
    const chip = document.createElement("span");
    chip.className = "chip";
    const purpose = ev.purpose ? `${ev.purpose}: ` : "";
    chip.textContent = `🎲 ${purpose}${ev.expression} → ${ev.total} [${ev.rolls.join(", ")}]`;
    const holder = document.createElement("p");
    holder.className = "notice";
    holder.appendChild(chip);
    log.appendChild(holder);
    dmBlock = null; // next narration starts a fresh paragraph after the chip
    scroll();
  },
  state_update(ev) {
    const changes = Object.entries(ev.changes)
      .map(([k, v]) => `${k} → ${JSON.stringify(v)}`)
      .join(", ");
    addBlock("notice", `⚙ ${ev.entity}: ${changes}`);
    dmBlock = null;
  },
  turn_end() {
    dmBlock = null;
    setBusy(false);
  },
  error(ev) {
    addBlock("notice error", `⚠ ${ev.message}`);
    setBusy(false);
  },
};

function connect(sessionId) {
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
  const text = input.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  addBlock("player", text);
  ws.send(JSON.stringify({ type: "player_action", text }));
  input.value = "";
  setBusy(true);
});

(async function init() {
  const res = await fetch("/api/demo-session", { method: "POST" });
  const { session_id } = await res.json();
  status.textContent = `session ${session_id.slice(0, 8)}…`;
  connect(session_id);
})();
