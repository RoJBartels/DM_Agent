# Dungeon Master Agent — Architecture Reference (v1, locked)

**Status:** Global architecture agreed and locked. **Addendum 2026-07-12** (post first play session): §2b (story/adventure guide) added and decided. **Addendum 2026-08-11** (second play session): player agency elevated to a core principle below; embeddings noted as a required runtime dependency (§8); build order (§9) gains M2d/M2e. Neither addendum reopens any prior decision.
**Core design principle:** *The LLM narrates, code adjudicates.* Everything mechanical (dice, HP, DCs, combat math) is handled by deterministic tools — never by the LLM.
**Player agency is absolute (core principle):** the players decide; the DM never decides for them. It answers a player's questions and describes the world *without advancing the fiction*, and never has a player's character take a consequential action the player didn't declare — it offers options and waits. The world moves only in response to a declared action (or a danger already in motion the player knows about). This binds every layer and milestone below (enforced today in the orchestrator system prompt; must survive party play, NPC subagents, and the story guide — which is advisory, never scripting).

---

## 1. Layered Architecture

```
┌─────────────────────────────────────────────────────┐
│  Frontend (stage, not chat): web / Discord           │
│  Consumes event stream via websocket                 │
├─────────────────────────────────────────────────────┤
│  Orchestrator / Agent Loop (FastAPI + Anthropic SDK) │
│  - routes player input                               │
│  - tool calling, streaming, error handling           │
│  - spawns NPC subagents                              │
├──────────────┬──────────────────┬───────────────────┤
│ Rules Engine │  Knowledge Layer │  Ambient Layer     │
│ (determin-   │  (static hybrid  │  (event-triggered  │
│  istic code) │   + dynamic RAG) │   sfx/visuals)     │
├──────────────┴──────────────────┴───────────────────┤
│  Game State: Postgres (structured) + Vector DB       │
│  + Knowledge Graph (static canon)                    │
└─────────────────────────────────────────────────────┘
```

### Components

1. **Orchestrator / agent loop** — code service (Python/FastAPI + Anthropic SDK). Owns conversation state, tool-call round-trips, streaming, subagent spawning. *Not* built in a workflow engine.
2. **Game state store** — Postgres for structured state (HP, inventory, quest flags, positions); vector DB (pgvector or Qdrant) for semantic recall.
3. **Rules engine** — deterministic code for dice, combat math, adjudication. LLM decides *what* to roll ("DC 15 Dex save"), the engine rolls and returns the result.
4. **Narrative layer** — LLM generates prose, dialogue, consequences, grounded by retrieved state + rules output.
5. **Ambient layer** — client-side renderer for sound and visual effects, driven by the event stream (see §6).
6. **Story guide (optional)** — advisory beat outline from an uploaded adventure, injected into context each turn to guide pacing without scripting outcomes (see §2b).

---

## 2. Knowledge Layer — Hybrid Approach (decided)

**Decision: Hybrid is locked.** The static/dynamic split addresses Microsoft GraphRAG's known weakness — slow, expensive re-indexing when new information arrives — by keeping live updates out of the graph entirely.

### Static layer (canon) — graph + attached prose
- Built **once at campaign creation**; expensive GraphRAG-style indexing is acceptable here.
- **Nodes:** `Character`, `Location`, `Faction`, `Item`, `Deity`, `Event`, `Law/Rule`
- **Edges:** `RULES`, `LOCATED_IN`, `MEMBER_OF`, `ENEMY_OF`, `KNOWS_SECRET`, `GOVERNED_BY_LAW`
- Long lore prose stored as **text properties on nodes, with embeddings** — the graph is the skeleton, prose is the flesh.
- Bootstrap: feed worldbuilding docs to an LLM with an entity/relation extraction prompt that emits graph inserts.

### Dynamic layer (session history) — pure vector RAG
- Session events, player actions, NPC deaths, promises. Appended live (milliseconds, no re-indexing).
- After each scene, the cheap model writes a summary chunk with metadata: `session`, `timestamp`, `entities: [canonical entity IDs]`.
- **Entity IDs are the join key** between the two layers.

### Retrieval: one tool, `lookup_lore(question)`
Internally: extract entities → fetch graph nodes + 1–2 hop neighborhood → vector search (static prose + dynamic chunks filtered by entity ID) → synthesize.
**Recency-override rule (enforced in the synthesis prompt): dynamic facts supersede static canon on conflict.** The dead duke must not host a banquet.

### Chronicler (background job, planned)
Between sessions, promotes settled dynamic facts into the graph (e.g. flip Aldric's node to `deceased`). Does GraphRAG's expensive update asynchronously, where latency doesn't matter.

---

## 2b. Story / Adventure Guide (optional, advisory — decided 2026-07-12)

**Decision: locked.** A user may optionally upload a pre-written adventure (a published module, or their own outline) to guide the narrator's pacing. This is a **different kind of bucket than §2's lore graph**, not an extension of it:

- **Lore (§2) is declarative** — facts that are true about the world (Duke Aldric rules Harrowgate).
- **A story guide is procedural** — the author's intended arc (Act 1: the party discovers the duke's secret). It describes what the writer *hoped* would happen, not what *is* true, and it must never override player agency: *the DM narrates around it, it does not narrate through the players.*

### Beats, not a script
The guide is ingested into an ordered sequence of **beats** (title, summary/read-aloud text, a trigger condition, optionally tagged with lore entity IDs — the same join-key pattern as the dynamic layer in §2), each carrying a status: `upcoming` / `active` / `completed` / `skipped`. Trigger conditions replace a fixed sequence specifically so the party can go off-script without anything breaking — there is no "wrong" order, only beats that haven't happened yet.

### Delivery: proactive context, not a retrieval tool
Unlike `lookup_lore`, which is queried on demand, pacing requires the *current* beat to be visible to the narrator on every turn without it having to think to ask. The orchestrator injects the active beat plus the next one or two upcoming beats into context each turn, framed explicitly as the DM's private notes — advisory, never binding. This is why it's a context-injection mechanism, not a new agent tool.

### Progress: code adjudicates, LLM judges
A single tool, `update_story_progress(beat_id, status)`, mirrors `update_world_state`'s split: the LLM makes the semantic call that a beat has been reached, completed, or skipped by the party's choices; the tool deterministically persists that call. No dice, no gating — nothing about this feature is allowed to constrain what the players can do, only what the DM privately expects to happen next.

### Optional by construction
A campaign with no uploaded guide has no beats and plays exactly as an entirely improvised campaign does today — this feature adds a bucket, it does not change the default.

---

### ⚠️ Open decision: graph technology
| Option | Notes |
|---|---|
| **Neo4j** | Pragmatic default; LLMs write decent Cypher; Aura free tier |
| **Kuzu / FalkorDB** | Embedded, lightweight, good for prototype |
| **RDF + SPARQL (Oxigraph)** | Ontologies + OWL inference; more rigor, more friction |
| **Microsoft GraphRAG (as indexer)** | Could be used for the *one-time static build only* (entity extraction, Leiden community detection, community summaries); its slow-update drawback doesn't apply since the static layer never updates live |

To be decided in a follow-up discussion. The hybrid architecture is fixed regardless of which store/indexer is chosen.

---

## 3. Agent Toolset

| Tool | Purpose | Notes |
|---|---|---|
| `roll_dice(expression)` | e.g. `2d6+3` | Deterministic |
| `character_sheet` (get/update) | PC stats, inventory, HP | Postgres-backed |
| `lookup_rules(query)` | RAG over **SRD 5.1** (Creative Commons) | Separate corpus from lore |
| `lookup_lore(question)` | Hybrid retrieval (§2), single black box to the orchestrator | Recency-override built in |
| `update_world_state` | Writes to dynamic layer + structured state | |
| `update_story_progress(beat_id, status)` | Marks a story-guide beat upcoming/active/completed/skipped (§2b) | Optional — only exists if a story guide was uploaded; LLM judges, tool persists |
| `manage_combat` | Initiative, turn order, positions | Shares coordinate data with maps |
| `generate_art(prompt)` | Diffusion model for portraits/scene art | Portraits generated **once**, URL stored on the graph node for visual continuity |
| `generate_map(spec)` | LLM emits structured JSON (rooms, exits, terrain, tokens) → renderer (SVG/canvas or Foundry VTT format) | Maps are **mechanically meaningful** — map data lives in game state, not just an image |
| `visualize(description)` | Live "concept sketch" of the group's ideas via fast image model | See §6 |

---

## 4. LLM Strategy — Two-Model Setup

- **High-capability model** (Opus/Fable-class): main narration, adjudication decisions, plot-critical NPCs. Long context + strong instruction-following are essential for campaign coherence.
- **Cheap fast model** (Haiku-class): session summarization into memory, player-intent classification, state updates, minor NPCs.
- **Provider: Anthropic API** (preferred). Architecture remains provider-agnostic.

---

## 5. NPC Subagents

When a player addresses an NPC, the orchestrator spawns a **scoped subagent**:

- **System prompt** built from the NPC's graph node: personality, goals, secrets, speech style, faction loyalty.
- **Scoped knowledge** (key feature): only lore the NPC *would know* — their graph neighborhood + dynamic events tagged with them or their location. Information asymmetry for free; the blacksmith cannot spoil the lich's secret.
- **Scoped tools:** at most a restricted `lookup_lore`. NPCs never roll dice or mutate world state.
- **Stateless between conversations:** NPC memory lives in the dynamic store and is reconstructed at spawn time — no NPC-local state that can drift from world state.
- The orchestrator detects dialogue end, summarizes the exchange into the dynamic store ("blacksmith agreed to forge the key, wants 50 gold"), and resumes narration.
- Minor NPCs run on the cheap model; plot-critical NPCs on the big model.

---

## 6. Ambient Experience Layer

**Governing principle: latency budget decides pre-built vs. generated.** A door creak arriving 4 seconds late is worse than silence.

### Pre-built (asset library, instant playback <100 ms)
| Category | Approach |
|---|---|
| **Sound effects** (door creak, sword clash, footsteps, dragon roar) | Curated/licensed library of a few hundred tagged SFX (Freesound, game-asset packs). Client plays the matching asset on event. |
| **Ambient soundscapes** (`tavern_busy`, `crypt_drips`) | Pre-built loops, triggered on location entry. |
| **Spell/effect visuals** (fireball, healing glow, lightning) | Library of particle effects / short loops, overlaid on the canvas map at grid position. Triggered by rules-engine events (e.g. `cast_spell(fireball)` resolves → effect tag in event). VTT-style. |
| Optional: generative audio (ElevenLabs SFX, AudioCraft) | **Offline only**, to pre-generate library assets — never for live playback. |

### Generated live (seconds of latency acceptable)
| Category | Approach |
|---|---|
| **Group-idea sketches** ("raft from the tavern door") | `visualize()` tool → fast image model (Flux Schnell / SDXL Turbo), 5–10 s. Presented as a "concept sketch" so quality expectations stay low — feels like the DM sketching, not lag. |
| **NPC portraits / scene art** | Generated once (at creation / first encounter), then cached and reused. |

### Event stream (the architectural glue)
The narration model's output is an **event stream, not just text**: prose tokens interleaved with typed events —
`sfx`, `ambience`, `effect`, `visual`, `map_update` — delivered to the client over a websocket. The rules engine emits events too (dice results, damage numbers). The frontend is a small **stage** that renders the stream, not a chat window.

---

## 7. Background Workflows (n8n — optional, not v1)

The core agent loop stays in code (latency-sensitive, multi-round tool calls). n8n (or plain cron/Celery/pg_cron) fits only the asynchronous edges:

- Chronicler job (dynamic → graph promotion between sessions)
- Discord notifications, session-recap emails, state backups
- "Rebuild static graph when a new lore doc lands in this folder"

**Decision: skip n8n for v1; add later only if background workflows multiply.**

---

## 8. Stack Summary

| Concern | Choice |
|---|---|
| Backend | Python + FastAPI, Anthropic SDK tool-use loop (LangGraph optional) |
| Structured state | Postgres (SQLite for prototype) |
| Vector store | pgvector or Qdrant |
| Embeddings | Local sentence-transformers (bge-small, 384-dim), in-process — a **required** runtime dependency (lore/rules retrieval and the world build all embed in-process; not an optional extra) |
| Knowledge graph | **Open** — see §2 |
| Rules corpus | SRD 5.1 |
| Story guide | Optional per campaign, `story_beats` table — see §2b |
| Frontend | Web stage (websocket event stream); Discord bot as natural TTRPG channel |
| Hosting | Fly.io / Railway for prototype |

---

## 9. Build Order

1. **Core loop:** text narration + dice + character sheets + structured state
2. **Knowledge layer:** static graph build + dynamic RAG + `lookup_lore` with recency-override, plus optional story-guide ingestion (§2b)
   - **Management & play UX** (added from play sessions; stage + small server additions, no new architecture): campaign/character CRUD + world/adventure upload *(done)*; **M2d** — start menu + visible history on reconnect (session state already persists; the on-screen transcript is replayed, display-only); **M2e** — multi-character party play (party roster injected into the per-turn context + the player picks which character acts). Both bound by the player-agency principle above.
3. **SFX/ambience:** event stream + tagged asset library *(huge immersion win, tiny effort — just tag matching)*
4. **Combat + maps:** `manage_combat` + `generate_map` renderer sharing coordinates
5. **NPC subagents** with scoped knowledge
6. **Map effect overlays**, then **live sketching** (`visualize`)
7. Later: chronicler job, n8n if needed

---

## 10. Open Points for Next Discussion

1. **Graph technology / static indexer** — Neo4j vs. Kuzu vs. RDF, and whether to use Microsoft GraphRAG's pipeline for the one-time static build (§2)
2. Retrieval tool interface details + synthesis prompt
3. Chronicler job design
4. NPC subagent prompt/scoping details
5. Ontology/schema refinement for the world graph
