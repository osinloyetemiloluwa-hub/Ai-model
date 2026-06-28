# ADR Gate — Architectural Decision Records (Standard Quality Discipline)

**adr-gate is now a standard quality discipline.** It is automatically injected into all default personas
(assistant, coder, browser, research) via the skill system. After every non-trivial implementation task,
the skill is available in your prompt; follow its rubric before declaring "done."

## The High Bar

The gate has a **HIGH BAR**: the default answer is **NO ADR needed**.

**Most tasks produce no ADR — that is correct and expected.**

An ADR is a design document for future engineers. If your change doesn't change how future engineers
will approach this subsystem, you don't need an ADR.

## When to Write an ADR

**Write an ADR only when BOTH of the following hold:**

1. **A real design choice was made:**
   - You chose option A over option B (genuine alternatives existed)
   - The choice constrains future code (e.g., "we always X when Y happens")
   - It's not the obvious/only solution

2. **At least one structural trigger:**
   - **New protocol / wire-format / schema** (changes how data flows between systems)
   - **Security or compliance mechanism** (new cryptographic operation, audit event, access gate)
   - **Irreversible default** (fail-open vs. fail-closed, deny vs. allow, can't be easily reversed)
   - **Cross-repo binding** (affects ≥2 repos, standardizes a boundary)
   - **New layer-level contract** (changes how a layer or subsystem works for all callers)

## When to Skip an ADR

**Skip an ADR for:**
- Bug fixes (even if complex) — you're restoring the intended behavior, not changing it
- Pure refactors — same external behavior, different internal code
- Config tuning — changing a number or flag, not the mechanism
- Test-only or docs-only changes — no code behavior change
- Anything trivially reversible without migration
- Simple feature additions that follow an established pattern

**When you skip, name the reason in one sentence — never skip silently.**

Example: "Skipped ADR because this is a pure refactor of the Forge MCP handler (no behavior change)."

## How to Apply adr-gate

1. **At the END** of any non-trivial task (feature, multi-file bugfix, refactor with behavior change, etc.)
2. **Load and read the adr-gate skill prompt** (it will be injected if available)
3. **Follow the three-level analysis:**
   - **Conceptual:** Does this task touch a design decision or a new mechanism?
   - **Structural:** Does it meet one of the trigger criteria above?
   - **Implementation:** Would future engineers benefit from knowing why we chose this way?
4. **If yes:** Write the ADR (see ADR Destination below)
5. **If no:** Name the skip reason in one sentence in your summary

## ADR Destination

**ADRs live in `Corvin-ADR/decisions/` (sibling repository), NOT in this repo.**

```
Corvin-ADR/decisions/XXXX-short-title.md
```

**Numbering:** max existing number + 1, four digits zero-padded (0042, 0156, etc.).

**Commit message:**
```
adr: add ADR-XXXX — [title]
```

Example:
```
adr: add ADR-0160 — Custom Layer System Tier Licensing
```

## ADR Structure (Lightweight Template)

```markdown
# ADR-XXXX — [Title]

## Status
Accepted | Proposed | Superseded

## Context
Why are we making this decision? What problem are we solving?

## Decision
What did we decide? (One sentence.)

## Rationale
Why this way and not the alternatives?

## Consequences
What changes as a result? What becomes easier/harder?

## Related
- [ADR-YYYY](...) — Previous decisions this builds on
- [Layer 16](../docs/claude-ref/layer-16-security.md) — Implementation details
```

## Hard Rules (Must NOT do)

1. **Don't write ADR content into the Corvin repo** — ADRs live in Corvin-ADR only.
   The sibling repo is the source of truth.

2. **Don't auto-skip for security/compliance mechanisms** without explicit written justification.
   Security/compliance changes almost always warrant an ADR (they constrain future work).

3. **Don't declare "done"** on a structural change without running adr-gate.
   If you're unsure, run the gate — it takes 5 minutes and catches overconfidence.

4. **Don't leave a skip implicit** — always write one sentence explaining why you skipped.
   Implicit skips become lost decisions.

## Recent ADRs Implemented

- **ADR-0069 (EAOS — Engine-Agnostic OS Shell):** Tool Execution Broker (TEB), Engine Command Interface (ECI), Function-Call Bridge (FCB), SkillCompiler — all non-CC engines share L10/L16/L33 guarantees
- **ADR-0071 (CopilotCliEngine):** GitHub Copilot CLI as 5th WorkerEngine (worker-only, `copilot -p`, task-type steering)
- **ADR-0141 (Layer Integrity Protocol):** Cryptographic layer presence verification (CAP_VERSIONS + RS256-signed manifest)
- **ADR-0142 (Layer Extension API):** Vendor-layer extensibility (Tier-A/B/C licensing model)
- **ADR-0156 (Custom Layer System):** Custom layer installation, licensing gate, boot enforcement

## Related

- [CLAUDE.md](../../CLAUDE.md) — Main conventions document
- [compliance-baseline.md](compliance-baseline.md) — Compliance constraints (ADRs often needed here)
- [Corvin-ADR repository](https://github.com/anthropics/corvin-adr) — Public ADR source of truth
