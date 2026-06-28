"""ST9.3 E2E: per-tool budget from meta.budget, clamped by policy.

Fictional scenario: Claude forges a number of tools, each declaring its
own budget in ``meta.budget``. The operator's ``policy.json`` caps the
upper bound. We validate four cases:

  1. Tool with budget within max → applied as-is, surfaces in completion.
  2. Tool with budget above max → clamped, ``meta.policy_clamped`` is set
     in the response envelope.
  3. Tool with no meta.budget → policy.default_budget applies.
  4. Tool that exceeds its CPU budget at runtime → killed by SIGXCPU.
  5. Tool that exceeds its wall budget → timeout error.
  6. Tool that exceeds its artifact_bytes budget → ToolError after run.

All paths go through the MCP server (the production path).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mcp import MCPClient


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ---------- impls --------------------------------------------------------

QUICK_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
print(json.dumps({"data": {"a": p["a"]}}))
'''
QUICK_SCHEMA = {"type": "object", "required": ["a"],
                "properties": {"a": {"type": "integer"}}}

# CPU-only burner: tight loop with no syscalls, gets killed by RLIMIT_CPU
SPIN_IMPL = '''#!/usr/bin/env python3
import json, sys
json.loads(sys.stdin.read())
while True:
    pass
'''

# Wall-clock burner: blocks on a timer
SLEEP_IMPL = '''#!/usr/bin/env python3
import json, sys, time
json.loads(sys.stdin.read())
time.sleep(60)
print("never")
'''

# Writes lots of bytes into _artifacts_dir
HUGE_ARTIFACT_IMPL = '''#!/usr/bin/env python3
import json, os, sys
p = json.loads(sys.stdin.read())
adir = p["_artifacts_dir"]
with open(os.path.join(adir, "big.bin"), "wb") as fh:
    fh.write(b"x" * 200_000)   # 200 KB
print(json.dumps({"data": {"wrote": 200_000}}))
'''

NOOP = {"type": "object", "properties": {}}


def _forge(client, *, name, impl, schema=None, meta=None):
    args = {
        "name": name,
        "description": name,
        "input_schema": schema or NOOP,
        "impl": impl,
    }
    if meta is not None:
        args["meta"] = meta
    return client.request(
        "tools/call", {"name": "forge_tool", "arguments": args}
    )


# ---------- tests --------------------------------------------------------

def test_within_envelope_budget_passes_through():
    print("\n[budget within max → applied as-is, no policy_clamped]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Operator policy: max cpu=10s, default cpu=5s
        (td / "policy.json").write_text(json.dumps({
            "default_budget": {"cpu_seconds": 5, "wall_seconds": 10,
                                "output_bytes": 4194304,
                                "artifact_bytes": 8388608},
            "max_budget":      {"cpu_seconds": 10, "wall_seconds": 30,
                                "output_bytes": 16777216,
                                "artifact_bytes": 67108864},
            "forbidden_imports": ["socket", "subprocess"],
        }))
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, name="quick", impl=QUICK_IMPL,
                   schema=QUICK_SCHEMA,
                   meta={"budget": {"cpu_seconds": 4, "wall_seconds": 8,
                                     "output_bytes": 100_000,
                                     "artifact_bytes": 1_000_000}})
            resp = client.request("tools/call", {
                "name": "quick", "arguments": {"a": 1},
            })
        finally:
            client.close()
        struct = resp["result"]["structuredContent"]
        env_meta = (struct.get("envelope") or {}).get("meta") or {}
        t("call ok", resp["result"].get("isError") is False)
        t("no meta.policy_clamped (budget within envelope)",
          "policy_clamped" not in env_meta)


def test_oversize_budget_gets_clamped():
    print("\n[budget exceeds max → clamped, meta.policy_clamped surfaces it]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "policy.json").write_text(json.dumps({
            "max_budget": {"cpu_seconds": 5, "wall_seconds": 10,
                            "output_bytes": 1_000_000,
                            "artifact_bytes": 5_000_000},
        }))
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, name="quick", impl=QUICK_IMPL,
                   schema=QUICK_SCHEMA,
                   meta={"budget": {"cpu_seconds": 100,
                                     "wall_seconds": 999,
                                     "output_bytes": 99_999_999,
                                     "artifact_bytes": 99_999_999}})
            resp = client.request("tools/call", {
                "name": "quick", "arguments": {"a": 1},
            })
        finally:
            client.close()
        struct = resp["result"]["structuredContent"]
        env_meta = (struct.get("envelope") or {}).get("meta") or {}
        clamp = env_meta.get("policy_clamped") or {}
        t("call ok", resp["result"].get("isError") is False)
        t("clamp covers cpu_seconds 100→5",
          clamp.get("cpu_seconds", {}).get("requested") == 100 and
          clamp.get("cpu_seconds", {}).get("applied") == 5)
        t("clamp covers wall_seconds 999→10",
          clamp.get("wall_seconds", {}).get("applied") == 10)
        t("clamp covers output_bytes 99999999→1_000_000",
          clamp.get("output_bytes", {}).get("applied") == 1_000_000)
        t("clamp covers artifact_bytes 99999999→5_000_000",
          clamp.get("artifact_bytes", {}).get("applied") == 5_000_000)


def test_no_meta_budget_uses_policy_default():
    print("\n[no meta.budget → policy.default_budget applies]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "policy.json").write_text(json.dumps({
            "default_budget": {"cpu_seconds": 7, "wall_seconds": 14,
                                "output_bytes": 250_000,
                                "artifact_bytes": 5_000_000},
            "max_budget":      {"cpu_seconds": 30, "wall_seconds": 60,
                                "output_bytes": 4_000_000,
                                "artifact_bytes": 50_000_000},
        }))
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, name="quick", impl=QUICK_IMPL,
                   schema=QUICK_SCHEMA)  # no meta at all
            resp = client.request("tools/call", {
                "name": "quick", "arguments": {"a": 1},
            })
        finally:
            client.close()
        struct = resp["result"]["structuredContent"]
        env_meta = (struct.get("envelope") or {}).get("meta") or {}
        t("ok", resp["result"].get("isError") is False)
        t("no clamp (defaults are within max)",
          "policy_clamped" not in env_meta)


def test_cpu_budget_exceeded_kills_tool():
    print("\n[CPU budget 1s + spin loop → SIGXCPU kills the tool]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "policy.json").write_text(json.dumps({
            "max_budget": {"cpu_seconds": 60, "wall_seconds": 60,
                            "output_bytes": 1_000_000,
                            "artifact_bytes": 1_000_000},
        }))
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, name="spinhog", impl=SPIN_IMPL, schema=NOOP,
                   meta={"budget": {"cpu_seconds": 1, "wall_seconds": 30,
                                     "output_bytes": 100_000,
                                     "artifact_bytes": 100_000}})
            t0 = time.monotonic()
            resp = client.request("tools/call",
                                   {"name": "spinhog", "arguments": {}},
                                   timeout=15.0)
            elapsed = time.monotonic() - t0
        finally:
            client.close()
        t("call returned isError=True",
          resp["result"].get("isError") is True)
        t("returned in well under wall_seconds (CPU killed earlier)",
          elapsed < 5.0, detail=f"elapsed={elapsed:.2f}s")


def test_wall_budget_exceeded_times_out():
    print("\n[wall_seconds budget = 0.5 + sleep(60) → timeout in <2s]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, name="sleeper", impl=SLEEP_IMPL, schema=NOOP,
                   meta={"budget": {"cpu_seconds": 30,
                                     "wall_seconds": 0,    # → applied 1 (min)
                                     "output_bytes": 100_000,
                                     "artifact_bytes": 100_000}})
            # Note: wall_seconds=0 → timeout=0 → race. We use 1 explicitly:
        finally:
            pass
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, name="sleeper2", impl=SLEEP_IMPL, schema=NOOP,
                   meta={"budget": {"cpu_seconds": 30, "wall_seconds": 1,
                                     "output_bytes": 100_000,
                                     "artifact_bytes": 100_000}})
            t0 = time.monotonic()
            resp = client.request("tools/call",
                                   {"name": "sleeper2", "arguments": {}},
                                   timeout=10.0)
            elapsed = time.monotonic() - t0
        finally:
            client.close()
        t("call isError=True", resp["result"].get("isError") is True)
        t("error mentions timeout",
          "timed out" in resp["result"]["content"][0]["text"].lower())
        t("returned in <3s (timeout fired at ~1s + cleanup)",
          elapsed < 3.0, detail=f"elapsed={elapsed:.2f}s")


def test_artifact_budget_exceeded_after_run():
    print("\n[artifact_bytes budget tiny + tool writes 200KB → reject]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, name="hugefile", impl=HUGE_ARTIFACT_IMPL,
                   schema=NOOP,
                   meta={"budget": {"cpu_seconds": 5, "wall_seconds": 10,
                                     "output_bytes": 100_000,
                                     "artifact_bytes": 50_000}})
            resp = client.request("tools/call",
                                   {"name": "hugefile", "arguments": {}})
        finally:
            client.close()
        result = resp["result"]
        t("isError=True", result.get("isError") is True)
        text = result["content"][0]["text"]
        t("error mentions artifact_budget.exceeded",
          "artifact_budget.exceeded" in text)


def main() -> int:
    test_within_envelope_budget_passes_through()
    test_oversize_budget_gets_clamped()
    test_no_meta_budget_uses_policy_default()
    test_cpu_budget_exceeded_kills_tool()
    test_wall_budget_exceeded_times_out()
    test_artifact_budget_exceeded_after_run()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
