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

## Cron (example)

```cron
# fully self-contained loop:
# acquire hourly -> integrate -> garden nightly -> synthesize weekly
0 * * * *  brain ingest hermes && brain ingest pai
15 * * * * brain integrate-run --apply
30 3 * * * brain garden --apply
0 4 * * 0  brain synthesize --apply
```

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
