"""Knowledge layer (M2): the hybrid static-canon graph + dynamic session RAG (§2).

- `embeddings` — EmbeddingProvider interface + sentence-transformers impl.
- `store` — GraphStore: node lookup, neighborhood expansion, vector search.
- `extract` — Opus structured-output extraction (worldbuilding markdown → typed graph).
- `communities` — Leiden clustering + Haiku community summaries.
- `retrieval` — the lookup_lore pipeline with the recency-override rule.
- `summarizer` — scene summarizer that appends dynamic chunks as play happens.
- `build` — the static-build CLI wiring extraction → embed → communities → summaries.
"""
