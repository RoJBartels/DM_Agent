/* Knowledge-graph explorer (M2i).
 *
 * "A knowledge graph overview of the world would be cool … sub graphs (people,
 * cities, countries) so you can zoom." The lore graph has always existed —
 * `nodes` + `edges` built by the static pipeline — but nothing ever showed it to
 * the person who wrote it. This draws it, filters it by entity type (the
 * "sub graphs"), and lets a node be inspected, corrected or deleted in place.
 *
 * Layout is a small spring simulation run once, client-side, before the first
 * paint: these graphs are tens of nodes, not thousands, so a few hundred
 * iterations cost milliseconds and buy a stable, readable picture with no
 * library and no build step. Pan/zoom is just the SVG viewBox.
 */
(function () {
  const W = 1200;   // layout coordinate space; the viewBox maps it to the screen
  const H = 800;
  const TYPE_COLORS = {
    Character: "#c8a45c",
    Location: "#5c9ec8",
    Faction: "#b06fc8",
    Item: "#c86f6f",
    Deity: "#d8c05c",
    Event: "#6fc89a",
    Law: "#8f8f9c",
  };
  const color = (t) => TYPE_COLORS[t] || "#7f8c8d";

  const svg = document.getElementById("graph-svg");
  const screen = document.getElementById("graph-screen");
  const inspect = document.getElementById("graph-inspect");
  const NS = "http://www.w3.org/2000/svg";

  let worldId = null;
  let nodes = [];         // {id,type,name,prose,props,x,y}
  let edges = [];         // {id,src,dst,type}
  let hidden = new Set(); // types toggled off
  let selected = null;
  let view = { x: 0, y: 0, w: W, h: H };

  const esc = (s) =>
    String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;");

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

  // --- layout ---------------------------------------------------------------

  /* Fruchterman-Reingold-ish: every pair pushes apart, every edge pulls together,
   * and a cooling schedule freezes the picture. Seeded from a ring rather than at
   * random so re-opening the same world gives the same layout — a graph that
   * reshuffles itself every visit is much harder to learn. */
  function layout(ns, es) {
    const n = ns.length;
    if (!n) return;
    const k = Math.sqrt((W * H) / n) * 0.8;
    ns.forEach((node, i) => {
      const a = (2 * Math.PI * i) / n;
      node.x = W / 2 + (W / 3) * Math.cos(a);
      node.y = H / 2 + (H / 3) * Math.sin(a);
    });
    const byId = new Map(ns.map((x) => [x.id, x]));
    const links = es
      .map((e) => [byId.get(e.src), byId.get(e.dst)])
      .filter(([a, b]) => a && b && a !== b);

    let temp = W / 8;
    for (let step = 0; step < 320; step++) {
      for (const v of ns) { v.dx = 0; v.dy = 0; }
      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          const a = ns[i], b = ns[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          let d = Math.hypot(dx, dy) || 0.01;
          if (d > k * 4) continue; // distant pairs contribute nothing visible
          const rep = (k * k) / d;
          dx /= d; dy /= d;
          a.dx += dx * rep; a.dy += dy * rep;
          b.dx -= dx * rep; b.dy -= dy * rep;
        }
      }
      for (const [a, b] of links) {
        let dx = a.x - b.x, dy = a.y - b.y;
        const d = Math.hypot(dx, dy) || 0.01;
        const att = (d * d) / k;
        dx /= d; dy /= d;
        a.dx -= dx * att; a.dy -= dy * att;
        b.dx += dx * att; b.dy += dy * att;
      }
      for (const v of ns) {
        // Gentle pull to the middle so disconnected islands don't drift away.
        v.dx += (W / 2 - v.x) * 0.012;
        v.dy += (H / 2 - v.y) * 0.012;
        const d = Math.hypot(v.dx, v.dy) || 0.01;
        v.x += (v.dx / d) * Math.min(d, temp);
        v.y += (v.dy / d) * Math.min(d, temp);
        v.x = Math.max(40, Math.min(W - 40, v.x));
        v.y = Math.max(30, Math.min(H - 30, v.y));
      }
      temp *= 0.985;
    }
  }

  // --- rendering ------------------------------------------------------------

  const visible = (nd) => !hidden.has(nd.type);

  function render() {
    const shown = nodes.filter(visible);
    const ids = new Set(shown.map((n) => n.id));
    const shownEdges = edges.filter((e) => ids.has(e.src) && ids.has(e.dst));
    const byId = new Map(nodes.map((n) => [n.id, n]));

    svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
    svg.innerHTML = "";

    for (const e of shownEdges) {
      const a = byId.get(e.src), b = byId.get(e.dst);
      const line = document.createElementNS(NS, "line");
      line.setAttribute("class", "edge" + (selected && (e.src === selected || e.dst === selected) ? " lit" : ""));
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      svg.appendChild(line);
      if (selected && (e.src === selected || e.dst === selected)) {
        const t = document.createElementNS(NS, "text");
        t.setAttribute("class", "elabel");
        t.setAttribute("x", (a.x + b.x) / 2);
        t.setAttribute("y", (a.y + b.y) / 2 - 3);
        t.setAttribute("text-anchor", "middle");
        t.textContent = e.type;
        svg.appendChild(t);
      }
    }

    for (const nd of shown) {
      const g = document.createElementNS(NS, "g");
      g.setAttribute("class", "node" + (nd.id === selected ? " sel" : ""));
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", nd.x); c.setAttribute("cy", nd.y);
      c.setAttribute("r", nd.id === selected ? 11 : 8);
      c.setAttribute("fill", color(nd.type));
      const label = document.createElementNS(NS, "text");
      label.setAttribute("x", nd.x); label.setAttribute("y", nd.y + 21);
      label.setAttribute("text-anchor", "middle");
      label.textContent = nd.name;
      g.appendChild(c);
      g.appendChild(label);
      // Ignore the click that ends a pan, so dragging across the canvas doesn't
      // select whatever happened to be under the pointer when you let go.
      g.addEventListener("click", () => { if (!panned) select(nd.id); });
      svg.appendChild(g);
    }

    document.getElementById("graph-count").textContent =
      `${shown.length} of ${nodes.length} entities · ${shownEdges.length} relationships`;
  }

  function renderTypes() {
    const box = document.getElementById("graph-types");
    const types = [...new Set(nodes.map((n) => n.type))].sort();
    box.innerHTML = "";
    for (const t of types) {
      const b = document.createElement("button");
      const count = nodes.filter((n) => n.type === t).length;
      b.className = hidden.has(t) ? "" : "on";
      b.innerHTML =
        `<span style="color:${color(t)}">●</span> ${esc(t)} <span class="muted">${count}</span>`;
      b.addEventListener("click", () => {
        if (hidden.has(t)) hidden.delete(t); else hidden.add(t);
        renderTypes();
        render();
      });
      box.appendChild(b);
    }
  }

  // --- inspect / edit -------------------------------------------------------

  function select(id) {
    selected = id;
    render();
    renderInspect();
  }

  function renderInspect() {
    const nd = nodes.find((n) => n.id === selected);
    if (!nd) {
      inspect.innerHTML = `<p class="muted">Pick an entity to see what the DM knows
        about it — and to correct it.</p>`;
      return;
    }
    const links = edges
      .filter((e) => e.src === nd.id || e.dst === nd.id)
      .map((e) => {
        const otherId = e.src === nd.id ? e.dst : e.src;
        const other = nodes.find((n) => n.id === otherId);
        const arrow = e.src === nd.id ? "→" : "←";
        return `<span class="gi-link" data-goto="${esc(otherId)}">${esc(e.type)} ${arrow} ${
          esc(other ? other.name : otherId)
        }</span>`;
      })
      .join("");
    const props = Object.entries(nd.props || {})
      .filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length))
      .map(([k, v]) => `<div><span class="muted">${esc(k)}:</span> ${esc(
        Array.isArray(v) ? v.join(", ") : v
      )}</div>`)
      .join("");

    inspect.innerHTML = `
      <div class="gi-type" style="color:${color(nd.type)}">${esc(nd.type)}</div>
      <h3>${esc(nd.name)}</h3>
      <div class="muted" style="font-size:0.72rem">${esc(nd.id)}</div>
      <div class="gi-prose">${esc(nd.prose) || '<span class="muted">No description.</span>'}</div>
      ${props ? `<div style="font-size:0.8rem">${props}</div>` : ""}
      ${links ? `<div class="gi-links"><div class="gi-type">Connections</div>${links}</div>` : ""}
      <div class="start-actions" style="margin-top:1rem">
        <button data-edit class="ghost small">✎ Edit</button>
        <button data-del class="ghost small danger">🗑 Delete</button>
      </div>`;
    for (const link of inspect.querySelectorAll("[data-goto]")) {
      link.addEventListener("click", () => {
        const target = nodes.find((n) => n.id === link.dataset.goto);
        if (!target) return;
        hidden.delete(target.type); // don't hop to something a filter is hiding
        renderTypes();
        select(target.id);
      });
    }
    inspect.querySelector("[data-edit]").addEventListener("click", () => renderEdit(nd));
    inspect.querySelector("[data-del]").addEventListener("click", () => remove(nd));
  }

  function renderEdit(nd) {
    const types = [...new Set([...Object.keys(TYPE_COLORS), nd.type])];
    inspect.innerHTML = `
      <div class="gi-type">Editing</div>
      <div class="field"><label>Name</label><input id="g-name" value="${esc(nd.name)}"></div>
      <div class="field"><label>Type</label>
        <select id="g-type">
          ${types.map((t) => `<option ${t === nd.type ? "selected" : ""}>${esc(t)}</option>`).join("")}
        </select></div>
      <div class="field"><label>Description
        <span class="muted">— this is what retrieval searches</span></label>
        <textarea id="g-prose">${esc(nd.prose)}</textarea></div>
      <div class="modal-actions">
        <button id="g-cancel" class="ghost">Cancel</button>
        <button id="g-save">Save</button>
      </div>`;
    document.getElementById("g-cancel").addEventListener("click", renderInspect);
    document.getElementById("g-save").addEventListener("click", async () => {
      const body = {
        name: document.getElementById("g-name").value.trim() || nd.name,
        type: document.getElementById("g-type").value,
        prose: document.getElementById("g-prose").value,
      };
      try {
        const updated = await api(`/api/worlds/${worldId}/nodes/${encodeURIComponent(nd.id)}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        });
        Object.assign(nd, updated);
        renderTypes();
        render();
        renderInspect();
      } catch (err) {
        alert(`Couldn't save — ${err.message}`);
      }
    });
  }

  async function remove(nd) {
    if (!confirm(`Delete "${nd.name}" and every relationship it's part of?`)) return;
    try {
      await api(`/api/worlds/${worldId}/nodes/${encodeURIComponent(nd.id)}`, { method: "DELETE" });
      nodes = nodes.filter((n) => n.id !== nd.id);
      edges = edges.filter((e) => e.src !== nd.id && e.dst !== nd.id);
      selected = null;
      layout(nodes, edges);
      renderTypes();
      render();
      renderInspect();
    } catch (err) {
      alert(`Couldn't delete — ${err.message}`);
    }
  }

  // --- pan / zoom -----------------------------------------------------------

  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const factor = e.deltaY > 0 ? 1.12 : 1 / 1.12;
    const rect = svg.getBoundingClientRect();
    const fx = (e.clientX - rect.left) / rect.width;
    const fy = (e.clientY - rect.top) / rect.height;
    const nw = Math.max(150, Math.min(W * 3, view.w * factor));
    const nh = nw * (view.h / view.w);
    view.x += (view.w - nw) * fx;
    view.y += (view.h - nh) * fy;
    view.w = nw; view.h = nh;
    svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
  }, { passive: false });

  // Deliberately no setPointerCapture: capturing on the <svg> retargets the whole
  // gesture to it, so the click that lands on a node never reaches the node. We
  // track the drag ourselves and let clicks through, suppressing only the click
  // that ends an actual pan.
  let drag = null;
  let panned = false;
  svg.addEventListener("pointerdown", (e) => {
    drag = { x: e.clientX, y: e.clientY };
    panned = false;
  });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const rect = svg.getBoundingClientRect();
    if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3) panned = true;
    view.x -= ((e.clientX - drag.x) * view.w) / rect.width;
    view.y -= ((e.clientY - drag.y) * view.h) / rect.height;
    drag = { x: e.clientX, y: e.clientY };
    svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
  });
  for (const end of ["pointerup", "pointercancel", "pointerleave"]) {
    svg.addEventListener(end, () => { drag = null; });
  }

  // --- open / close ---------------------------------------------------------

  function close() {
    screen.classList.remove("open");
  }
  document.getElementById("graph-close").addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && screen.classList.contains("open")) close();
  });

  window.openGraph = async function openGraph(id, title) {
    worldId = id;
    selected = null;
    hidden = new Set();
    view = { x: 0, y: 0, w: W, h: H };
    document.getElementById("graph-title").textContent = title || "World";
    document.getElementById("graph-count").textContent = "loading…";
    screen.classList.add("open");
    try {
      const graph = await api(`/api/worlds/${id}/graph`);
      nodes = graph.nodes.map((n) => ({ ...n }));
      edges = graph.edges;
    } catch (err) {
      document.getElementById("graph-count").textContent = `couldn't load — ${err.message}`;
      return;
    }
    layout(nodes, edges);
    renderTypes();
    render();
    renderInspect();
  };
})();
