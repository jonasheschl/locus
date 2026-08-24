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
11. Commit and push coherent, verified change sets regularly during longer maintenance sessions.
    Do not leave substantial completed application or schema work only in the local worktree.

## Source integration workflow

1. Adding or attaching an Ingest source starts a review; it does not authorize Wiki changes.
2. Read the source and the Wiki pages it may affect, then discuss its main contribution, evidence,
   limitations, contradictions, and plausible impact with the owner. Ask what to emphasize,
   challenge, keep, leave out, or reinterpret instead of treating the source as automatically true.
   If situational awareness requires downloading related pages or other enrichment material, keep
   every acquired file in the same Ingest folder as the original source. One folder represents one
   ingestion event; never create a separate global enrichment or agent-download folder.
3. Keep the review conversational for as many turns as the owner wants. Carry their editorial
   decisions forward as working context. Raw diffs are optional and should never be required for
   the owner to guide the integration.
4. Integrate only after the owner explicitly moves the review into integration or explicitly asks
   to skip review. Apply the agreed direction across every relevant durable concept, entity,
   comparison, and synthesis page; one source may legitimately affect many pages.
5. Let Locus rebuild `wiki/index.md`, append `wiki/log.md`, and return a concise operation receipt.
   Offer the tracked raw diff for inspection on demand, while keeping summary-level review and undo
   as the normal workflow.
6. Batch review or integration is allowed when explicitly requested, but default to one source at a
   time so the owner can steer what the Wiki learns.

This contract is deliberately editable. The owner and agent should refine it as the Wiki develops.
