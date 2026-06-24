# Open Source Release Boundary

Doctor separates source code from local generated data.

## Commit

- source code under `src/`
- tests under `tests/`
- reusable docs under `docs/`
- sample fixtures under `fixtures/`
- example config files such as `config/wiki_projects.example.json`

## Do Not Commit

- `catalog-shards/`
- `indexes/`
- `vault/`
- `manifests/`
- `extracted/`
- `packs/`
- `runtime/`
- private config such as `config/wiki_projects.json`

These folders may contain local filesystem metadata, private document excerpts,
generated context packs, or machine-specific state.

## Local Project Inventory

Create a private project inventory by copying the example:

```bash
cp config/wiki_projects.example.json config/wiki_projects.json
```

Edit `config/wiki_projects.json` so each entry points to a local project you want
Doctor to compile into the OKF vault.

Then run:

```bash
uv run ./agent-context wiki \
  --out . \
  --action baseline \
  --approve
```

The generated `vault/` remains local by default.
