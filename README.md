# itools

**Internal Developer Tools** — a modular Python command-line toolkit for Jira, Bitbucket, Nexus and other internal services.

Phase 1 implements **Jira Server/Data Center Excel WBS issue import**, with a shared CLI/configuration layer. Other service namespaces will be added in later phases.

## Install

Requires Python **3.11+**.

```bash
python -m pip install -e .
# or: uv pip install -e .
itools --help
```

For development: `python -m pip install -e '.[dev]'` and `pytest -q`.

### Offline / air-gapped installation

On an internet-connected machine with a matching Python/platform, prepare wheels:

```bash
python -m pip wheel --wheel-dir wheelhouse .
```

Copy `wheelhouse/` to the offline machine, then:

```bash
python -m pip install --no-index --find-links wheelhouse itools
```

Keep the wheelhouse for the target operating system / Python version. No token or internal URL belongs in a wheel.

## Configure Jira

```bash
itools config init
# edit ~/.config/itools/config.toml
export ITOOLS_JIRA_TOKEN='your-data-center-PAT'
itools jira ping
itools jira doctor -p DEV
```

Example `~/.config/itools/config.toml`:

```toml
[profiles.internal.jira]
url = "https://jira.internal"
auth = "pat"
token_env = "ITOOLS_JIRA_TOKEN"
# ca_bundle = "/etc/ssl/certs/internal-ca.pem"
```

Also supported: `auth = "basic"` using `ITOOLS_JIRA_USERNAME` and `ITOOLS_JIRA_PASSWORD`. Environment variable `ITOOLS_JIRA_URL` overrides the profile URL; `--profile NAME` selects a different named profile; `--config PATH` overrides the config path. Tokens are **not** stored in the configuration or import report. TLS verification is on by default.

## Phase 1 commands

```bash
itools jira ping
itools jira doctor -p DEV
itools jira field list --name Epic
itools jira link-type list

itools jira issue template -o planning.xlsx
itools jira issue template -o example.xlsx --example
itools jira issue validate planning.xlsx
itools jira issue validate planning.xlsx --format json
itools jira issue import planning.xlsx -p DEV --dry-run
itools jira issue import planning.xlsx -p DEV
itools jira issue import planning.xlsx -p DEV --resume
itools jira issue import planning.xlsx -p DEV -y -o import-report.json
```

**Recommended workflow:** generate a blank template (or use the agreed 11-column WBS workbook) → plan/edit → local `validate` → connected `--dry-run` → `import`. A dry-run never creates issues, links, or a checkpoint.

## Excel file contract

Single planning sheet; the reader locates the header by `WBS`, `Summary`, `Issue Type`, so title rows above it are fine. The 11 columns are:

| WBS | Summary | Issue Type | Assignee | Priority | Original Estimate (h) | Due Date | Linked Issues | Component/s | Labels | Description |
|---|---|---|---|---|---|---|---|---|---|---|

- WBS **must be formatted as text**: `1` = Epic, `1.1` = Task, `1.1.1` = Sub-task. No zero parts or deeper levels. No explicit Parent/Epic Link column; the importer infers ancestry from WBS, never from Excel Group/Outline metadata.
- In a single Linked Issues cell, use **one relationship per line**, e.g.:

```text
blocks: 1.2, 1.3
is blocked by: DEV-125
relates to: 1.1.2
```

- WBS targets refer to rows in the same workbook; Jira keys refer to already-existing Jira issues. Relationship labels must match the outward/inward strings configured on your server (inspect with `itools jira link-type list`). Inverse duplicates are coalesced before writing.
- `Original Estimate` is **entered effort**, not a roll-up; numbers mean hours (`1.5` → `1h 30m`). Jira durations such as `2d 4h` also work. This field requires Time Tracking to be enabled and available on the create screen; legacy time tracking configuration may not accept it.
- Due Date accepts Excel dates, `YYYY-MM-DD` and `DD/MM/YYYY`.
- `Assignee` uses **Jira Server/DC username**, not Jira Cloud account ID. Comma-separated `Component/s` and `Labels` are supported. Preflight verifies priority/component names.
- For Jira Software Server/DC, Epic Name is filled from Summary and Task Epic Link is discovered dynamically using `GET /rest/api/2/field`. Sub-task uses `parent.key`. Required custom fields or screens beyond those represented here must be configured on Jira.

## Safety and resume

The importer validates the entire workbook locally and checks project, issue types, issue link relationships, priorities, components and referenced external Jira keys **before issuing any creates**.

Issues are created in dependency order: **Epic → Task → Sub-task**, then all issue links. After each success, the tool saves a local JSON checkpoint beside the input, named `planning.xlsx.itools-state.json`. A repeated import refuses to start unless `--resume` is passed, and resume checks that previously-created WBS rows have not changed. It also checks Jira for previously-created links before adding missing links.

**Important:** Jira writes are *not transactional*; no automatic rollback is possible. A power failure in the narrow gap between Jira accepting a new issue and writing its local checkpoint may require manual reconciliation. Do not delete the checkpoint or change already imported WBS identifiers. Copy the checkpoint when moving the Excel file to another machine. Import **only creates**; editing already-created issues is a future `issue sync` feature. `--yes` skips the confirmation and should only be used in controlled automation.

Phase 1 targets **Jira Server/Data Center**, not Jira Cloud. A real Jira instance is needed for integration testing; repository tests use a mock Jira REST server and do not modify production data.

## Source layout

```text
src/itools/
  cli.py                 # root/service/resource/action command routing
  core/config.py         # profiles and env credentials
  core/http.py           # authenticated Jira REST client
  jira/models.py         # 11-column workbook parser, WBS validation
  jira/service.py        # remote metadata/preflight and payload mapping
  jira/importer.py       # checkpoint, ordered issue/link imports
  jira/template.py       # styled Excel planning template
tests/                  # Excel parser, CLI and mock Jira tests
```

Later: `itools jira issue get/search/export/sync`, Bitbucket, Nexus, Jenkins, shared cross-service workflows. Core functionality remains independent of the CLI to support scripted or MCP-based integrations.
