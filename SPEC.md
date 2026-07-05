# Personal Brain — Specification v0.5

## 1. Purpose

A personal knowledge system where an LLM-maintained wiki (Karpathy's llm-wiki
pattern) sits on top of immutable raw sources (Open Knowledge Format
conventions), with tooling that lets both the human and agents ingest, lint,
search, and continuously curate the knowledge base. The wiki should compound in
value over time through automated gardening.

## 2. Architecture

Two repositories with a strict data/logic split:

| Repo | Role | Writable by |
|---|---|---|
| `brain` (vault) | Data: `raw/` + `wiki/` + `AGENTS.md` | raw/: human & ingest modules · wiki/: agents & gardener |
| `brain-tools` | Logic: CLI, MCP server, modules | developer |

`brain-tools` never hardcodes paths; it resolves the vault via `brain.toml`
(`$BRAIN_CONFIG` → `./brain.toml` → `~/.config/brain/brain.toml`).

### Vault layout

```
brain/
├── AGENTS.md            # agent operating manual (schema + workflows)
├── raw/                 # immutable, provenance-stamped sources
│   ├── manual/  sessions/  hermes/  pai/  assets/
├── wiki/                # OKF wiki, agent-owned
│   ├── index.md         # human section + AUTO-INDEX block (tool-generated)
│   ├── log.md           # append-only: YYYY-MM-DD [actor] message
│   ├── entities/  clients/  concepts/  sources/  syntheses/
└── .brain/              # generated state, gitignored
    ├── search.db        # FTS5 index
    └── manifest.json    # staged raw files + integration flags
```

### Page schema (OKF-derived)

Frontmatter required: `type` (entity | concept | source | synthesis | client |
index | log | moc), `title`, `description`, `timestamp`, `tags` (≥1 domain
tag: personal | business | research). Optional fields: `expires:`
(time-limited intelligence), `summary:` (one-line distillation, skimmed by
synthesis/search instead of full bodies), `status:` (active | dormant |
archived — PARA-derived lifecycle as frontmatter, not folders; archived is
the never-delete-silently end state and is exempt from staleness lint),
`kind:` (area | project on business pages — ongoing responsibility vs.
finish-line work, enabling different review cadences), and `review:` (set by
autonomous passes on uncertain judgment calls; surfaced by `brain review` /
`brain_review_queue`, cleared with `brain review --clear`). Invalid `status`
/ `kind` values fail lint. Wiki pages carry `sources:` pointing at raw paths. Raw files
carry `origin`, `origin_id`, `content_hash`, `retrieved_at`. Single wiki;
domains are separated by tags, not directories — the system is agnostic
between personal and business use, and `clients/` pages anchor the business
side (relationship history, preferences, open threads, decision outcomes).

### Autonomy model

Everything runs from cron inside brain-tools: deterministic passes (ingest
acquisition, lint, search indexing, mechanical gardening) need no LLM; LLM
passes (integrator, curator, synthesis) call the Anthropic API directly with
per-pass model tiering. External agents are optional consumers, never
required for operation. The only outward-living component is the Claude Code
SessionEnd hook, a data feeder.

## 3. Functional components

### 3.1 Ingest (two-phase)

**Phase 1 — Acquisition (deterministic, cron-safe).** Source modules implement
`fetch() -> Iterator[RawItem]`; the runner normalizes to markdown, stamps
provenance, dedups by content hash against `manifest.json`, writes to
`raw/<origin>/`, and never touches `wiki/`. Modules: `manual` (files/dirs/
URLs), `sessions` (Claude Code transcripts via the SessionEnd hook in
`hooks/session_end.sh` — digests user/assistant turns into a dated note),
`hermes` (agent memory), `pai` (Miessler PAI memory); new sources are added
by subclassing `SourceModule`.

**Phase 2 — Integration (LLM).** Two interchangeable drivers converging on
the same manifest flag, so they never double-process:

- **Internal (default for a standalone brain):** `brain integrate-run` — the
  built-in integration agent. Per queued item it finds related pages via
  keyword search, requests a strict JSON plan from the model (update
  existing pages preferred; create sparingly; receipts in `sources:`;
  `expires` on time-sensitive claims; skip low-value items), validates every
  op (wiki-only paths, schema-checked frontmatter, index/log untouchable,
  per-item page cap), applies, logs, marks integrated, and reindexes.
  Every integrated item gets its own git commit — one reviewable,
  individually revertible diff per autonomous change. Uncertain judgment
  calls are integrated anyway but flagged with `review` frontmatter rather
  than stalling the batch. dry_run defaults on.
- **External (optional):** any agent over the MCP server following the
  AGENTS.md workflow, ending with `brain_mark_integrated`.

The brain is therefore fully self-contained: acquisition, integration,
curation, and synthesis all run from cron with its own LLM calls; MCP is a
query/collaboration surface, not a dependency.

### 3.2 Lint (two-tier)

**Mechanical (`brain lint`, exit code 1 on issues):** frontmatter schema,
broken wikilinks, orphan pages, duplicate titles, staleness (>180 days),
expired intelligence (`expires` date passed), index drift. Deterministic and
free; suitable for git hooks and CI.

**Semantic:** contradictions, coverage gaps, quality — performed by the
gardener's LLM pass, which consumes the mechanical report as input.

### 3.3 Search

Pluggable backend behind one interface (`brain search`, `brain_search`):

- `keyword` (default): SQLite FTS5 with BM25 ranking, porter stemming,
  snippets, tag filtering. Zero external dependencies.
- `qmd`: subprocess wrapper around the qmd CLI for BM25 + vector + hybrid
  reranked search (`--semantic` flag maps to vsearch).
- Future: native embedding pipeline (sqlite-vec + API/local embedder) can
  replace the qmd wrapper without changing callers.

`brain reindex` rebuilds; the gardener reindexes after every run.

### 3.4 Gardener (compounding curation, cron-scheduled)

`brain garden`, intended for cron (e.g. nightly). Three passes with model
tiering — routine work on the cheap tier (`curator_model`, Haiku-class),
whole-vault review on the premium tier (`synthesis_model`):

**Pass A — Mechanical (always runs, free):** regenerate the AUTO-INDEX block
of index.md, rebuild search index, produce a fresh lint report, log the run.

**Pass B — Curator (LLM, config-gated):** builds a prompt from the lint
report, the full list of valid page stems, and the 15 least-linked pages
(they benefit most). The model returns a strict JSON plan:

- `add_links` — inject [[wikilinks]] into existing prose (exact find/replace)
- `new_pages` — entity/concept stubs for recurring people, orgs, ideas
- `fix_broken_links` — retarget to an existing page or unlink

Guardrails: `dry_run` default on (plan printed, nothing written); hard caps
`max_pages_edited_per_run` (10) and `max_new_pages_per_run` (3); operations
validated before applying (targets must exist, types must be legal); raw/,
log.md history, and paths outside wiki/ are unwritable; every applied op is
logged to log.md; the vault is git-committed per run so `git revert` undoes
any run. Compounding comes from small bounded runs: each run densifies links
and extracts entities, which improves the next run's graph signal.

**Pass C — Synthesis (premium model, weekly):** `brain synthesize` or
`brain garden --synthesis`. One whole-vault read producing exactly one dated
page (`syntheses/weekly-YYYY-WW.md`, idempotent per ISO week): what changed,
emerging themes, contradictions/drift, expiring intelligence to re-verify,
and 3–5 suggestions for what to add next. Never edits existing pages.

### 3.5 Playbook layer (prompt pack)

`<vault>/playbooks/{integrate,curate,synthesize}.md` are the single source
of truth for LLM-pass behavior. Each playbook contains (a) the system prompt
between `<!-- BEGIN/END SYSTEM PROMPT -->` markers, loaded at runtime by the
internal Python passes (fallback to built-in constants if absent), and (b) a
workflow for pointed external agents: context-gathering steps using brain
tools, the JSON plan schema, and submission via `brain apply-plan` /
`brain_apply_plan`. Plans from any producer flow through the identical
validated apply path (path guards, schema checks, caps, provenance,
manifest, log, git commit) — guardrails are enforced by code, not requested
by prompt. This makes the LLM passes executor-agnostic: internal API calls,
Claude Code headless (`claude -p "$(cat playbooks/integrate.md)"` on a
subscription instead of metered API), or any MCP-connected agent.
Concurrency between executors is serialized by lockfiles
(`.brain/lock.<pass>`, 120-min stale threshold); the manifest flag
additionally prevents double-integration of individual items.

### 3.6 MCP server

`brain serve` (stdio, FastMCP). Tools: `brain_search`, `brain_get_page`,
`brain_get_neighbors` (navigate the link graph cheaply before reading
bodies), `brain_lint`, `brain_status`, `brain_read_raw`, `brain_write_page`
(wiki-only, path-escape guarded), `brain_log`, `brain_mark_integrated`,
`brain_get_playbook`, `brain_apply_plan`.
CLI and MCP call the same functions — one implementation, two surfaces.

## 4. Non-functional requirements

- Vault must remain a plain markdown tree: readable in Obsidian, greppable,
  future-proof without the tooling.
- Derived-projection contract: nothing outside `raw/` and `wiki/` is a
  source of truth; `.brain/` and any future exports are disposable
  projections, reconstructible by deleting and rerunning.
- Ingest is idempotent: dedupe on (origin, origin_id, content_hash) in the
  manifest — re-running a module never duplicates unchanged items.
- Both repos under git; gardener writes are commit-isolated for rollback.
- Python ≥3.11, managed with uv; only deps: click, python-frontmatter,
  pyyaml; optional extras: mcp, anthropic.

## 5. Agent workflows (codified in vault AGENTS.md)

- **Integration:** read staged raw → update/create pages → receipts +
  timestamps → log → mark integrated → lint.
- **Research intake (skeptic pattern):** every claim carries a receipt
  (source + date); contested claims get fresh-context adversarial
  verification before landing; time-sensitive claims get `expires`.
- **Session mining:** extract decisions, caught mistakes, preferences, and
  recurring patterns from session digests; most sessions yield nothing.
- **Client pages:** business deliverables open by reading the client page;
  client-relevant work updates it.

## 6. Roadmap

1. **v0.1:** core, lint, FTS5 search, manual ingest, MCP server, gardener
   with dry-run curation.
2. **v0.2:** sessions ingest + SessionEnd hook, expiry lint, weekly
   synthesis pass, model tiering, client pages, skeptic workflow.
3. **v0.3:** internal integration agent (`brain integrate-run`) — fully
   self-contained operation.
4. **v0.4:** playbook layer — vault-editable prompts shared by internal
   passes and pointed agents, `apply-plan` shared apply path, run lockfiles.
5. **v0.5 (this scaffold):** PARA-derived lifecycle frontmatter (status /
   kind / summary), per-item git commits, review flags + `brain review`,
   derived-projection contract.
6. **v0.6:** wire hermes/pai adapters to real schemas; systemd timer
   examples; gardener metrics (link density, orphan count over time) charted
   from log.md.
7. **v0.7:** semantic search — qmd as default or native sqlite-vec pipeline;
   embedding cache in `.brain/`.
8. **v0.8:** semantic lint pass (contradiction detection) as a distinct
   gardener stage; per-page curation history.

## 7. Open decisions

- Gardener LLM provider is Anthropic API for now; could be routed through a
  local model or Claude Code headless (`claude -p`) instead.
- Whether gardener pass B should open a PR-style branch instead of committing
  to main once the vault is synced to a remote.
