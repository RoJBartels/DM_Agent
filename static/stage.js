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
// NB: distinct from manage.js's `party` — both files are classic scripts sharing
// one global scope, so a duplicate top-level `let` would be a redeclaration error
// that aborts the other script. Keep cross-file top-level names unique.
let stageParty = []; // characters for the "who is acting" selector (M2e)

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

const OUTCOME = {
  critical_success: { icon: "★", label: "Critical", cls: "crit-good" },
  success: { icon: "✓", label: "Success", cls: "good" },
  failure: { icon: "✗", label: "Failure", cls: "bad" },
  critical_failure: { icon: "✗", label: "Critical miss", cls: "crit-bad" },
};

// Derive a readable die notation from the expression (the dice, without the flat
// modifier): "d20+5" → "d20", "2d6+3" → "2d6", advantage/disadvantage labelled.
function diceNotation(expression) {
  const e = String(expression || "").replaceAll(" ", "").toLowerCase();
  if (e.includes("2d20kh1")) return "d20 (adv)";
  if (e.includes("2d20kl1")) return "d20 (disadv)";
  const m = e.match(/\d*d\d+/);
  return m ? m[0].replace(/^d/, "1d").replace(/^1d/, "d") : e || "roll";
}

function span(cls, text) {
  const s = document.createElement("span");
  if (cls) s.className = cls;
  s.textContent = text;
  return s;
}

// A legible dice chip (M2h): shows the die face(s) vs the modifier vs the total,
// and — for a check — the DC and a coloured pass/fail/critical verdict. Falls back
// to a plain reading for pre-M2h events that lack the new fields.
function renderDice(ev) {
  const chip = document.createElement("span");
  chip.className = "chip dice";

  chip.append(span("", "🎲 "));
  if (ev.purpose) chip.append(span("d-label", `${ev.purpose} — `));
  chip.append(span("d-note", `${diceNotation(ev.expression)}: `));

  // Dice faces: kept dice sum with " + "; advantage's dropped die is struck through.
  const kept = Array.isArray(ev.kept) ? ev.kept : ev.rolls || [];
  const dropped = Array.isArray(ev.dropped) ? ev.dropped : [];
  kept.forEach((v, i) => {
    if (i) chip.append(span("", " + "));
    chip.append(span("", String(v)));
  });
  dropped.forEach((v) => {
    chip.append(span("", " / "));
    chip.append(span("dropped", String(v)));
  });

  if (ev.modifier) chip.append(span("d-mod", ` ${ev.modifier > 0 ? "+" : "−"}${Math.abs(ev.modifier)}`));
  chip.append(span("", ` = ${ev.total}`));

  if (ev.dc != null) {
    chip.append(span("d-note", ` vs DC ${ev.dc}`));
    const o = OUTCOME[ev.outcome];
    if (o) chip.append(span(`d-out ${o.cls}`, ` → ${o.icon} ${o.label}`));
  }

  const holder = document.createElement("p");
  holder.className = "notice";
  holder.appendChild(chip);
  // Modifier provenance: "+3 DEX, +2 proficiency" — visible and on hover.
  const breakdown = Array.isArray(ev.breakdown) ? ev.breakdown : [];
  if (breakdown.length) {
    const src = breakdown
      .map((b) => `${b.value > 0 ? "+" : "−"}${Math.abs(b.value)} ${b.source}`)
      .join(", ");
    chip.title = src;
    holder.appendChild(span("mod-src", src));
  }
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
  stageParty = Array.isArray(chars) ? chars : [];
  if (!actorSelect) return;
  if (stageParty.length < 2) {
    actorSelect.hidden = true;
    actorSelect.innerHTML = "";
    return;
  }
  const prev = actorSelect.value;
  actorSelect.innerHTML = stageParty
    .map((c) => {
      const tag = c.is_pc ? "" : " (NPC)";
      const name = String(c.name ?? "");
      return `<option value="${name}">${name}${tag}</option>`;
    })
    .join("");
  if (stageParty.some((c) => c.name === prev)) actorSelect.value = prev;
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
