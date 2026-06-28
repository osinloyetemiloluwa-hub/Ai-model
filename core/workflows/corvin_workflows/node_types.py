"""NODE_TYPES registry — pluggable node executors.

Each entry binds a node-type string (the YAML `type:` field) to:
- a validator (called by R10)
- an executor (called by DAGRunner)

Operators can register additional types without forking by calling
`register_node_type(name, validator=..., executor=...)`.

The three shipped types:

  agent             — static single-shot engine call (default)
  fan_out           — same agent over N items from a state field, parallel
  delegation_loop   — manager-LLM iterates DELEGATE / COMPLETE until budget
"""
from __future__ import annotations

from typing import Any, Callable

# Forward references — runner imports node_types, node_types imports nothing.
_Executor = Callable[..., Any]
_Validator = Callable[[dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Agent node — the simple case
# ---------------------------------------------------------------------------


def _validate_agent(node: dict[str, Any]) -> None:
    agent = node.get("agent")
    if not isinstance(agent, str) or not agent:
        raise ValueError("agent node requires non-empty 'agent' field")


def _execute_agent(*, node, engine, state, inputs, audit) -> dict[str, Any]:
    """Single engine.spawn call. The agent reads from `state` + `inputs`."""
    from .engines import EngineCall

    call = EngineCall(
        agent=node["agent"],
        instructions=node.get("instructions", ""),
        inputs=dict(inputs),
        state=dict(state),
        iteration=0,
        metadata={"node_id": node["id"], "node_type": "agent"},
    )
    audit("node.engine_call", node_id=node["id"], agent=call.agent, iteration=0)
    return engine.spawn(call)


# ---------------------------------------------------------------------------
# Fan-out node — same agent over many items
# ---------------------------------------------------------------------------


def _validate_fan_out(node: dict[str, Any]) -> None:
    agent = node.get("agent")
    if not isinstance(agent, str) or not agent:
        raise ValueError("fan_out node requires 'agent'")
    items_from = node.get("items_from")
    if not isinstance(items_from, str) or not items_from:
        raise ValueError("fan_out node requires 'items_from' (state field name)")


def _execute_fan_out(*, node, engine, state, inputs, audit) -> dict[str, Any]:
    from .engines import EngineCall

    field = node["items_from"]
    # items_from accepts "node_id.field" or just "field" (top-level state)
    if "." in field:
        nid, subfield = field.split(".", 1)
        items = (state.get(nid) or {}).get(subfield, [])
    else:
        items = state.get(field, [])
    if not isinstance(items, list):
        raise ValueError(f"fan_out: items_from {field!r} did not resolve to a list")

    results = []
    for i, item in enumerate(items):
        call = EngineCall(
            agent=node["agent"],
            instructions=node.get("instructions", ""),
            inputs={**inputs, "item": item, "item_index": i},
            state=dict(state),
            iteration=i,
            metadata={"node_id": node["id"], "node_type": "fan_out", "item_index": i},
        )
        audit("node.engine_call", node_id=node["id"], agent=call.agent, iteration=i)
        results.append(engine.spawn(call))
    return {"items": results, "count": len(results)}


# ---------------------------------------------------------------------------
# Delegation-loop node — the composed Pattern-4 inner step
# ---------------------------------------------------------------------------


def _validate_delegation_loop(node: dict[str, Any]) -> None:
    cfg = node.get("config") or {}
    manager = cfg.get("manager")
    if not isinstance(manager, str) or not manager:
        raise ValueError("delegation_loop node requires config.manager")
    budget = cfg.get("budget") or {}
    if not isinstance(budget.get("max_loops"), int) or budget["max_loops"] < 1:
        raise ValueError("delegation_loop node requires config.budget.max_loops >= 1")
    if not isinstance(budget.get("max_total_workers"), int) or budget["max_total_workers"] < 1:
        raise ValueError("delegation_loop node requires config.budget.max_total_workers >= 1")


def _execute_delegation_loop(*, node, engine, state, inputs, audit) -> dict[str, Any]:
    """Manager-worker loop, bounded by budget.

    Manager protocol — returns a dict with one of:
      {"decision": "DELEGATE", "workers": [{agent, instructions, inputs?}, ...]}
      {"decision": "COMPLETE", "result": {...}, "confidence": 0.0..1.0}
      {"decision": "FAIL", "reason": "..."}

    Workers return free-form dicts; the runtime stitches them into
    `state["_delegation"][node_id]["iterations"]` so the next manager call
    sees the full history.
    """
    from .engines import EngineCall

    cfg = node["config"]
    manager_name = cfg["manager"]
    budget = cfg["budget"]
    max_loops = int(budget["max_loops"])
    max_total_workers = int(budget["max_total_workers"])

    iterations: list[dict[str, Any]] = []
    workers_spawned = 0
    terminal = None  # set by COMPLETE / FAIL / budget-hit

    for it in range(1, max_loops + 1):
        manager_call = EngineCall(
            agent=manager_name,
            instructions=node.get("instructions", ""),
            inputs=dict(inputs),
            state={**state, "_iterations": iterations},
            iteration=it,
            metadata={
                "node_id": node["id"],
                "node_type": "delegation_loop",
                "role": "manager",
                "workers_spawned": workers_spawned,
                "max_total_workers": max_total_workers,
            },
        )
        audit(
            "node.delegation_iteration",
            node_id=node["id"],
            iteration=it,
            workers_spawned=workers_spawned,
        )
        decision = engine.spawn(manager_call)
        kind = decision.get("decision")

        if kind == "COMPLETE":
            terminal = {
                "state": "complete",
                "iteration": it,
                "result": decision.get("result", {}),
                "confidence": float(decision.get("confidence", 1.0)),
            }
            iterations.append({"iteration": it, "manager": decision, "workers": []})
            break

        if kind == "FAIL":
            terminal = {
                "state": "failed",
                "iteration": it,
                "reason": decision.get("reason", "manager said FAIL"),
            }
            iterations.append({"iteration": it, "manager": decision, "workers": []})
            break

        if kind != "DELEGATE":
            terminal = {
                "state": "failed",
                "iteration": it,
                "reason": f"unknown manager decision: {kind!r}",
            }
            iterations.append({"iteration": it, "manager": decision, "workers": []})
            break

        workers_spec = list(decision.get("workers") or [])
        # Trim to budget so we never overshoot max_total_workers
        remaining = max_total_workers - workers_spawned
        if len(workers_spec) > remaining:
            workers_spec = workers_spec[:remaining]
        worker_results: list[dict[str, Any]] = []
        for wi, w in enumerate(workers_spec):
            wcall = EngineCall(
                agent=w["agent"],
                instructions=w.get("instructions", ""),
                inputs={**inputs, **(w.get("inputs") or {})},
                state={**state, "_iterations": iterations},
                iteration=it,
                metadata={
                    "node_id": node["id"],
                    "node_type": "delegation_loop",
                    "role": "worker",
                    "loop_iter": it,
                    "worker_index": wi,
                },
            )
            audit(
                "node.engine_call",
                node_id=node["id"],
                agent=wcall.agent,
                iteration=it,
            )
            worker_results.append(engine.spawn(wcall))
            workers_spawned += 1

        iterations.append({"iteration": it, "manager": decision, "workers": worker_results})

        if workers_spawned >= max_total_workers:
            # Budget exhausted but manager didn't COMPLETE; mark partial.
            terminal = {
                "state": "partial",
                "iteration": it,
                "reason": f"max_total_workers reached ({max_total_workers})",
            }
            break

    if terminal is None:
        terminal = {
            "state": "partial",
            "iteration": max_loops,
            "reason": f"max_loops reached ({max_loops})",
        }

    # Lift the manager's `result` dict to the top level so `share_output:
    # [score, top_quotes, ...]` projects naturally without forcing
    # downstream nodes to know about delegation-loop internals. Metadata
    # (terminal, iterations, workers_spawned) stays under reserved keys.
    output: dict[str, Any] = {
        "_terminal": terminal,
        "_iterations": iterations,
        "_workers_spawned": workers_spawned,
        # Back-compat / inspection keys (tests still read these)
        "terminal": terminal,
        "iterations": iterations,
        "workers_spawned": workers_spawned,
        "result": terminal.get("result", None),
    }
    result_payload = terminal.get("result") or {}
    if isinstance(result_payload, dict):
        for k, v in result_payload.items():
            if k not in output:
                output[k] = v
    return output


# ---------------------------------------------------------------------------
# Registry + extension point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Deliver node — write upstream output to a bridge outbox
# ---------------------------------------------------------------------------

_DELIVER_CHANNELS = {"discord", "telegram", "slack", "whatsapp", "email", "signal", "teams"}
_DELIVER_FORMATS = {"text", "markdown"}


def _validate_deliver(node: dict[str, Any]) -> None:
    cfg = node.get("config") or {}
    channel = cfg.get("channel", "")
    if channel not in _DELIVER_CHANNELS:
        raise ValueError(
            f"deliver node requires config.channel in {sorted(_DELIVER_CHANNELS)}, got {channel!r}"
        )
    chat_id = cfg.get("chat_id", "")
    if not chat_id:
        raise ValueError("deliver node requires config.chat_id (channel ID, group ID, or 'auto')")
    fmt = cfg.get("format", "markdown")
    if fmt not in _DELIVER_FORMATS:
        raise ValueError(f"deliver node config.format must be one of {_DELIVER_FORMATS}, got {fmt!r}")
    # voice is optional boolean
    voice = cfg.get("voice")
    if voice is not None and not isinstance(voice, bool):
        raise ValueError("deliver node config.voice must be true or false")


def _execute_deliver(*, node: dict[str, Any], engine: Any, state: dict[str, Any], inputs: dict[str, Any], audit: Any) -> dict[str, Any]:
    """Write upstream output to the bridge outbox so the messenger daemon delivers it."""
    import json as _json
    import os as _os
    import secrets as _secrets
    import time as _time
    from pathlib import Path as _Path

    cfg = node.get("config") or {}
    channel = cfg["channel"]
    chat_id = str(cfg.get("chat_id", "auto"))
    fmt = cfg.get("format", "markdown")

    # Collect text from upstream nodes via state
    deps = node.get("depends_on") or []
    text_parts: list[str] = []
    for dep in deps:
        dep_val = state.get(dep)
        if isinstance(dep_val, dict):
            text_parts.append(str(dep_val.get("output", dep_val.get("text", ""))))
        elif dep_val is not None:
            text_parts.append(str(dep_val))
    text = "\n\n".join(p for p in text_parts if p).strip()

    if not text:
        audit("deliver.skipped", node_id=node["id"], reason="no upstream output")
        return {"delivered": False, "reason": "no upstream output"}

    # Resolve chat_id="auto" from state (set by the workflow runner on triggered runs)
    if chat_id == "auto":
        chat_id = str(state.get("__trigger_chat_id__", ""))
        if not chat_id:
            audit("deliver.skipped", node_id=node["id"], reason="chat_id=auto but no trigger context")
            return {"delivered": False, "reason": "chat_id=auto — no trigger context available"}

    # Locate the shared outbox directory (same path Discord/Telegram daemons poll)
    _here = _Path(__file__).resolve()
    _repo = _here.parents[3]  # workflows/corvin_workflows/ → core/ → repo root
    outbox_dir = _repo / "operator" / "bridges" / "shared" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)

    envelope = {
        "channel": channel,
        "chat_id": chat_id,
        "text": text[:4000],  # Discord hard limit is 2000; daemon chunks longer messages
        "_workflow_deliver": True,
        "ts": int(_time.time() * 1000),
    }

    fname = f"wf_deliver_{_secrets.token_hex(6)}.json"
    fpath = outbox_dir / fname
    fpath.write_text(_json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")

    audit("deliver.sent", node_id=node["id"], channel=channel, chat_id=chat_id, chars=len(text))
    return {"delivered": True, "channel": channel, "chat_id": chat_id, "chars": len(text)}


# ---------------------------------------------------------------------------
# Registry + extension point
# ---------------------------------------------------------------------------

NODE_TYPES: dict[str, dict[str, Any]] = {
    "agent": {"validate": _validate_agent, "execute": _execute_agent},
    "fan_out": {"validate": _validate_fan_out, "execute": _execute_fan_out},
    "delegation_loop": {
        "validate": _validate_delegation_loop,
        "execute": _execute_delegation_loop,
    },
    "deliver": {"validate": _validate_deliver, "execute": _execute_deliver},
}


def register_node_type(
    name: str,
    *,
    validate: _Validator | None,
    execute: _Executor,
) -> None:
    """Operator-facing extension point. Adds a node type without forking."""
    if name in NODE_TYPES:
        raise ValueError(f"node type {name!r} already registered")
    NODE_TYPES[name] = {"validate": validate, "execute": execute}
