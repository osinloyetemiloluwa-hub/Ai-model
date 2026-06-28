# Layer 38 — A2A Network Membership Attestation (ADR-0103)

This document covers the A2A network membership attestation system layered on top of
the core RemoteTriggerReceiver/Sender protocol (ADR-0048).  Read
[`layer-38-a2a-flow.md`](layer-38-a2a-flow.md) for the base protocol reference.

---

## Overview

CorvinOS is Apache-2.0 open source. Without additional protection, a fork that
bypasses the local license check could build protocol-conformant A2A envelopes once
paired. ADR-0103 closes this gap by making the **pairing step** the enforcement
boundary: only instances holding a valid Corvin Labs Session Token (SesT) can join
the network.

### Trust anchor

A cryptographic trust anchor signs all A2A attestation envelopes. All four layers
below derive their security from this single root of trust.

---

## Layer 1 — Pairing Gate (M1)

**Trigger:** `corvin-a2a pair <peer> <peer-url>`

**Flow:**

```
Local instance                    features.corvinlabs.io
     │                                     │
     │── POST /v1/pair/authorize ──────────▶│
     │   { instance_id, sest_fp,           │
     │     peer_url }                      │
     │                                     │── verify SesT
     │                                     │── check revocation
     │◀── 200 { pairing_id, pairing_cert } │
     │         OR 403 { reason }           │
```

`sest_fp = SHA-256(header_b64url + "." + payload_b64url)` — the fingerprint of the
JWT header+payload, without its signature.

On success the **PairingCertificate** (30-day JWT) and `pairing_id` are written into
the origin JSON file alongside the HMAC keys.

**Fail-closed:** network errors or a 403 from the gate abort the pairing. Use
`--offline-pair` for isolated / air-gapped networks that do not connect to
Corvin Labs.

```bash
# Standard (requires network + valid SesT):
corvin-a2a pair mypeer https://remote.host:8000/v1/a2a/receive

# Isolated network:
corvin-a2a pair --offline-pair mypeer https://remote.host:8000/v1/a2a/receive
```

---

## Layer 2 — Per-Envelope Attestation (M2)

Every outbound `TaskEnvelope` (Protocol v6) carries a `network_attestation` block:

```json
{
  "task_id": "…",
  "origin_id": "…",
  "sender_instance_id": "…",
  "issued_at": 1749462000,
  "instruction": "…",
  "attachments": [],
  "network_attestation": {
    "sest_fp":     "<hex SHA-256 of JWT header.payload>",
    "sest_sig":    "<base64url signature from the JWT>",
    "pairing_id":  "<uuid from PairingCertificate>",
    "attested_at": 1749462000
  },
  "signature": "<HMAC-SHA256 over all fields including network_attestation>"
}
```

The `network_attestation` block is included in the HMAC payload, making it
tamper-evident. Replacing or stripping it invalidates the HMAC.

### Receiver validation (Step 6.8 in `_validate()`)

After HMAC verification succeeds:

| Check | Failure code |
|---|---|
| `attested_at` within ±300 s | `network_attestation_time_window` |
| Signature verify: `sest_sig` over `sest_fp` | `network_attestation_bad_sig` |
| `pairing_id` matches stored origin `pairing_id` | `network_attestation_pairing_mismatch` |
| `sest_fp` not on manifest revocation list | `network_attestation_revoked` |
| `pairing_id` not on manifest revocation list | `network_attestation_pairing_revoked` |

If `network_attestation` is absent: check `attestation_mandatory_after` from the
manifest. Before that timestamp (grace period), the envelope is accepted with a
WARNING. After it, the envelope is rejected with `network_attestation_required`.

**Disable for tests:**

```bash
CORVIN_A2A_ATTESTATION_DISABLED=1 pytest ...
```

---

## Layer 3 — Protocol Manifest (M3)

On every adapter restart, CorvinOS fetches a signed manifest:

```
GET https://corvinlabs.io/a2a/manifest.json
Mirror: https://github.com/CorvinLabs/CorvinOS/releases/latest/download/a2a-manifest.json
Cache: <corvin_home>/global/a2a_manifest.json  (mode 0600)
```

### Manifest schema

```json
{
  "schema_version": 1,
  "issued_at": 1749462000,
  "min_protocol_version": "3.0",
  "current_protocol_version": "6.0",
  "revoked_instance_ids": ["<uuid>"],
  "revoked_sest_fps":     ["<hex fp>"],
  "revoked_pairing_ids":  ["<uuid>"],
  "attestation_mandatory_after": 1752054000,
  "signature": "<signature over canonical JSON without this field>"
}
```

The manifest is cryptographically signed over the canonical JSON (all fields except
`"signature"`, sorted keys, no whitespace). The receiver verifies it against the
embedded trust anchor.

### Staleness policy

| Manifest age | Behaviour |
|---|---|
| < 3 days | Normal |
| 3–7 days | `a2a.manifest_stale` WARNING to audit chain |
| > 7 days | Treated as absent; revocation list cleared (fail-open) |

Operators can set `a2a_manifest_required: true` in `tenant.corvin.yaml` to make
a stale / absent manifest fail-closed (A2A reception disabled until refresh succeeds).

### Python API

```python
from a2a_manifest import load_manifest, clear_cached

manifest = load_manifest()
# manifest.revoked_sest_fps: set[str]
# manifest.revoked_instance_ids: set[str]
# manifest.attestation_mandatory_after: float  (unix timestamp)
# manifest.is_stale: bool
# manifest.sig_verified: bool

# Force re-fetch:
clear_cached()
manifest = load_manifest(force_refresh=True)
```

---

## Layer 4 — Self-Test (M4)

`operator/bridges/shared/self_test.py` runs `_check_a2a_network_membership()` as
part of `run_self_test()`.

| Check name | Severity | Condition |
|---|---|---|
| `a2a.network_pubkey` | CRITICAL | `a2a_network_pubkey.pem` missing or malformed |
| `a2a.network_pubkey` | WARNING | `cryptography` package not installed |
| `a2a.manifest_age` | WARNING | Cached manifest ≥ 3 days old |
| `a2a.sest_not_revoked` | CRITICAL | Local SesT fingerprint on revocation list |

---

## Audit events (ADR-0103)

Registered in `operator/forge/forge/security_events.py`:

| Event | Severity | When |
|---|---|---|
| `a2a.pairing_authorized` | INFO | M1 gate returns a valid PairingCertificate |
| `a2a.pairing_denied` | WARNING | M1 gate returns 403 or is unreachable |
| `a2a.manifest_fetched` | INFO | Fresh manifest successfully fetched + verified |
| `a2a.manifest_stale` | WARNING | Cached manifest ≥ 3 days old, or no manifest available |
| `a2a.attestation_failed` | WARNING | Any M2 validation failure |

**Audit allow-list** — never include in `details`:
- SesT bytes or full JWT
- Instruction or result payload
- Full `sest_fp` (use first 16 hex chars: `sest_fp_prefix`)
- Pairing cert body

**Allowed in `details`:** `instance_id`, `sest_fp_prefix`, `pairing_id`,
`origin_id`, `endpoint_id`, `reason`, `grace_days_remaining`, `manifest_age_days`.

---

## Key files

| File | Role |
|---|---|
| `operator/security/a2a_network_pubkey.pem` | Embedded trust anchor public key |
| `operator/bridges/shared/a2a_manifest.py` | M3 manifest fetch / cache / expose |
| `operator/voice/scripts/corvin_a2a.py` | M1 pairing gate (`_authorize_pairing_m1`) |
| `operator/bridges/shared/remote_trigger_sender.py` | M2 build `network_attestation` |
| `operator/bridges/shared/remote_trigger_receiver.py` | M2 validate `network_attestation` |
| `operator/bridges/shared/self_test.py` | M4 CRITICAL checks |
| `operator/forge/forge/security_events.py` | New A2A audit event types |

---

## Licence quota enforcement — pairing routes

All console pairing paths enforce `a2a_peers_max` (ADR-0094) before writing
any origin/endpoint config files. Exceeding the limit returns HTTP 402.

| Route | Quota check added |
|---|---|
| `POST /remote-trigger/pair/redeem` | Yes (original ADR-0094 implementation) |
| `POST /remote-trigger/pair/accept` | Yes — issuer side (review fix 2026-06-17) |
| `POST /remote-trigger/pair/cli-accept` | Yes (review fix 2026-06-17) |
| `POST /remote-trigger/pair/friendship/import` | Yes (review fix 2026-06-17) |

The shared helper `_check_a2a_peers_max()` (in `a2a_pair.py`) counts existing
`*.json` files in the origins directory and raises HTTP 402 when the count
meets or exceeds the licence limit.

---

## Threat model

| Threat | Mitigated by |
|---|---|
| Fork bypasses pairing attestation | M1 (pairing gate) + M2 (per-envelope signed attestation) |
| Stolen HMAC keys without SesT | M2 (fork has no signing key) |
| Compromised legitimate instance | M3 (manifest revocation effective on next restart) |
| MitM on manifest fetch | Manifest is cryptographically signed; MitM cannot forge |
| Stale manifest attack | 7-day TTL; `a2a_manifest_required` for strict mode |
| Free-tier quota bypass via alternative pairing paths | `_check_a2a_peers_max()` called by all 4 pairing routes |

**Out of scope:** Operator with valid license who deliberately modifies source.
The network enforces *valid license*, not *unmodified binary*.

---

## ADR

Full decision record: `Corvin-ADR: decisions/0103-a2a-network-membership-attestation.md`
