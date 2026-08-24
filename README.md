# Locus knowledge wiki

Locus is a private, local-first working interface for the knowledge in this directory. It has two
primary pages:

- **Chat** is the default workbench. One conversational agent decides whether it needs to answer
  from the Wiki, inspect raw sources, lint structure, or persist an explicitly requested change.
- **Notes** is an Obsidian-style file explorer. Browse directories, edit Markdown in a visual
  WYSIWYG surface, inspect spreadsheet notes as sheet grids, and preview extracted ingest text.
  Right-click a root, directory, or file to create, import, rename, or delete entries.

The application combines Svelte, FastAPI, SQLite full-text/local similarity search, and a custom
OpenAI Agents SDK agent.

## Start it

Requirements: Docker Engine with Docker Compose.

```bash
cd locus
docker compose up --build
```

Open [http://localhost:7331](http://localhost:7331). The Docker port is explicitly published on
`127.0.0.1`, so the application is not reachable through the machine's LAN address. FastAPI serves
the compiled Svelte application as one origin; there is no separately exposed development server.

In Chrome or Edge, use the **Install app** button in the top bar (or the browser's install icon) to
add Locus to the desktop and launch it in its own window. The app shell and loaded static assets are
available offline; notes, search, and chat still require the local Locus server to be running.
Locus supplies desktop-sized and maskable versions of its own icon. If the app was installed before
those assets were available, uninstall and reinstall it once so Chromium refreshes the operating
system launcher files.

### Neutralino desktop app

The frontend also ships as a lightweight Neutralino desktop application. Keep the Docker Compose
service running so the desktop UI can reach the Locus API at `http://127.0.0.1:7331`, then run:

```bash
cd frontend
npm ci
npm run desktop:update
npm run desktop:dev
```

`desktop:update` downloads the pinned Neutralino runtime binaries and only needs to be rerun when
the runtime version changes. To create release bundles for Linux, macOS, and Windows:

```bash
npm run desktop:build
```

The bundles are written below `frontend/desktop-dist/`. Closing or minimizing the window sends it
to the system tray; use **Open Locus** to restore it and **Quit Locus** to exit. The native API is
restricted to tray creation, window show/hide/focus, application exit, and writing the running
app's own `resources.neu` update bundle. General filesystem reads/writes, shell, environment,
clipboard, storage, extensions, and other system APIs are not exposed.

The desktop app checks the latest GitHub Release when it starts. If that release has a newer
version, it downloads and installs `resources.neu`, then offers to quit so the update is loaded on
the next launch. Publish a release by pushing a semantic-version tag:

```bash
git tag v1.0.1
git push origin v1.0.1
```

The desktop release workflow builds the cross-platform package and publishes `update.json`,
`resources.neu`, and the portable release ZIP. These assets must be publicly downloadable; if the
repository is private, configure `VITE_UPDATE_MANIFEST_URL` to use a public update host instead.
Neutralino's built-in updater replaces the app resources, not the Neutralino runtime executable;
runtime upgrades still require replacing the portable package.

To stop it:

```bash
docker compose down
```

## Three knowledge spaces

### Manual

`../manual/` contains the notes you author. Markdown is edited visually while remaining Markdown
on disk. ODS, XLSX, and CSV files can be imported from the Manual tree and are rendered as local
sheet grids; their cell text is included in search and is available to the chat agent.

The agent may read Manual freely, but its instructions and tools only permit changing an existing
Manual note after an explicit request from you.

Locus checks Manual integration state at least once per minute. New and modified notes are batched
into the same agent workflow used for explicit Wiki updates; deleted notes trigger a review of Wiki
claims that depended on them. Successful snapshot hashes are stored in `wiki.sqlite3`, so unchanged
notes are not repeatedly sent to Codex. Failed runs—such as while Codex is logged out—remain pending
and retry on a later interval.

### Ingest

`../ingest/` contains raw external inputs. The Chat page can add:

- PDF
- Markdown and text
- HTML/web exports
- CSV and JSON
- Downloaded HTTP/HTTPS sources

PDF and text-like content is extracted locally for search and agent reading. Downloaded HTML is
converted to structured Markdown locally with Docling, while the immutable original HTML remains
in Ingest. Each ingestion event receives its own folder. URLs added through the source menu—or
included directly in a chat message—are downloaded as their original HTML, PDF, text, CSV, or JSON
representation. If the agent downloads related pages for situational awareness, those enrichment
files are routed into the original source's folder instead of a global agent-download directory.
The Notes browser timestamps these ingest folders and orders them newest first. Redirects are
checked, downloads are limited to 50 MB, and local/private-network targets are rejected. Reusing
the same URL attaches the already stored source rather than creating another copy.

Ingest is immutable through the note editor and through the agent. To replace a source, change the
raw file outside Locus or add a newer source.

Adding or attaching an unprocessed source opens a **Source discussion** stage. Those turns can read
the source and relevant Wiki pages but have no Wiki or Manual write tools. Use the conversation to
decide what to emphasize, challenge, retain, omit, or reinterpret; Locus preserves the attachment
when the thread is reopened. Choose **Ready to integrate** only when that direction is settled. The
next turn can then update every relevant Wiki page, after which the normal receipt shows a concise
change list and undo. **View raw diff** remains available on the receipt when exact edits matter,
but reviewing diffs is not required for the normal workflow.

### Wiki

`../wiki/` contains synthesized, interlinked Markdown pages. This is the agent's normal write
target when you ask it to integrate sources, consolidate topics, or maintain the wiki. Wiki pages
are expected to link their Manual/Ingest provenance and related Wiki pages using exact relative
paths. You can still inspect and edit them directly from Notes when needed.

`locus/AGENTS.md` is the editable maintenance contract bundled into the application.
`wiki/index.md` is rebuilt after every Wiki-changing agent operation, while `wiki/log.md` is an
append-only operational history. Locus creates the three knowledge roots and special Wiki files
when they are missing.

## One agent, autonomous workflow

There are no chat modes and no agent switcher. Every conversation starts with the compiled
`wiki/index.md`. The same agent can answer from the Wiki, return to Manual/Ingest for evidence,
review an attached source, lint the knowledge structure, or carry out an explicitly requested
integration or repair.

For longer jobs, that same agent also has a standard shell in a persistent Docker workspace. It
can inspect large sets of notes, download multi-page sources, run Docling, and keep intermediate
artifacts across tool calls. The runtime sees Manual, Ingest, and Wiki read-only and has no access
to the Locus source tree, SQLite credentials, or the main application network. Completed external
files leave the workspace through an Ingest outbox; Locus validates, imports, and indexes them,
while Wiki changes continue through the normal audited write operation. Agent runs may use up to
64 model turns rather than stopping after a short fixed sequence.

Reading, questioning, and source review do not create write operations. A tracked operation begins
lazily only when the agent actually invokes a write tool. It returns a receipt containing sources
and every created or updated path. Raw unified diffs are loaded only when requested. **Undo
operation** restores the exact pre-operation contents, but refuses to overwrite a newer human or
agent edit. Manual edits remain exceptional and require a request that explicitly names the note.

## Index and storage

- Changes made in another editor are detected every few seconds. Locus writes Markdown atomically.
- Only `manual/`, `ingest/`, and `wiki/` are indexed; the neighboring `locus/` application is
  structurally outside the knowledge spaces.
- Search scans all three spaces and combines SQLite FTS5 with deterministic 256-dimensional local
  hash embeddings.
- PDF extraction, indexes, embeddings, links, chat history, and Codex credentials are stored in
  `./wiki.sqlite3`. The database is created with user-only (`0600`) permissions.
- Operation snapshots, provenance, source integration status, and undo metadata are also stored in
  that same SQLite file. No sidecar cache directory is used.
- Application and database files stay below `locus/` and are excluded from indexing by design.
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
WIKI_MODEL=gpt-5.5 WIKI_SCAN_INTERVAL=5 WIKI_MANUAL_INTEGRATION_INTERVAL=60 docker compose up --build
```

The selected model must be available to the signed-in account.

`WIKI_MANUAL_INTEGRATION_INTERVAL` defaults to 60 seconds and is capped at 60 seconds so Manual
changes are always considered at least once per minute.

The sidebar **Settings** dialog can override the model and reasoning effort, enable Fast mode, and
show the connected Codex account's current allowance windows, reset times, and credit status.
Fast mode requests Codex priority processing independently of reasoning effort. Locus sends both
the `service_tier=priority` payload field and Codex priority routing hint. Choices are stored in
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

The Git repository itself lives in `locus/`. The sibling knowledge spaces are therefore outside
source control, while `wiki.sqlite3` remains ignored inside the repository. Only application code
and the editable `AGENTS.md` contract belong in source control by default.
