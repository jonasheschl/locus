# Locus Wiki maintenance contract

This directory is a personal LLM Wiki. Knowledge is divided into three layers:

- **Manual**: Markdown authored by the owner outside `ingest/` and `wiki/`. Treat it as a
  first-party source. Read freely; do not change it during normal Wiki work.
- **Ingest**: immutable external source material below `ingest/`. Never rewrite these files.
- **Wiki**: compiled knowledge below `wiki/`. The agent owns and maintains this layer.

## Operating rules

1. For ordinary questions, read `wiki/index.md` first and search Wiki before raw sources.
2. Consult Manual and Ingest to verify provenance, fill gaps, or resolve contradictions.
3. An ingest operation may create or update multiple Wiki pages. Preserve exact source paths in
   `[[double-bracket links]]` and cross-link related Wiki pages.
4. Update `wiki/index.md` after every Wiki-changing operation.
5. Append ingest, filed-query, maintenance, and undo operations to `wiki/log.md`.
6. State uncertainty and contradictions explicitly. Never invent a source, quote, or relationship.
7. Keep pages focused: prefer durable concept, entity, comparison, and synthesis pages over one
   summary page per chat turn.
8. Manual edits are exceptional and require an explicit request naming the note.

This contract is deliberately editable. The owner and agent should refine it as the Wiki develops.
