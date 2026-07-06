# brain-tools

CLI + MCP tooling for a personal knowledge brain. Data lives in a separate
vault repo (see `SPEC.md`); this repo only contains logic.

## Setup

```bash
# 1. Put the vault somewhere and init git
mv brain-vault ~/brain && cd ~/brain && git init && git add -A && git commit -m init

# 2. Install the tools (uv)
cd brain-tools
cp brain.toml.example brain.toml   # set vault.path = "~/brain"
uv venv && uv pip install -e '.[all]'

# 3. Smoke test
brain lint
brain ingest manual ~/notes/some-file.md
brain status
brain search "some topic"
brain garden --no-llm
```

## Commands

- `brain ingest <manual|sessions|hermes|pai> [targets…]` — stage raw sources (phase 1)
- `brain status` — integration queue
- `brain integrate-run [--apply]` — internal integration agent: LLM turns staged raw into wiki pages
- `brain integrate origin:id` — mark a raw item integrated (external agents call this)
- `brain lint [--json]` — mechanical checks; exit 1 on issues
- `brain search <query> [-n N] [--tag t] [--semantic]`
- `brain reindex` — rebuild FTS index
- `brain review [--clear <page>]` — queue of pages agents flagged as uncertain
- `brain garden [--no-llm] [--apply] [--synthesis]` — curation run (cron this)
- `brain synthesize [--apply]` — weekly whole-vault review page (premium model)
- `brain apply-plan --kind <integrate|curate> [--key origin:id]` — apply an external agent's JSON plan through the brain's validators
- `brain serve` — MCP server (stdio)

## MCP

```json
{ "mcpServers": { "brain": { "command": "brain", "args": ["serve"] } } }
```

## Acquisition modules (pull, never push)

The brain FETCHES from agent memories on its own schedule; agents never write
into the vault. Modules implement `fetch()` (side-effect free, yields items);
the runner stages into `raw/<module>/` with provenance frontmatter and
content-hash dedup. Living source files restage automatically when they
change — the integrator folds updates in (never deletes silently).

| Module | Pulls | Item granularity |
|--------|-------|------------------|
| `hermes` | Hermes agent memory (`SOUL.md`, `memories/*.md`) | one item per `##` section, verbatim |
| `pai` | PAI memory: completed work ISAs, learning reflections, knowledge notes, telos, project state | file per item; jsonl per line |
| `sessions` | Claude Code transcripts (via SessionEnd hook) | digest per session |
| `manual` | anything you point it at | file |

Configure roots and globs in `brain.toml` (`[ingest.hermes]`, `[ingest.pai]`).
A missing source root is a stderr warning + zero items, never an error — cron
chains keep running while a source isn't wired up yet.

**Remote sources:** if an agent lives on another machine, sync its memory tree
here first, in the same cron slot: e.g.
`rsync -az otherhost:~/.hermes/ ~/.hermes/ && brain ingest hermes`.

## Cron (example)

```cron
# fully self-contained loop:
# acquire hourly -> integrate -> garden nightly -> synthesize weekly
0 * * * *  brain ingest hermes && brain ingest pai
15 * * * * brain integrate-run --apply
30 3 * * * brain garden --apply
0 4 * * 0  brain synthesize --apply
```

Acquisition lines are safe to install anytime (stage-only, no LLM). Hold the
`--apply` lines until the LLM passes have produced sane plans in dry-run.

## Playbooks (pointing external agents at the brain)

The vault's `playbooks/` directory lets any agent run the LLM passes instead
of the internal API calls — same prompts, same validated apply path. E.g.
integration via Claude Code on a subscription instead of metered API:

```bash
cd path/to/brain-vault && claude -p "$(cat playbooks/integrate.md)"
```

Edit pass behavior by editing the playbooks; the internal passes load their
system prompts from the same files.

## Claude Code session hook

Every coding session auto-stages its transcript into `raw/sessions/`:
see `hooks/session_end.sh` for the ~/.claude/settings.json snippet.
