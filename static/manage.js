// Management layer (M2b): campaign switcher, party sheets, world upload.
// Drives the sidebar + modal and tells the stage (stage.js) which session to
// connect to. Kept separate from stage.js so the event-stream renderer stays
// focused; both are plain scripts, no build step.

const ABILITIES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"];
const LS_CAMPAIGN = "dm.campaign";
const LS_WORLD = "dm.world";

// --- SRD 5e helpers for the character creator (M2f, M2j) -------------------
// Hardcoded 5e on purpose — the M8 multi-ruleset pass generalizes this later.
// The creator only *augments* the free-form form: every list below is a
// suggestion the player can ignore, nothing is enforced, and free text stays
// allowed everywhere. What it writes into `stats` is what the narrator reads
// from the party roster each turn (rules/sheet.py), so capturing it here is
// what lets the DM know a rogue can pick a lock.

// The 18 SRD skills and the ability governing each — mirrors rules/sheet.py.
const SKILLS = {
  Acrobatics: "DEX", "Animal Handling": "WIS", Arcana: "INT", Athletics: "STR",
  Deception: "CHA", History: "INT", Insight: "WIS", Intimidation: "CHA",
  Investigation: "INT", Medicine: "WIS", Nature: "INT", Perception: "WIS",
  Performance: "CHA", Persuasion: "CHA", Religion: "INT", "Sleight of Hand": "DEX",
  Stealth: "DEX", Survival: "WIS",
};
const ALL_SKILLS = Object.keys(SKILLS);

// Per class: saving throws, a one-line "what it plays like", the skill list it
// picks from (and how many), starting tool proficiencies and level-1 features,
// and — for casters — a few common SRD cantrips/1st-level spells to offer.
const SRD_CLASSES = {
  Barbarian: {
    saves: ["STR", "CON"], picks: 2,
    blurb: "Front-line bruiser — rage, heavy hits, and the most hit points in the party.",
    skills: ["Animal Handling", "Athletics", "Intimidation", "Nature", "Perception", "Survival"],
    tools: [], features: ["Rage", "Unarmored Defense"],
  },
  Bard: {
    saves: ["DEX", "CHA"], picks: 3,
    blurb: "Face, support caster and jack of all trades — picks any three skills.",
    skills: ALL_SKILLS,
    tools: ["musical instrument"], features: ["Bardic Inspiration"],
    cantrips: ["vicious mockery", "minor illusion", "prestidigitation", "mage hand"],
    spells: ["healing word", "charm person", "disguise self", "faerie fire", "sleep"],
  },
  Cleric: {
    saves: ["WIS", "CHA"], picks: 2,
    blurb: "Divine caster — healing, blessings, and holy fire.",
    skills: ["History", "Insight", "Medicine", "Persuasion", "Religion"],
    tools: [], features: ["Divine Domain"],
    cantrips: ["sacred flame", "guidance", "light", "thaumaturgy"],
    spells: ["cure wounds", "bless", "guiding bolt", "shield of faith", "command"],
  },
  Druid: {
    saves: ["INT", "WIS"], picks: 2,
    blurb: "Nature caster and shapeshifter — the wild fights alongside you.",
    skills: ["Arcana", "Animal Handling", "Insight", "Medicine", "Nature", "Perception",
             "Religion", "Survival"],
    tools: ["herbalism kit"], features: ["Druidic"],
    cantrips: ["druidcraft", "produce flame", "shillelagh", "thorn whip"],
    spells: ["entangle", "faerie fire", "cure wounds", "goodberry", "thunderwave"],
  },
  Fighter: {
    saves: ["STR", "CON"], picks: 2,
    blurb: "Weapon master — the most reliable damage, and any armour you like.",
    skills: ["Acrobatics", "Animal Handling", "Athletics", "History", "Insight",
             "Intimidation", "Perception", "Survival"],
    tools: [], features: ["Fighting Style", "Second Wind"],
  },
  Monk: {
    saves: ["STR", "DEX"], picks: 2,
    blurb: "Unarmed martial artist — fast, mobile, and hard to pin down.",
    skills: ["Acrobatics", "Athletics", "History", "Insight", "Religion", "Stealth"],
    tools: ["artisan's tools or musical instrument"],
    features: ["Martial Arts", "Unarmored Defense"],
  },
  Paladin: {
    saves: ["WIS", "CHA"], picks: 2,
    blurb: "Armoured holy warrior — smites, auras, and a binding oath.",
    skills: ["Athletics", "Insight", "Intimidation", "Medicine", "Persuasion", "Religion"],
    tools: [], features: ["Divine Sense", "Lay on Hands"],
    spells: ["bless", "cure wounds", "divine favor", "shield of faith", "command"],
  },
  Ranger: {
    saves: ["STR", "DEX"], picks: 3,
    blurb: "Wilderness hunter — tracking, archery, and a little wild magic.",
    skills: ["Animal Handling", "Athletics", "Insight", "Investigation", "Nature",
             "Perception", "Stealth", "Survival"],
    tools: [], features: ["Favored Enemy", "Natural Explorer"],
    spells: ["hunter's mark", "cure wounds", "goodberry", "longstrider",
             "speak with animals"],
  },
  Rogue: {
    saves: ["DEX", "INT"], picks: 4,
    blurb: "Skill expert — stealth, locks, traps, and devastating sneak attacks.",
    skills: ["Acrobatics", "Athletics", "Deception", "Insight", "Intimidation",
             "Investigation", "Perception", "Performance", "Persuasion",
             "Sleight of Hand", "Stealth"],
    tools: ["thieves' tools"],
    features: ["Sneak Attack", "Expertise", "Thieves' Cant"],
  },
  Sorcerer: {
    saves: ["CON", "CHA"], picks: 2,
    blurb: "Innate arcane caster — raw blasting power, magic in the blood.",
    skills: ["Arcana", "Deception", "Insight", "Intimidation", "Persuasion", "Religion"],
    tools: [], features: ["Sorcerous Origin"],
    cantrips: ["fire bolt", "prestidigitation", "mage hand", "light", "ray of frost"],
    spells: ["magic missile", "shield", "burning hands", "charm person", "sleep"],
  },
  Warlock: {
    saves: ["WIS", "CHA"], picks: 2,
    blurb: "Pact caster — eldritch blast and an otherworldly bargain.",
    skills: ["Arcana", "Deception", "History", "Intimidation", "Investigation",
             "Nature", "Religion"],
    tools: [], features: ["Otherworldly Patron", "Pact Magic"],
    cantrips: ["eldritch blast", "minor illusion", "prestidigitation", "mage hand"],
    spells: ["hex", "armor of Agathys", "arms of Hadar", "charm person", "witch bolt"],
  },
  Wizard: {
    saves: ["INT", "WIS"], picks: 2,
    blurb: "Studied arcane caster — the broadest spellbook of anyone.",
    skills: ["Arcana", "History", "Insight", "Investigation", "Medicine", "Religion"],
    tools: [], features: ["Arcane Recovery", "Spellbook"],
    cantrips: ["fire bolt", "mage hand", "prestidigitation", "light", "ray of frost"],
    spells: ["magic missile", "shield", "detect magic", "mage armor", "sleep"],
  },
};
const STANDARD_ARRAY = [15, 14, 13, 12, 10, 8];
const POINT_BUY_COST = { 8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9 };
const POINT_BUY_BUDGET = 27;
// World-aligned pick-lists: which lore node type feeds each character field.
const WORLD_ROLES = [
  { key: "faction", label: "Faction", type: "Faction" },
  { key: "home", label: "Home", type: "Location" },
  { key: "deity", label: "Patron", type: "Deity" },
];

const abilMod = (s) => Math.floor((Number(s) - 10) / 2);
const fmtMod = (m) => (m > 0 ? `+${m}` : m < 0 ? `−${Math.abs(m)}` : "0");
const profForLevel = (lvl) => 2 + Math.floor((Math.max(1, Number(lvl) || 1) - 1) / 4);
const norm = (s) => String(s ?? "").trim().toLowerCase();
const slug = (s) => String(s).replace(/[^a-z0-9]+/gi, "-");
// stats lists are free-form JSONB — an array, or text a player typed by hand.
const splitList = (v) => String(v ?? "").split(/[\n,]/).map((s) => s.trim()).filter(Boolean);

function matchSrdClass(name) {
  const n = norm(name);
  return Object.keys(SRD_CLASSES).find((k) => k.toLowerCase() === n) || null;
}

const classInfo = (name) => SRD_CLASSES[matchSrdClass(name)] || null;

// The class as the form currently states it: a picked SRD class, or homebrew text.
function currentClass() {
  const sel = el("c-class-sel");
  if (!sel) return "";
  return sel.value === "__other" ? el("c-class-other").value.trim() : sel.value;
}

function roll4d6DropLowest() {
  const d = [0, 0, 0, 0].map(() => 1 + Math.floor(Math.random() * 6));
  d.sort((a, b) => a - b);
  return d[1] + d[2] + d[3];
}

const el = (id) => document.getElementById(id);
const overlay = el("overlay");
const modal = el("modal");

let worlds = [];
let activeWorldId = null;
let campaigns = []; // only the active world's — unrelated settings never mix (M2i)
let activeCampaignId = null;
let party = []; // characters of the active campaign (drives menu roster + stage selector)

const activeWorld = () => worlds.find((w) => w.id === activeWorldId);
const activeCampaign = () => campaigns.find((c) => c.id === activeCampaignId);

// --- fetch helpers ---------------------------------------------------------

async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.status === 204 ? null : res.json();
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// --- modal -----------------------------------------------------------------

function openModal(html) {
  modal.innerHTML = html;
  overlay.classList.add("open");
}
function closeModal() {
  overlay.classList.remove("open");
  modal.innerHTML = "";
}
overlay.addEventListener("click", (e) => { if (e.target === overlay) closeModal(); });

// --- worlds (M2i) ----------------------------------------------------------
//
// The world is the isolation boundary: everything below (campaigns, their
// characters, their stories) is loaded per world, so two unrelated settings can
// live on the same box without ever showing up in each other's menus.

async function loadWorlds() {
  worlds = await api("/api/worlds");
  if (worlds.length === 0) {
    // Bootstrap the out-of-the-box demo so a fresh DB is still playable.
    await api("/api/demo-session", { method: "POST" });
    worlds = await api("/api/worlds");
  }
  return worlds;
}

async function selectWorld(id) {
  activeWorldId = id;
  localStorage.setItem(LS_WORLD, id);
  el("world-name").textContent = activeWorld()?.name || "—";
  await loadCampaigns();
  // Keep the current campaign if it lives here; otherwise fall back to the first.
  const keep = campaigns.some((c) => c.id === activeCampaignId);
  renderStartWorlds();
  renderWorldDetail();
  if (campaigns.length) {
    await selectCampaign(keep ? activeCampaignId : campaigns[0].id);
  } else {
    activeCampaignId = null;
    party = [];
    window.Stage.setParty([]);
    renderParty([]);
    renderStartCampaigns();
    renderStartDetail();
  }
}

async function createWorld(name, description) {
  const w = await api("/api/worlds", {
    method: "POST",
    body: JSON.stringify({ name: name.trim(), description: (description || "").trim() }),
  });
  worlds.push(w);
  return w;
}

function renderStartWorlds() {
  const box = el("start-worlds");
  box.innerHTML = "";
  for (const w of worlds) {
    const card = document.createElement("div");
    card.className = "start-world" + (w.id === activeWorldId ? " active" : "");
    const bits = [
      `${w.campaign_count} campaign${w.campaign_count === 1 ? "" : "s"}`,
      w.node_count ? `${w.node_count} entities` : "no lore yet",
    ];
    card.innerHTML =
      `<span class="sw-name">${esc(w.name)}</span>` +
      `<span class="sw-meta">${bits.join(" · ")}</span>`;
    card.addEventListener("click", () => selectWorld(w.id));
    box.appendChild(card);
  }
}

function renderWorldDetail() {
  const detail = el("start-world-detail");
  const w = activeWorld();
  if (!w) { detail.innerHTML = ""; return; }
  detail.innerHTML = `
    <h2>${esc(w.name)}</h2>
    ${w.description ? `<p class="world-desc">${esc(w.description)}</p>` : ""}
    <div class="start-actions">
      <button data-w="lore" class="ghost small">⇪ Lore</button>
      <button data-w="graph" class="ghost small" ${w.node_count ? "" : "disabled"}>🕸 Explore</button>
      <button data-w="edit" class="ghost small">✎ Edit</button>
      <button data-w="delete" class="ghost small danger">🗑 Delete world</button>
    </div>`;
  detail.querySelector('[data-w="lore"]').addEventListener("click", () => openUploadModal("world"));
  detail.querySelector('[data-w="graph"]').addEventListener("click", () => openGraph(w.id, w.name));
  detail.querySelector('[data-w="edit"]').addEventListener("click", () => openWorldModal(w));
  detail.querySelector('[data-w="delete"]').addEventListener("click", () => deleteWorld(w));
}

function openWorldModal(w) {
  openModal(`
    <h2>Edit world</h2>
    <div class="field"><label>Name</label>
      <input id="w-name" value="${esc(w.name)}"></div>
    <div class="field"><label>Description <span class="muted">— for you, not the DM</span></label>
      <textarea id="w-desc" style="min-height:5rem">${esc(w.description)}</textarea></div>
    <div class="field">
      <label>Lore</label>
      <div class="muted" style="font-size:0.8rem">
        ${w.node_count} entities on record.
        Re-uploading replaces the graph; deleting it keeps the world and its campaigns.
      </div>
      <div class="start-actions" style="margin-top:0.5rem">
        <button id="w-relore" class="ghost small">⇪ Re-ingest lore</button>
        <button id="w-dellore" class="ghost small danger" ${w.node_count ? "" : "disabled"}>
          🗑 Delete lore
        </button>
      </div>
    </div>
    <div class="modal-actions">
      <button id="w-cancel" class="ghost">Cancel</button>
      <button id="w-save">Save</button>
    </div>`);
  el("w-cancel").addEventListener("click", closeModal);
  el("w-relore").addEventListener("click", () => openUploadModal("world"));
  el("w-dellore").addEventListener("click", () => deleteLore(w));
  el("w-save").addEventListener("click", async () => {
    const name = el("w-name").value.trim();
    if (!name) return;
    try {
      const updated = await api(`/api/worlds/${w.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name, description: el("w-desc").value }),
      });
      Object.assign(w, updated);
      closeModal();
      el("world-name").textContent = updated.name;
      renderStartWorlds();
      renderWorldDetail();
    } catch (err) {
      alert(`Couldn't save — ${err.message}`);
    }
  });
}

async function deleteLore(w) {
  if (!confirm(`Delete all lore for "${w.name}"? Campaigns and characters are kept.`)) return;
  try {
    const counts = await api(`/api/worlds/${w.id}/lore`, { method: "DELETE" });
    w.node_count = 0;
    closeModal();
    await refreshMenu();
    alert(`Removed ${counts.nodes} entities and ${counts.edges} relationships.`);
  } catch (err) {
    alert(`Couldn't delete lore — ${err.message}`);
  }
}

// The server refuses while campaigns remain, on purpose: deleting a setting must
// never quietly take somebody's saved games with it.
async function deleteWorld(w) {
  if (!confirm(`Delete the world "${w.name}" and all its lore?`)) return;
  try {
    await api(`/api/worlds/${w.id}`, { method: "DELETE" });
    worlds = worlds.filter((x) => x.id !== w.id);
    if (!worlds.length) worlds = await loadWorlds();
    await selectWorld(worlds[0].id);
  } catch (err) {
    alert(
      err.message.includes("409")
        ? `${w.name} still has campaigns in it. Delete those first — each one takes its ` +
          `characters and play history with it.`
        : `Couldn't delete the world — ${err.message}`
    );
  }
}

// --- campaigns -------------------------------------------------------------

async function loadCampaigns() {
  campaigns = await api(`/api/campaigns?world_id=${activeWorldId}`);
  renderCampaignSelect();
}

function renderCampaignSelect() {
  const sel = el("campaign-select");
  sel.innerHTML = campaigns
    .map((c) => `<option value="${c.id}">${esc(c.name)}</option>`)
    .join("");
  if (activeCampaignId) sel.value = activeCampaignId;
}

function renderCampaignStatuses() {
  const w = activeWorld();
  el("world-name").textContent = w?.name || "—";
  el("world-status").textContent = w?.node_count
    ? `${w.node_count} entities ✓`
    : "no lore yet";
  el("story-status").textContent = activeCampaign()?.has_story ? "guide loaded ✓" : "no guide yet";
}

// Selecting a campaign loads its party and refreshes the menu — it no longer
// connects. Play begins only when the player explicitly enters it (M2d).
async function selectCampaign(id) {
  activeCampaignId = id;
  localStorage.setItem(LS_CAMPAIGN, id);
  el("campaign-select").value = id;
  renderCampaignStatuses();
  renderStartCampaigns();
  await loadParty(id);
}

async function enterPlay() {
  if (!activeCampaignId) return;
  const { session_id } = await api(`/api/campaigns/${activeCampaignId}/session`, { method: "POST" });
  el("start-screen").classList.remove("open");
  window.Stage.connect(session_id);
}

async function createCampaign(name) {
  const c = await api("/api/campaigns", {
    method: "POST",
    body: JSON.stringify({ name: name.trim(), world_id: activeWorldId }),
  });
  campaigns.push(c);
  const w = activeWorld();
  if (w) w.campaign_count += 1;
  renderCampaignSelect();
  return c;
}

// A campaign owns its characters, its story guide and its play; the world's canon
// is not its to delete, and any sibling campaign keeps playing.
async function deleteCampaign(c) {
  if (
    !confirm(
      `Delete the campaign "${c.name}"? Its characters, story guide and play history ` +
        `go with it. The world's lore stays.`
    )
  ) return;
  try {
    await api(`/api/campaigns/${c.id}`, { method: "DELETE" });
    if (activeCampaignId === c.id) activeCampaignId = null;
    await selectWorld(activeWorldId);
  } catch (err) {
    alert(`Couldn't delete the campaign — ${err.message}`);
  }
}

// Re-read everything the menu shows, keeping the current selections.
async function refreshMenu() {
  worlds = await api("/api/worlds");
  if (!worlds.some((w) => w.id === activeWorldId)) activeWorldId = worlds[0]?.id ?? null;
  await loadCampaigns();
  renderStartWorlds();
  renderWorldDetail();
  renderStartCampaigns();
  renderCampaignStatuses();
  renderStartDetail();
}

// Re-open the start menu mid-play; refresh flags first so it's current.
async function showStartScreen() {
  await refreshMenu();
  el("start-screen").classList.add("open");
}

// The sidebar switcher jumps straight into that campaign's play.
el("campaign-select").addEventListener("change", async (e) => {
  await selectCampaign(e.target.value);
  await enterPlay();
});

el("new-campaign").addEventListener("click", async () => {
  const name = prompt("New campaign name:");
  if (!name || !name.trim()) return;
  const c = await createCampaign(name);
  await selectCampaign(c.id);
  await enterPlay();
});

// --- start menu (M2d) ------------------------------------------------------

function renderStartCampaigns() {
  const box = el("start-campaigns");
  box.innerHTML = "";
  for (const c of campaigns) {
    const card = document.createElement("div");
    card.className = "start-campaign" + (c.id === activeCampaignId ? " active" : "");
    const bits = [
      `${c.character_count} character${c.character_count === 1 ? "" : "s"}`,
      c.has_world ? "world ✓" : "no world",
    ];
    if (c.has_story) bits.push("guide ✓");
    card.innerHTML =
      `<span class="sc-name">${esc(c.name)}</span>` +
      `<span class="sc-meta">${bits.join(" · ")}</span>`;
    card.addEventListener("click", () => selectCampaign(c.id));
    box.appendChild(card);
  }
}

function renderStartDetail() {
  const detail = el("start-detail");
  const play = el("start-play");
  const c = campaigns.find((x) => x.id === activeCampaignId);
  if (!c) {
    detail.innerHTML = "";
    play.disabled = true;
    return;
  }
  const roster = party.length
    ? `<div class="roster">${party
        .map(
          (m) =>
            `<span class="rname" data-cid="${m.id}">${esc(m.name)}` +
            `${m.is_pc ? "" : " (NPC)"}</span>`
        )
        .join("")}</div>`
    : `<div class="muted">No characters yet.</div>`;
  const autoResolve = c.settings && c.settings.auto_resolve_simple;
  const recap = c.has_history
    ? `<div class="recap">
         <button data-recap class="ghost small">📖 Story so far</button>
         <div class="recap-text" hidden></div>
       </div>`
    : "";
  detail.innerHTML = `
    <h2>${esc(c.name)}</h2>
    ${roster}
    ${recap}
    <div class="start-actions">
      <button data-add class="ghost small">＋ Character</button>
      <button data-up="story" class="ghost small">⇪ Adventure</button>
      ${c.has_story ? `<button data-story class="ghost small">📜 Edit beats</button>` : ""}
      <button data-del class="ghost small danger">🗑 Delete campaign</button>
    </div>
    <label class="setting">
      <input type="checkbox" data-setting="auto_resolve_simple" ${autoResolve ? "checked" : ""}>
      <span>Auto-resolve trivial actions
        <span class="muted">— the DM may just do obviously-safe things (open an unlocked
        door, pick up a loose item) instead of asking first. Off by default; it still
        never decides your meaningful choices, and questions never advance the story.</span>
      </span>
    </label>`;
  const recapBtn = detail.querySelector("[data-recap]");
  if (recapBtn) recapBtn.addEventListener("click", () => toggleRecap(recapBtn));
  detail.querySelector("[data-add]").addEventListener("click", () => openCharacterModal(null));
  detail.querySelector('[data-up="story"]').addEventListener("click", () => openUploadModal("story"));
  detail.querySelector("[data-del]").addEventListener("click", () => deleteCampaign(c));
  const storyBtn = detail.querySelector("[data-story]");
  if (storyBtn) storyBtn.addEventListener("click", () => openStoryModal(c));
  detail.querySelector('[data-setting="auto_resolve_simple"]').addEventListener("change", (e) => {
    updateCampaignSetting("auto_resolve_simple", e.target.checked);
  });
  for (const span of detail.querySelectorAll(".rname")) {
    span.addEventListener("click", () => {
      const ch = party.find((m) => m.id === span.dataset.cid);
      if (ch) openCharacterModal(ch);
    });
  }
  play.disabled = false;
  play.textContent = c.has_history ? "▶ Continue" : "▶ Begin";
}

// The catch-up recap (M2k): a few sentences on where the story stands, for a
// player picking a campaign back up days later — the alternative to rereading the
// whole transcript. Fetched on demand rather than with the menu: it's a (cheap,
// server-cached) model call, so nothing is spent until someone asks for it.
async function toggleRecap(btn) {
  const box = btn.parentElement.querySelector(".recap-text");
  if (!box.hidden) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  if (box.dataset.loaded) return;
  box.textContent = "…catching you up";
  box.classList.add("muted");
  try {
    const { text } = await api(`/api/campaigns/${activeCampaignId}/recap`);
    box.textContent = text || "Nothing has happened yet.";
    box.classList.remove("muted");
    box.dataset.loaded = "1";
  } catch (err) {
    box.textContent = `Couldn't write the recap — ${err.message}`;
  }
}

// --- story-guide editor (M2i) ----------------------------------------------
//
// Beats are advisory, so editing one can't break a campaign — which is exactly
// why they should be editable: fixing an extraction the model got slightly wrong
// shouldn't mean re-uploading the whole adventure.

const BEAT_STATUSES = ["upcoming", "active", "completed", "skipped"];

async function openStoryModal(campaign) {
  openModal(`<h2>Story guide</h2><p class="muted">loading beats…</p>`);
  let beats;
  try {
    beats = await api(`/api/campaigns/${campaign.id}/story`);
  } catch (err) {
    openModal(`<h2>Story guide</h2><p class="muted">Couldn't load — ${esc(err.message)}</p>`);
    return;
  }
  renderStoryModal(campaign, beats);
}

function renderStoryModal(campaign, beats) {
  const rows = beats
    .map(
      (b, i) => `
      <div class="studio-row" data-beat="${b.id}">
        <span class="sr-type">${esc(b.status)}</span>
        <span class="sr-name">${i + 1}. ${esc(b.title)}
          ${b.entity_ids.length ? `<span class="sr-sub">· ${esc(b.entity_ids.join(", "))}</span>` : ""}
        </span>
        <button data-edit class="ghost small">✎</button>
        <button data-del class="ghost small danger">🗑</button>
      </div>`
    )
    .join("");
  openModal(`
    <h2>Story guide — ${esc(campaign.name)}</h2>
    <p class="muted">The DM paces toward the active beat but never enforces it; the party
      can always go off-book. Editing here is safe.</p>
    <div class="studio-list">${rows || `<p class="muted">No beats.</p>`}</div>
    <div class="modal-actions">
      <button id="s-delall" class="ghost danger" ${beats.length ? "" : "disabled"}>
        🗑 Delete guide
      </button>
      <button id="s-close">Done</button>
    </div>`);
  el("s-close").addEventListener("click", async () => { closeModal(); await refreshMenu(); });
  el("s-delall").addEventListener("click", async () => {
    if (!confirm(`Delete the whole guide? "${campaign.name}" goes back to improvised play.`)) return;
    try {
      await api(`/api/campaigns/${campaign.id}/story`, { method: "DELETE" });
      closeModal();
      await refreshMenu();
    } catch (err) {
      alert(`Couldn't delete — ${err.message}`);
    }
  });
  for (const row of modal.querySelectorAll("[data-beat]")) {
    const beat = beats.find((b) => b.id === row.dataset.beat);
    row.querySelector("[data-edit]").addEventListener("click", () =>
      openBeatModal(campaign, beat, beats)
    );
    row.querySelector("[data-del]").addEventListener("click", async () => {
      if (!confirm(`Delete the beat "${beat.title}"?`)) return;
      try {
        await api(`/api/story-beats/${beat.id}`, { method: "DELETE" });
        renderStoryModal(campaign, beats.filter((b) => b.id !== beat.id));
      } catch (err) {
        alert(`Couldn't delete — ${err.message}`);
      }
    });
  }
}

function openBeatModal(campaign, beat, beats) {
  openModal(`
    <h2>Edit beat</h2>
    <div class="field"><label>Title</label><input id="b-title" value="${esc(beat.title)}"></div>
    <div class="field"><label>Status</label>
      <select id="b-status">
        ${BEAT_STATUSES.map(
          (s) => `<option value="${s}" ${s === beat.status ? "selected" : ""}>${s}</option>`
        ).join("")}
      </select></div>
    <div class="field"><label>Summary <span class="muted">— what the DM should know</span></label>
      <textarea id="b-summary" style="min-height:6rem">${esc(beat.summary)}</textarea></div>
    <div class="field"><label>Read-aloud <span class="muted">— optional boxed text</span></label>
      <textarea id="b-read" style="min-height:4rem">${esc(beat.read_aloud)}</textarea></div>
    <div class="field"><label>Trigger <span class="muted">— when this beat becomes active</span></label>
      <input id="b-trigger" value="${esc(beat.trigger_condition)}"></div>
    <div class="modal-actions">
      <button id="b-back" class="ghost">Back</button>
      <button id="b-save">Save</button>
    </div>`);
  el("b-back").addEventListener("click", () => renderStoryModal(campaign, beats));
  el("b-save").addEventListener("click", async () => {
    try {
      const updated = await api(`/api/story-beats/${beat.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: el("b-title").value.trim() || beat.title,
          status: el("b-status").value,
          summary: el("b-summary").value,
          read_aloud: el("b-read").value,
          trigger_condition: el("b-trigger").value,
        }),
      });
      Object.assign(beat, updated);
      renderStoryModal(campaign, beats);
    } catch (err) {
      alert(`Couldn't save — ${err.message}`);
    }
  });
}

// Persist a single per-campaign setting (M2g). settings is stored as one blob, so
// merge onto the current copy and PATCH the whole object back.
async function updateCampaignSetting(key, value) {
  if (!activeCampaignId) return;
  const c = campaigns.find((x) => x.id === activeCampaignId);
  const settings = { ...((c && c.settings) || {}), [key]: value };
  try {
    const updated = await api(`/api/campaigns/${activeCampaignId}`, {
      method: "PATCH",
      body: JSON.stringify({ settings }),
    });
    if (c) Object.assign(c, updated);
  } catch (err) {
    alert(`Couldn't save setting — ${err.message}`);
    renderStartDetail(); // revert the checkbox to the stored value
  }
}

// --- party -----------------------------------------------------------------

async function loadParty(campaignId) {
  party = await api(`/api/campaigns/${campaignId}/characters`);
  renderParty(party);
  window.Stage.setParty(party); // keep the "who is acting" selector in sync (M2e)
  renderStartDetail();
}

function renderParty(chars) {
  const box = el("party");
  if (chars.length === 0) {
    box.innerHTML = `<div class="muted">No characters yet.</div>`;
    return;
  }
  box.innerHTML = "";
  for (const c of chars) {
    const item = document.createElement("div");
    item.className = "party-item";
    const cls = c.stats?.class ? `${c.stats.class}` : (c.is_pc ? "PC" : "NPC");
    const lvl = c.stats?.level ? ` · lvl ${c.stats.level}` : "";
    item.innerHTML =
      `<span><span class="pname">${esc(c.name)}</span>` +
      `<span class="pmeta"> — ${esc(cls)}${lvl}</span></span>` +
      `<span class="hp">${c.hp}/${c.max_hp} HP</span>`;
    item.addEventListener("click", () => openCharacterModal(c));
    box.appendChild(item);
  }
}

el("add-character").addEventListener("click", () => openCharacterModal(null));

// Optional world-aligned pick-lists (M2f): dropdowns for Faction / Home / Patron,
// populated from the campaign's lore graph. Only roles with lore entries render;
// a value the player typed earlier that isn't in the lore stays selectable.
function worldPickHtml(lore, stats) {
  const rows = WORLD_ROLES.map((r) => {
    const opts = lore[r.type] || [];
    const current = stats[r.key] || "";
    if (!opts.length && !current) return "";
    const options = ['<option value="">(none)</option>'];
    for (const n of opts) {
      const sel = n.name === current ? " selected" : "";
      options.push(`<option value="${esc(n.name)}"${sel}>${esc(n.name)}</option>`);
    }
    if (current && !opts.some((n) => n.name === current)) {
      options.push(`<option value="${esc(current)}" selected>${esc(current)} (custom)</option>`);
    }
    return `<div class="field"><label>${r.label}</label>
      <select id="c-world-${r.key}">${options.join("")}</select></div>`;
  }).filter(Boolean);
  if (!rows.length) return "";
  return `<div class="field"><label>From the world</label>
    <div class="grid">${rows.join("")}</div>
    <div class="muted" style="margin-top:0.3rem">Optional — tie this character into the
      campaign's lore. Anything free-form still goes in Notes.</div></div>`;
}

// The class picker (M2j): a list of playable options with a one-line description,
// plus an explicit "other" escape hatch so homebrew is still first-class. The old
// bare datalist left it unclear which classes the game actually knows.
function classPickHtml(stats) {
  const known = matchSrdClass(stats.class);
  const custom = !known && String(stats.class || "").trim();
  const options = ['<option value="">(choose a class)</option>']
    .concat(
      Object.keys(SRD_CLASSES).map(
        (n) => `<option value="${n}"${n === known ? " selected" : ""}>${n}</option>`
      )
    )
    .concat([`<option value="__other"${custom ? " selected" : ""}>Other / homebrew…</option>`])
    .join("");
  return `<div class="field"><label>Class</label>
    <select id="c-class-sel">${options}</select>
    <input id="c-class-other" type="text" placeholder="Homebrew class"
      style="margin-top:0.35rem" value="${esc(custom || "")}"${custom ? "" : " hidden"}></div>`;
}

// Skill proficiencies (M2j). All 18 SRD skills, always pickable; the chosen
// class's list is highlighted and its pick count shown as guidance, never
// enforced. The live modifier is the number the DM will roll with.
function skillsHtml(stats) {
  const proficient = new Set(splitList(stats.skills).map(norm));
  const expert = new Set(splitList(stats.expertise).map(norm));
  const rows = ALL_SKILLS.map((name) => {
    const id = slug(name);
    const isExpert = expert.has(norm(name));
    const checked = isExpert || proficient.has(norm(name));
    return `<div class="skill" data-skill="${esc(name)}">
      <input type="checkbox" class="sk-prof" id="sk-${id}"${checked ? " checked" : ""}>
      <label for="sk-${id}">${esc(name)} <span class="sk-abil">${SKILLS[name]}</span></label>
      <span class="sk-mod" id="skmod-${id}"></span>
      <input type="checkbox" class="sk-exp" id="skx-${id}"${isExpert ? " checked" : ""}>
      <label for="skx-${id}" class="sk-exp-label"
        title="Expertise — double proficiency bonus">×2</label>
    </div>`;
  }).join("");
  return `<div class="field"><label>Skill proficiencies</label>
    <div class="skills">${rows}</div>
    <div class="muted" id="sk-hint"></div></div>`;
}

// Tools, spells and features (M2j) — the rest of "what can this hero do".
function loadoutHtml(stats) {
  return `
    <div class="grid2">
      <div class="field"><label>Tool &amp; kit proficiencies</label>
        <input id="c-tools" type="text" placeholder="thieves' tools, herbalism kit"
          value="${esc(splitList(stats.tools).join(", "))}"></div>
      <div class="field"><label>Class features</label>
        <input id="c-features" type="text" placeholder="Sneak Attack, Cunning Action"
          value="${esc(splitList(stats.features).join(", "))}"></div>
    </div>
    <div class="field" id="spell-field">
      <label>Spells &amp; cantrips (one per line)</label>
      <textarea id="c-spells" style="min-height:4.5rem"
        >${esc(splitList(stats.spells).join("\n"))}</textarea>
      <div class="chips" id="spell-suggest"></div>
    </div>`;
}

function characterFormHtml(c, lore) {
  c = c || {};
  const stats = c.stats || {};
  const abilInputs = ABILITIES.map(
    (k) =>
      `<div class="abil"><label>${k}</label>` +
      `<input id="c-${k}" type="number" value="${stats[k] ?? 10}">` +
      `<span class="abil-mod" id="mod-${k}"></span></div>`
  ).join("");
  return `
    <h2>${c.id ? "Edit character" : "New character"}</h2>
    <div class="field"><label>Name</label>
      <input id="c-name" type="text" value="${esc(c.name || "")}"></div>
    <div class="checkbox field">
      <input id="c-ispc" type="checkbox" ${c.is_pc === false ? "" : "checked"}>
      <label style="margin:0">Player character</label></div>
    <div class="muted" style="margin:-0.55rem 0 0.85rem">
      Checked = a hero a player controls (listed first in the party). Unchecked =
      an NPC the DM voices.</div>
    <div class="grid">
      ${classPickHtml(stats)}
      <div class="field"><label>Level</label>
        <input id="c-level" type="number" value="${stats.level ?? 1}"></div>
      <div class="field"><label>Prof. bonus</label>
        <input id="c-prof" type="number" value="${stats.proficiency_bonus ?? 2}"></div>
    </div>
    <div class="muted" id="c-classinfo" style="margin:-0.4rem 0 0.85rem"></div>
    ${worldPickHtml(lore, stats)}
    <div class="field"><label>Ability scores</label>
      <div class="grid6">${abilInputs}</div>
      <div class="abil-tools">
        <button type="button" id="c-std" class="ghost small">Standard array</button>
        <button type="button" id="c-roll" class="ghost small">Roll 4d6</button>
        <span class="pb">
          <span id="pb-spent"></span>
          <span class="muted" id="pb-range">point buy: 8–15 per score</span>
        </span>
      </div></div>
    ${skillsHtml(stats)}
    ${loadoutHtml(stats)}
    <div class="grid">
      <div class="field"><label>AC</label>
        <input id="c-ac" type="number" value="${c.ac ?? 10}"></div>
      <div class="field"><label>Max HP</label>
        <input id="c-maxhp" type="number" value="${c.max_hp ?? 10}"></div>
      <div class="field"><label>HP</label>
        <input id="c-hp" type="number" value="${c.hp ?? 10}"></div>
    </div>
    <div class="field"><label>Inventory (one per line)</label>
      <textarea id="c-inv">${esc((c.inventory || []).join("\n"))}</textarea></div>
    <div class="field"><label>Notes</label>
      <textarea id="c-notes">${esc(c.notes || "")}</textarea></div>
    <div class="modal-actions">
      ${c.id ? '<button id="c-delete" class="danger small">Delete</button>' : ""}
      <button id="c-cancel" class="ghost">Cancel</button>
      <button id="c-save">Save</button>
    </div>`;
}

// Live-update the derived readouts as the form is edited: ability modifiers, the
// point-buy budget, and each skill's total modifier.
function refreshDerived() {
  for (const k of ABILITIES) {
    const span = el(`mod-${k}`);
    if (span) span.textContent = fmtMod(abilMod(el(`c-${k}`).value));
  }
  refreshPointBuy();
  refreshSkills();
}

// Point buy (M2j repair). Two facts, two readouts, both always on screen: how
// much of the budget is SPENT, and what the legal per-score RANGE is. The old
// single label flipped between "27" and "8–15" as scores went out of range,
// which read like one number changing meaning.
function refreshPointBuy() {
  const spent = el("pb-spent");
  if (!spent) return;
  let total = 0;
  let buyable = true;
  for (const k of ABILITIES) {
    const v = parseInt(el(`c-${k}`).value, 10);
    if (!(v in POINT_BUY_COST)) { buyable = false; break; }
    total += POINT_BUY_COST[v];
  }
  if (!buyable) {
    // Rolled scores, or a score outside the buyable band — there's no budget to
    // report, which is fine and not an error.
    spent.textContent = "spent: n/a — a score is outside the range";
    spent.classList.remove("over");
    return;
  }
  const over = total - POINT_BUY_BUDGET;
  spent.textContent =
    over > 0
      ? `spent: ${total} of ${POINT_BUY_BUDGET} — ⚠ ${over} over budget`
      : `spent: ${total} of ${POINT_BUY_BUDGET}`;
  spent.classList.toggle("over", over > 0);
}

// Skill rows (M2j): highlight the class's list, show how many it grants, and
// keep each skill's live modifier (ability + proficiency, doubled by expertise)
// in view — that number is exactly what the DM will roll.
function refreshSkills() {
  const rows = document.querySelectorAll(".skill");
  if (!rows.length) return;
  const cls = matchSrdClass(currentClass());
  const info = classInfo(currentClass());
  const eligible = info ? new Set(info.skills.map(norm)) : null;
  const bonus = parseInt(el("c-prof").value, 10) || 0;
  let chosen = 0;
  for (const row of rows) {
    const name = row.dataset.skill;
    const id = slug(name);
    const prof = el(`sk-${id}`);
    const exp = el(`skx-${id}`);
    if (!prof.checked) exp.checked = false; // expertise implies proficiency
    exp.disabled = !prof.checked;
    if (prof.checked) chosen++;
    row.classList.toggle("eligible", !!eligible && eligible.has(norm(name)));
    const mod =
      abilMod(el(`c-${SKILLS[name]}`).value) +
      (prof.checked ? bonus : 0) +
      (exp.checked ? bonus : 0);
    const out = el(`skmod-${id}`);
    out.textContent = fmtMod(mod);
    out.classList.toggle("trained", prof.checked);
  }
  el("sk-hint").textContent = info
    ? `${chosen} chosen · a ${cls} normally picks ${info.picks} from the highlighted ` +
      `list (plus any from your background) — but nothing here is enforced.`
    : `${chosen} chosen · pick whatever fits your hero.`;
}

// Class-dependent parts of the form: the description line, spell suggestions,
// and the skill highlighting.
function refreshClass() {
  const sel = el("c-class-sel");
  if (!sel) return;
  el("c-class-other").hidden = sel.value !== "__other";
  const info = classInfo(currentClass());
  el("c-classinfo").textContent = info
    ? `${info.blurb} Saving throws: ${info.saves.join(", ")}.`
    : "";
  refreshSpellSuggestions();
  refreshSkills();
}

// Class-appropriate spells offered as click-to-add chips (M2j). A textarea keeps
// the list free-form — the suggestions are a starting point, not the whole SRD.
function refreshSpellSuggestions() {
  const box = el("spell-suggest");
  if (!box) return;
  const info = classInfo(currentClass());
  const suggestions = info ? [...(info.cantrips || []), ...(info.spells || [])] : [];
  // A non-caster's spell field stays out of the way unless they already know something.
  el("spell-field").hidden = !suggestions.length && !el("c-spells").value.trim();
  box.innerHTML = suggestions.length
    ? `<span class="muted">common for a ${matchSrdClass(currentClass())}:</span>` +
      suggestions
        .map((s) => `<button type="button" class="chip-add">${esc(s)}</button>`)
        .join("")
    : "";
  for (const b of box.querySelectorAll(".chip-add")) {
    b.addEventListener("click", () => addSpell(b.textContent));
  }
}

function addSpell(name) {
  const ta = el("c-spells");
  if (splitList(ta.value).map(norm).includes(norm(name))) return;
  ta.value = ta.value.trim() ? `${ta.value.trim()}\n${name}` : name;
}

// What applyClassDefaults last wrote, so switching class can replace its own
// suggestion instead of stranding the previous class's. Reset per modal.
let appliedDefaults = { tools: "", features: "" };

// Fill in what the class grants, without ever overwriting the player's own text:
// an empty field gets the suggestion, a field still holding the last suggestion
// gets the new one, and anything the player typed or edited is left alone.
function applyClassDefaults() {
  const info = classInfo(currentClass());
  const next = {
    tools: info ? info.tools.join(", ") : "",
    features: info ? info.features.join(", ") : "",
  };
  for (const key of ["tools", "features"]) {
    const field = el(`c-${key}`);
    if (!field.value.trim() || field.value === appliedDefaults[key]) field.value = next[key];
  }
  appliedDefaults = next;
}

async function openCharacterModal(c) {
  const existingStats = (c && c.stats) || {};
  const lore = await fetchLoreForCreator();
  openModal(characterFormHtml(c, lore));
  appliedDefaults = { tools: "", features: "" }; // this sheet's text is the player's
  refreshClass();
  // Only a brand-new sheet gets class defaults filled in on open: an existing
  // character's empty field is a choice they already made, not a gap to fill.
  if (!c) applyClassDefaults();
  refreshDerived();

  el("c-cancel").addEventListener("click", closeModal);
  for (const k of ABILITIES) el(`c-${k}`).addEventListener("input", refreshDerived);
  el("c-class-sel").addEventListener("change", () => {
    refreshClass();
    applyClassDefaults(); // only fills fields the player left empty
  });
  el("c-class-other").addEventListener("input", refreshClass);
  el("c-prof").addEventListener("input", refreshSkills);
  el("c-level").addEventListener("input", () => {
    el("c-prof").value = profForLevel(el("c-level").value); // auto-derive; still editable
    refreshSkills();
  });
  for (const box of modal.querySelectorAll(".sk-prof, .sk-exp")) {
    box.addEventListener("change", refreshSkills);
  }
  el("c-std").addEventListener("click", () => {
    ABILITIES.forEach((k, i) => { el(`c-${k}`).value = STANDARD_ARRAY[i]; });
    refreshDerived();
  });
  el("c-roll").addEventListener("click", () => {
    for (const k of ABILITIES) el(`c-${k}`).value = roll4d6DropLowest();
    refreshDerived();
  });

  el("c-save").addEventListener("click", async () => {
    const name = el("c-name").value.trim();
    if (!name) { alert("Name is required."); return; }
    const num = (id, d) => {
      const v = parseInt(el(id).value, 10);
      return Number.isNaN(v) ? d : v;
    };
    // Overlay the edited known fields onto existing stats so any extra keys
    // (M8: other rulesets' stats) survive an edit.
    const stats = { ...existingStats };
    stats.class = currentClass();
    stats.level = num("c-level", 1);
    stats.proficiency_bonus = num("c-prof", 2);
    for (const k of ABILITIES) stats[k] = num(`c-${k}`, 10);
    // Prefill class saving-throw proficiencies for a recognized SRD class (M2f).
    const cls = matchSrdClass(stats.class);
    if (cls) stats.save_proficiencies = SRD_CLASSES[cls].saves;
    // Capabilities (M2j): what the narrator reads off the party roster each turn,
    // and therefore what decides whether this hero can pick a lock or cast a spell.
    const skills = [];
    const expertise = [];
    for (const row of modal.querySelectorAll(".skill")) {
      const id = slug(row.dataset.skill);
      if (el(`sk-${id}`).checked) skills.push(row.dataset.skill);
      if (el(`skx-${id}`).checked) expertise.push(row.dataset.skill);
    }
    setList(stats, "skills", skills);
    setList(stats, "expertise", expertise);
    setList(stats, "tools", splitList(el("c-tools").value));
    setList(stats, "features", splitList(el("c-features").value));
    setList(stats, "spells", splitList(el("c-spells").value));
    // World-aligned picks write into stats; clearing one removes the key.
    for (const r of WORLD_ROLES) {
      const sel = el(`c-world-${r.key}`);
      if (!sel) continue;
      const v = sel.value.trim();
      if (v) stats[r.key] = v; else delete stats[r.key];
    }

    const body = {
      name,
      is_pc: el("c-ispc").checked,
      stats,
      max_hp: num("c-maxhp", 10),
      hp: num("c-hp", 10),
      ac: num("c-ac", 10),
      inventory: el("c-inv").value.split("\n").map((s) => s.trim()).filter(Boolean),
      notes: el("c-notes").value,
    };

    try {
      if (c && c.id) {
        await api(`/api/characters/${c.id}`, { method: "PATCH", body: JSON.stringify(body) });
      } else {
        await api(`/api/campaigns/${activeCampaignId}/characters`, {
          method: "POST",
          body: JSON.stringify(body),
        });
      }
      closeModal();
      await loadParty(activeCampaignId);
    } catch (err) {
      alert(`Save failed — ${err.message}`);
    }
  });

  const del = el("c-delete");
  if (del) {
    del.addEventListener("click", async () => {
      if (!confirm(`Remove ${c.name} from the party?`)) return;
      await api(`/api/characters/${c.id}`, { method: "DELETE" });
      closeModal();
      await loadParty(activeCampaignId);
    });
  }
}

// Keep `stats` tidy: an emptied list drops its key rather than storing [].
function setList(stats, key, values) {
  if (values.length) stats[key] = values;
  else delete stats[key];
}

// Lore entities for the creator's world-aligned pick-lists (M2f), grouped by type.
// Empty (all groups) when the campaign has no world or the fetch fails.
async function fetchLoreForCreator() {
  const grouped = { Faction: [], Location: [], Deity: [] };
  const camp = campaigns.find((c) => c.id === activeCampaignId);
  if (!camp || !camp.has_world) return grouped;
  try {
    const nodes = await api(
      `/api/campaigns/${activeCampaignId}/lore-nodes?types=Faction,Location,Deity`
    );
    for (const n of nodes) if (grouped[n.type]) grouped[n.type].push(n);
  } catch (_) { /* best-effort — creator still works without lore */ }
  return grouped;
}

// --- uploads (world lore + story guide) ------------------------------------
// Both flows are the same shape — paste/choose a doc, POST it, poll a job — so
// they share one modal driven by this config.

// Two upload flows through one modal. They differ in what they attach to: lore
// belongs to the WORLD (every campaign in it shares that canon), a story guide to
// one campaign (it's that playthrough's arc).
const UPLOADS = {
  world: {
    scope: "world",
    subject: () => activeWorld(),
    has: (w) => w && w.node_count > 0,
    title: "Upload world lore",
    intro: `Paste worldbuilding markdown. The build extracts a typed lore graph
      (characters, locations, factions…), embeds it, and writes community
      summaries. Every campaign in this world draws on it. This can take a minute.`,
    rebuildWarn: "already has lore — rebuilding replaces the whole graph",
    placeholder: "# The Barony of Aldenmoor&#10;&#10;Duke Aldric Vane rules...",
    post: (id) => `/api/worlds/${id}/lore`,
    poll: (job) => `/api/world-jobs/${job}`,
    building: "building… (extracting entities, embedding, clustering)",
    done: (s) => `✓ built: ${s.nodes} nodes, ${s.edges} edges, ${s.communities} communities.`,
  },
  story: {
    scope: "campaign",
    subject: () => activeCampaign(),
    has: (c) => Boolean(c && c.has_story),
    title: "Upload adventure guide",
    intro: `Paste a pre-written adventure or your own outline. It's extracted into
      an ordered set of advisory story beats the DM uses to pace the game — never
      to script it. The party can always go off-book.`,
    rebuildWarn: "already has a story guide — rebuilding replaces its beats",
    placeholder: "# The Harvest Banquet&#10;&#10;Act 1: the party arrives at Vane Hall...",
    post: (id) => `/api/campaigns/${id}/story`,
    poll: (job) => `/api/story-jobs/${job}`,
    building: "building… (extracting story beats)",
    done: (s) => `✓ built: ${s.beats} story beats.`,
  },
};

el("upload-world").addEventListener("click", () => openUploadModal("world"));
el("explore-world").addEventListener("click", () => {
  const w = activeWorld();
  if (w) openGraph(w.id, w.name);
});
el("upload-story").addEventListener("click", () => openUploadModal("story"));

function openUploadModal(kind) {
  const cfg = UPLOADS[kind];
  const subject = cfg.subject();
  if (!subject) {
    alert(kind === "story" ? "Pick a campaign first." : "Pick a world first.");
    return;
  }
  const warn = cfg.has(subject)
    ? `<p class="muted">${esc(subject.name)} ${cfg.rebuildWarn}.</p>`
    : "";
  openModal(`
    <h2>${esc(cfg.title)}</h2>
    <p class="muted">${cfg.intro}</p>
    ${warn}
    <div class="field"><input id="u-file" type="file" accept=".md,.markdown,.txt"></div>
    <div class="field"><textarea id="u-text" style="min-height:13rem"
      placeholder="${cfg.placeholder}"></textarea></div>
    <div id="job-status"></div>
    <div class="modal-actions">
      <button id="u-cancel" class="ghost">Cancel</button>
      <button id="u-build">Build</button>
    </div>`);

  el("u-cancel").addEventListener("click", closeModal);
  el("u-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (file) el("u-text").value = await file.text();
  });
  el("u-build").addEventListener("click", () => submitUpload(kind, subject.id));
}

async function submitUpload(kind, subjectId) {
  const cfg = UPLOADS[kind];
  const text = el("u-text").value.trim();
  const jobStatus = el("job-status");
  if (!text) { jobStatus.textContent = "Paste or choose a document first."; return; }
  const build = el("u-build");
  build.disabled = true;
  jobStatus.textContent = "starting build…";

  try {
    const { job_id } = await api(cfg.post(subjectId), {
      method: "POST",
      body: JSON.stringify({ documents: [text] }),
    });

    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const job = await api(cfg.poll(job_id));
      if (job.status === "running") {
        jobStatus.textContent = cfg.building;
        continue;
      }
      if (job.status === "done") {
        jobStatus.textContent = cfg.done(job.stats || {});
        build.textContent = "Done";
        await refreshMenu(); // node counts, has_story, the sidebar statuses
        return;
      }
      jobStatus.innerHTML = `<span class="error">✗ build failed: ${esc(job.error)}</span>`;
      build.disabled = false;
      return;
    }
  } catch (err) {
    jobStatus.innerHTML = `<span class="error">✗ ${esc(err.message)}</span>`;
    build.disabled = false;
  }
}

// --- init ------------------------------------------------------------------

el("toggle-sidebar").addEventListener("click", () => {
  el("sidebar").classList.toggle("collapsed");
});
el("home").addEventListener("click", showStartScreen);
el("start-play").addEventListener("click", enterPlay);
el("start-new-campaign").addEventListener("click", async () => {
  const name = prompt("New campaign name:");
  if (!name || !name.trim()) return;
  const c = await createCampaign(name);
  renderStartWorlds();
  await selectCampaign(c.id); // stay on the menu; player enters when ready
});
el("start-new-world").addEventListener("click", async () => {
  const name = prompt("New world name:");
  if (!name || !name.trim()) return;
  const description = prompt("A line about it (optional):") || "";
  const w = await createWorld(name, description);
  await selectWorld(w.id);
});

(async function init() {
  await loadWorlds();
  const storedWorld = localStorage.getItem(LS_WORLD);
  const storedCampaign = localStorage.getItem(LS_CAMPAIGN);
  // Restore the last campaign only if it still lives in the world we're opening —
  // otherwise selectWorld falls back to that world's first campaign.
  activeCampaignId = storedCampaign;
  await selectWorld(worlds.some((w) => w.id === storedWorld) ? storedWorld : worlds[0].id);
  el("start-screen").classList.add("open");
})();
