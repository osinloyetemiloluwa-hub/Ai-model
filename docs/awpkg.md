# AWPKG — Portable Workflow Package Format

> **Plugin:** `core/awpkg/` ·
> **Status:** Phase 1 implemented (local install/remove/build, no registry)

AWPKG is the distribution format for Corvin workflow bundles. A `.awpkg` file
is a ZIP archive that bundles AWP workflow DAGs, Forge tools, SkillForge skills and
Cowork personas into one installable, removable, shareable unit — like an app package
for the Corvin runtime.

---

## Archive layout

<img src="assets/awpkg-format.svg" alt="AWPKG archive layout: manifest.yaml + workflows/ + tools/ + skills/ + personas/ + data/" width="100%"/>

```
name-version.awpkg          ← ZIP (deflate)
├── manifest.yaml           mandatory — JSON Schema v1
├── workflows/              AWP workflow YAML files (DAG / delegation / mixed)
│   └── *.awp.yaml
├── tools/                  Forge tool schema definitions
│   └── code_*.json         name field must match  code.<slug>
├── skills/                 SkillForge skill bodies
│   └── <slug>/SKILL.md     SkillForge-linted before extraction
├── personas/               Cowork persona definitions
│   └── *.yaml
├── data/                   Optional non-PII defaults / seed config
│   └── defaults.yaml
└── README.md               Shown by  corvin pkg inspect
```

No executable scripts, compiled binaries, or hook directories are permitted.
The package *declares* — the Corvin runtime *installs*.

---

## Workflow topologies supported

A single package can contain multiple workflows mixing any of the AWP node types:

| Topology | Node type | Example |
|---|---|---|
| Linear DAG | `agent` | `daily_news_briefing` — fetch → summarize → format |
| Parallel fan-out/fan-in | `agent` (parallel level-0) | `market_research_suite` — news + reddit + filings in parallel → merge |
| Delegation graph | `delegation_loop` | `code_review_bot` — orchestrator spawns security/perf/style workers |
| Mixed DAG + delegation | `agent` + `delegation_loop` | `trading_strategy_pack` — parallel fetchers → signal delegation loop → risk filter |

---

## Lifecycle

<img src="assets/awpkg-lifecycle.svg" alt="AWPKG lifecycle: AUTHOR → INSPECT → INSTALL → ACTIVE → REMOVED" width="100%"/>

1. **Author** — `corvin pkg init` scaffolds `awpkg.yaml`; `corvin pkg build` produces the ZIP.
2. **Inspect** — `corvin pkg inspect <file>` is read-only: validates the manifest, lists components, reports warnings. Zero extraction.
3. **Install** — eight pre-extraction checks must all pass before a single byte is extracted. On success the package lands in the scope directory and a `package.installed` audit event is written.
4. **Active** — Forge tools appear in the MCP namespace, skills are injected into future bridge turns, personas are available to the cowork resolver, workflows register with the scheduler and slash-command dispatcher.
5. **Remove** — `corvin pkg remove <id>` deletes the scope directory and writes a `package.removed` audit event.

---

## Security model

<img src="assets/awpkg-security.svg" alt="Eight pre-extraction checks — all fail-closed: manifest schema, component paths, no undeclared files, no path-traversal, tool namespace, network policy, SkillForge linter, AWP validator" width="100%"/>

All eight checks run **before** any file is extracted. A single failure aborts with
`InstallError` and leaves the filesystem untouched.

The path-gate hook (`operator/voice/hooks/path_gate.py`) extends its protected subtree to
include `<corvin_home>/**/packages/**` — no agent subprocess can write into an installed
package directory directly. Only the installer CLI (or its future MCP surface) may write there.

---

## Scopes

| Scope | Install location | Visibility |
|---|---|---|
| `user` (default) | `~/.corvin/packages/<id>/` | All projects and tenants of this user |
| `project` | `.corvin/packages/<id>/` | This repository only |
| `session` | `<corvin_home>/sessions/…/packages/<id>/` | This chat session only (testing) |

Session-scope packages are never promoted automatically.

---

## CLI reference

```bash
# Install from a local .awpkg file (default scope: user)
corvin pkg install my-workflow-1.2.0.awpkg
corvin pkg install my-workflow-1.2.0.awpkg --scope project

# Install from registry (Phase 2)
corvin pkg install com.example.my-workflow

# List installed packages
corvin pkg list
corvin pkg list --scope project

# Inspect without installing (read-only)
corvin pkg inspect my-workflow-1.2.0.awpkg

# Remove
corvin pkg remove com.example.my-workflow
corvin pkg remove com.example.my-workflow --scope project

# Build a package from awpkg.yaml in the current directory
corvin pkg init                 # scaffold awpkg.yaml + sample workflow
corvin pkg build                # produces <id>-<version>.awpkg
corvin pkg build --out ./dist/

# Re-export an installed package back to a file
corvin pkg export com.example.my-workflow ./dist/
```

---

## `manifest.yaml` reference

```yaml
awpkg: "1.0"                          # format version (required)

id: "com.example.my-workflow"         # reverse-domain, globally unique (required)
name: "My Workflow"                   # display name (required)
version: "1.2.3"                      # SemVer (required)
description: "One-paragraph summary." # (required)
author: "Alice <alice@example.com>"   # optional
license: "Apache-2.0"                 # optional
homepage: "https://github.com/…"      # optional

min_corvin_version: "0.9.0"          # optional
max_corvin_version: null             # optional, null = unbounded

components:                           # at least one non-empty list required
  workflows:    [workflows/my.awp.yaml]
  forge_tools:  [tools/code_my_tool.json]
  skills:       [skills/my_skill/SKILL.md]
  personas:     [personas/my_persona.yaml]
  data:         [data/defaults.yaml]

permissions:
  network: false      # sandbox policy forwarded to Forge tool runner
  compute: true       # may schedule Compute Worker runs
  secrets:            # required secret NAMES (values live in vault)
    - MY_API_KEY

dependencies:
  - id: "com.corvin.base-tools"
    version: ">=1.0.0"
```

---

## `awpkg.yaml` build config

```yaml
# Lives in the source directory; not shipped inside the .awpkg
awpkg: "1.0"
id: "com.example.my-workflow"
name: "My Workflow"
version: "1.2.3"
description: "..."

include:
  workflows:
    - src/my.awp.yaml
  forge_tools:
    - forge/code_my_tool.json
  skills:
    - skills/my_skill/SKILL.md

permissions:
  network: false
  compute: false
  secrets: []

dependencies: []
```

---

## Audit chain events

Every install and remove writes into the unified hash-chained audit log
(`<corvin_home>/global/forge/audit.jsonl`):

```jsonc
// install
{ "event_type": "package.installed", "severity": "INFO",
  "details": { "id": "com.example.my-workflow", "version": "1.2.3",
               "scope": "user", "tenant_id": "_default" },
  "prev_hash": "…", "hash": "…" }

// remove
{ "event_type": "package.removed", "severity": "INFO",
  "details": { "id": "com.example.my-workflow", "scope": "user" },
  "prev_hash": "…", "hash": "…" }
```

---

## Relation to other subsystems

| Subsystem | Relation |
|---|---|
| **Workflow plugins** | AWPKG *is* the `.corvin-pkg` placeholder. Single-file workflow YAMLs can be shipped standalone OR inside an `.awpkg`. |
| **Plugin system** | `CorvinPlugin` protocol is the lifecycle contract. AWPKG is the *transport* for plugins that are not Python packages. |
| **Forge (L6)** | Tool JSON files ship inside `tools/`; Forge schema validator runs during install. |
| **SkillForge (L7)** | `SKILL.md` files ship inside `skills/`; SkillForge linter runs during install. Slot-mirror scope-gate applies. |
| **Cowork (L4)** | Persona YAMLs ship inside `personas/`; available to persona resolver after install. |
| **Path-gate (L10)** | `packages/**` added to protected subtree — no direct agent writes. |
| **Audit chain (L16)** | `package.installed` / `package.removed` added to the hash chain. |

---

## Tests

```bash
cd core/awpkg
python3 -m pytest tests/test_e2e.py -v
```

**52 tests** across six classes:

| Class | What it covers |
|---|---|
| `TestManifestParsing` | Schema validation, bad IDs, bad semver, empty components |
| `TestInspector` | Read-only introspection, undeclared-file warnings |
| `TestSecurity` | Path-traversal, absolute paths, undeclared files, bad tool names, missing manifest, not-a-zip, network mismatch, schema violation, declared-missing |
| `TestLifecycle` | Install/remove roundtrip, meta-file, component extraction, list, project-scope |
| `TestDAGSimple / TestDAGComplex / TestDelegation / TestMixed` | Fixture E2E: build → inspect → install → verify structure → remove |
| `TestAuditChain` | install/remove events, SHA-256 hash-chain integrity across multiple installs |
| `TestPathGateIntegration` | `is_protected_path` returns True for `packages/**` |

### Fixture workflows

| Fixture | Topology | Components |
|---|---|---|
| `dag_simple` | Linear DAG (3 nodes) | 1 workflow · 1 skill |
| `dag_complex` | Parallel fan-out/fan-in (5 nodes) | 1 workflow · 2 Forge tools · 1 skill · 1 persona |
| `delegation` | Delegation loop + synthesiser | 1 workflow · 1 Forge tool · 1 skill |
| `mixed` | DAG + delegation loop + linear DAG | 2 workflows · 3 Forge tools · 2 skills · 1 persona |
