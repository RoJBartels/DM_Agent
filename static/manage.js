// Management layer (M2b): campaign switcher, party sheets, world upload.
// Drives the sidebar + modal and tells the stage (stage.js) which session to
// connect to. Kept separate from stage.js so the event-stream renderer stays
// focused; both are plain scripts, no build step.

const ABILITIES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"];
const LS_CAMPAIGN = "dm.campaign";

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
  detail.innerHTML = `
    <h2>${esc(c.name)}</h2>
    ${roster}
    <div class="start-actions">
      <button data-add class="ghost small">＋ Character</button>
      <button data-up="world" class="ghost small">⇪ World lore</button>
      <button data-up="story" class="ghost small">⇪ Adventure</button>
    </div>`;
  detail.querySelector("[data-add]").addEventListener("click", () => openCharacterModal(null));
  detail.querySelector('[data-up="world"]').addEventListener("click", () => openUploadModal("world"));
  detail.querySelector('[data-up="story"]').addEventListener("click", () => openUploadModal("story"));
  for (const span of detail.querySelectorAll(".rname")) {
    span.addEventListener("click", () => {
      const ch = party.find((m) => m.id === span.dataset.cid);
      if (ch) openCharacterModal(ch);
    });
  }
  play.disabled = false;
  play.textContent = c.has_history ? "▶ Continue" : "▶ Begin";
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

function characterFormHtml(c) {
  c = c || {};
  const stats = c.stats || {};
  const abilInputs = ABILITIES.map(
    (k) =>
      `<div><label style="text-align:center">${k}</label>` +
      `<input id="c-${k}" type="number" value="${stats[k] ?? 10}"></div>`
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
      <div class="field"><label>Class</label>
        <input id="c-class" type="text" value="${esc(stats.class || "")}"></div>
      <div class="field"><label>Level</label>
        <input id="c-level" type="number" value="${stats.level ?? 1}"></div>
      <div class="field"><label>Prof. bonus</label>
        <input id="c-prof" type="number" value="${stats.proficiency_bonus ?? 2}"></div>
    </div>
    <div class="field"><label>Ability scores</label>
      <div class="grid6">${abilInputs}</div></div>
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

function openCharacterModal(c) {
  const existingStats = (c && c.stats) || {};
  openModal(characterFormHtml(c));
  el("c-cancel").addEventListener("click", closeModal);

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
    stats.class = el("c-class").value.trim();
    stats.level = num("c-level", 1);
    stats.proficiency_bonus = num("c-prof", 2);
    for (const k of ABILITIES) stats[k] = num(`c-${k}`, 10);

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
