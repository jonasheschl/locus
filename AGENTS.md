# Locus Wiki maintenance contract

This directory is a personal LLM Wiki. Knowledge is divided into three layers:

- **Manual**: Markdown and spreadsheet notes authored by the owner below `manual/`. Treat them as
  first-party sources. Read freely; do not change them during normal Wiki work. The owner has
  authorized Locus to integrate new, modified, and deleted Manual snapshots into Wiki
  automatically.
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
9. Only `manual/`, `ingest/`, and `wiki/` belong to the knowledge workspace. Never treat files
   below `locus/` as notes or source material.
10. For work spanning many documents or website pages, use the isolated persistent workspace to
    inspect, download, and convert sources as needed. Import original external material through its
    Ingest outbox, inspect every imported source before synthesis, and finish the whole requested
    scope rather than stopping after the first landing page.

This contract is deliberately editable. The owner and agent should refine it as the Wiki develops.
