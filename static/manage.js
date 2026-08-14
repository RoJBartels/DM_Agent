// Management layer (M2b): campaign switcher, party sheets, world upload.
// Drives the sidebar + modal and tells the stage (stage.js) which session to
// connect to. Kept separate from stage.js so the event-stream renderer stays
// focused; both are plain scripts, no build step.

const ABILITIES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"];
const LS_CAMPAIGN = "dm.campaign";

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

let campaigns = [];
let activeCampaignId = null;
let party = []; // characters of the active campaign (drives menu roster + stage selector)

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

// --- campaigns -------------------------------------------------------------

async function loadCampaigns() {
  campaigns = await api("/api/campaigns");
  if (campaigns.length === 0) {
    // Bootstrap the out-of-the-box demo so a fresh DB is still playable.
    await api("/api/demo-session", { method: "POST" });
    campaigns = await api("/api/campaigns");
  }
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
  const campaign = campaigns.find((c) => c.id === activeCampaignId);
  el("world-status").textContent = campaign?.has_world ? "world loaded ✓" : "no world yet";
  el("story-status").textContent = campaign?.has_story ? "guide loaded ✓" : "no guide yet";
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
    body: JSON.stringify({ name: name.trim() }),
  });
  campaigns.push(c);
  renderCampaignSelect();
  return c;
}

// Re-open the start menu mid-play; refresh flags first so it's current.
async function showStartScreen() {
  campaigns = await api("/api/campaigns");
  renderCampaignSelect();
  renderStartCampaigns();
  renderStartDetail();
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
  detail.innerHTML = `
    <h2>${esc(c.name)}</h2>
    ${roster}
    <div class="start-actions">
      <button data-add class="ghost small">＋ Character</button>
      <button data-up="world" class="ghost small">⇪ World lore</button>
      <button data-up="story" class="ghost small">⇪ Adventure</button>
    </div>
    <label class="setting">
      <input type="checkbox" data-setting="auto_resolve_simple" ${autoResolve ? "checked" : ""}>
      <span>Auto-resolve trivial actions
        <span class="muted">— the DM may just do obviously-safe things (open an unlocked
        door, pick up a loose item) instead of asking first. Off by default; it still
        never decides your meaningful choices, and questions never advance the story.</span>
      </span>
    </label>`;
  detail.querySelector("[data-add]").addEventListener("click", () => openCharacterModal(null));
  detail.querySelector('[data-up="world"]').addEventListener("click", () => openUploadModal("world"));
  detail.querySelector('[data-up="story"]').addEventListener("click", () => openUploadModal("story"));
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

const UPLOADS = {
  world: {
    hasFlag: "has_world",
    title: "Upload world lore",
    intro: `Paste worldbuilding markdown. The build extracts a typed lore graph
      (characters, locations, factions…), embeds it, and writes community
      summaries. This can take a minute.`,
    rebuildWarn: "already has a world — rebuilding replaces its lore graph",
    placeholder: "# The Barony of Aldenmoor&#10;&#10;Duke Aldric Vane rules...",
    post: (id) => `/api/campaigns/${id}/world`,
    poll: (job) => `/api/world-jobs/${job}`,
    building: "building… (extracting entities, embedding, clustering)",
    done: (s) => `✓ built: ${s.nodes} nodes, ${s.edges} edges, ${s.communities} communities.`,
  },
  story: {
    hasFlag: "has_story",
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
el("upload-story").addEventListener("click", () => openUploadModal("story"));

function openUploadModal(kind) {
  const cfg = UPLOADS[kind];
  const campaign = campaigns.find((c) => c.id === activeCampaignId);
  const warn = campaign?.[cfg.hasFlag]
    ? `<p class="muted">This campaign ${cfg.rebuildWarn}.</p>`
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
  el("u-build").addEventListener("click", () => submitUpload(kind));
}

async function submitUpload(kind) {
  const cfg = UPLOADS[kind];
  const text = el("u-text").value.trim();
  const jobStatus = el("job-status");
  if (!text) { jobStatus.textContent = "Paste or choose a document first."; return; }
  const build = el("u-build");
  build.disabled = true;
  jobStatus.textContent = "starting build…";

  try {
    const { job_id } = await api(cfg.post(activeCampaignId), {
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
        // Refresh has_world/has_story flags + sidebar + start menu.
        campaigns = await api("/api/campaigns");
        renderCampaignSelect();
        renderCampaignStatuses();
        renderStartCampaigns();
        renderStartDetail();
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
  await selectCampaign(c.id); // stay on the menu; player enters when ready
});

(async function init() {
  await loadCampaigns();
  const stored = localStorage.getItem(LS_CAMPAIGN);
  const initial = campaigns.find((c) => c.id === stored) ? stored : campaigns[0].id;
  await selectCampaign(initial); // loads party + menu, does not connect
  el("start-screen").classList.add("open");
})();
