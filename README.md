# Locus knowledge wiki

Locus is a private, local-first working interface for the knowledge in this directory. It has two
primary pages:

- **Chat** is the default workbench. One conversational agent decides whether it needs to answer
  from the Wiki, inspect raw sources, lint structure, or persist an explicitly requested change.
- **Notes** is an Obsidian-style raw file explorer. Browse directories, render Markdown, inspect
  source text, edit authored notes, and preview extracted text from ingest files. Right-click a
  root, directory, or file to create, rename, or delete entries.

The application combines Svelte, FastAPI, SQLite full-text/local similarity search, and a custom
OpenAI Agents SDK agent.

## Start it

Requirements: Docker Engine with Docker Compose.

```bash
docker compose up --build
```

Open [http://localhost:7331](http://localhost:7331). The Docker port is explicitly published on
`127.0.0.1`, so the application is not reachable through the machine's LAN address. FastAPI serves
the compiled Svelte application as one origin; there is no separately exposed development server.

To stop it:

```bash
docker compose down
```

## Three knowledge spaces

### Manual

Manual is the Markdown you authored. Existing Markdown files and directories stay exactly where
they are and are classified as Manual unless they live below `ingest/` or `wiki/`. Nothing is moved
during migration. New Manual notes also use the relative path you choose.

The agent may read Manual freely, but its instructions and tools only permit changing an existing
Manual note after an explicit request from you.

### Ingest

`./ingest/` contains raw external inputs. The Chat page can add:

- PDF
- Markdown and text
- HTML/web exports
- CSV and JSON
- HTTP/HTTPS bookmarks

PDF and text-like content is extracted locally for search and agent reading. A URL is currently
stored as a Markdown source bookmark under `ingest/web/`; Locus does not silently fetch the page.
That keeps the acquisition policy explicit while downloaded or exported pages can be uploaded.

Ingest is immutable through the note editor and through the agent. To replace a source, change the
raw file outside Locus or add a newer source.

### Wiki

`./wiki/` contains synthesized, interlinked Markdown pages. This is the agent's normal write
target when you ask it to integrate sources, consolidate topics, or maintain the wiki. Wiki pages
are expected to link their Manual/Ingest provenance and related Wiki pages using exact relative
paths. You can still inspect and edit them directly from Notes when needed.

`AGENTS.md` is the editable maintenance contract. `wiki/index.md` is rebuilt after every
Wiki-changing agent operation, while `wiki/log.md` is an append-only operational history. Locus
creates all three automatically when they are missing.

## One agent, autonomous workflow

There are no chat modes and no agent switcher. Every conversation starts with the compiled
`wiki/index.md`. The same agent can answer from the Wiki, return to Manual/Ingest for evidence,
review an attached source, lint the knowledge structure, or carry out an explicitly requested
integration or repair.

Reading, questioning, and source review do not create write operations. A tracked operation begins
lazily only when the agent actually invokes a write tool. It returns a receipt containing sources
and every created or updated path. **Undo operation** restores the exact pre-operation contents,
but refuses to overwrite a newer human or agent edit. Manual edits remain exceptional and require
a request that explicitly names the note.

## Index and storage

- Changes made in another editor are detected every few seconds. Locus writes Markdown atomically.
- Search scans all three spaces and combines SQLite FTS5 with deterministic 256-dimensional local
  hash embeddings.
- PDF extraction, indexes, embeddings, links, chat history, and Codex credentials are stored in
  `./wiki.sqlite3`. The database is created with user-only (`0600`) permissions.
- Operation snapshots, provenance, source integration status, and undo metadata are also stored in
  that same SQLite file. No sidecar cache directory is used.
- The implementation folders, hidden folders, this README, and unsupported files outside
  `ingest/` are excluded from indexing.
- Space roots plus `wiki/index.md` and `wiki/log.md` are protected from rename/delete. Folder
  deletion is limited to empty folders so unrelated files cannot disappear recursively.

Local embeddings do not require an embedding API. For an agent request, only the prompt, recent
chat context, and the source content selected by the agent's scoped tools are sent to Codex.

## Codex sign-in

Choose **Connect Codex** in the sidebar. Locus starts OpenAI's device-code flow, displays a
one-time code, and polls until authorization completes. No API key is required.

The implementation follows Pi's OpenAI Codex flow: it uses the public Codex client ID, OpenAI's
device authorization endpoints, automatic token refresh, and the ChatGPT Codex Responses backend
with the account ID carried by the token.

This is intended for one person's local use with their own ChatGPT/Codex subscription—not for a
multi-user or resold service. ChatGPT subscription OAuth is separate from the OpenAI Platform API
key contract and can change. The compatibility boundary lives in `backend/app/auth.py` and
`backend/app/agent_service.py`.

Credentials are never returned to the browser or written to logs, but they are kept in the local
SQLite database so restarts do not require another login. Protect `wiki.sqlite3` as a credential
file.

## Configuration

```bash
WIKI_MODEL=gpt-5.5 WIKI_SCAN_INTERVAL=5 docker compose up --build
```

The selected model must be available to the signed-in account.

The sidebar **Settings** dialog can override the model and reasoning effort, enable Fast mode, and
show the connected Codex account's current allowance windows, reset times, and credit status.
Fast mode requests priority processing independently of reasoning effort. Choices are stored in
`wiki.sqlite3`, take effect on the next agent turn, and survive container restarts. The environment
model remains the fallback when no runtime choice has been saved.

Usage is read with the same OAuth session from the account endpoint used by Codex. That endpoint is
not a stable public Platform API, so the usage panel degrades gracefully if it is unavailable or
its response changes; it never blocks the rest of Settings or an agent turn.

## Development and verification

Run the same containerized backend suite used for delivery:

```bash
docker build --target test -t locus-wiki-test .
docker run --rm locus-wiki-test
```

Frontend checks:

```bash
cd frontend
npm ci
npm run check
npm run build
```

Useful endpoints include `/api/health`, `/api/docs`, `/api/files`, `/api/search`,
`/api/ingest/upload`, `/api/ingest/url`, `/api/wiki/lint`, `/api/wiki/schema`,
`/api/operations/{id}/undo`, `/api/settings`, `/api/auth/codex/usage`,
`/api/files/move`, and `/api/index/refresh`.

The repository ignore rules intentionally exclude personal Markdown, spreadsheets, `ingest/`,
`wiki/`, and `wiki.sqlite3`. Only application code and the editable `AGENTS.md` contract belong in
source control by default.
