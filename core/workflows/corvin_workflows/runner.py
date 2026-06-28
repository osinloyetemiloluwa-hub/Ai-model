"""DAG runner — topological execution with node-type dispatch.

The runner is single-process and synchronous. Same-level nodes execute
sequentially in this MVP; parallelism is a Phase-2 concern (ThreadPoolExecutor
analog to L25-compute's ParallelDriver).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable

from .node_types import NODE_TYPES
from .storage import WorkflowDoc


@dataclass
class NodeResult:
    node_id: str
    node_type: str
    started_at: float
    finished_at: float
    output: dict[str, Any]
    error: str | None = None

    @property
    def wall_s(self) -> float:
        return self.finished_at - self.started_at


@dataclass
class RunResult:
    workflow: str
    state: str  # "complete" | "failed"
    inputs: dict[str, Any]
    nodes: dict[str, NodeResult] = field(default_factory=dict)
    final_state: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    audit: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_wall_s(self) -> float:
        if not self.nodes:
            return 0.0
        return sum(n.wall_s for n in self.nodes.values())


def _topo_levels(graph: list[dict[str, Any]]) -> list[list[str]]:
    """Kahn's algorithm — returns a list of execution levels."""
    incoming: dict[str, set[str]] = {n["id"]: set(n.get("depends_on") or []) for n in graph}
    levels: list[list[str]] = []
    remaining = dict(incoming)
    while remaining:
        ready = sorted([nid for nid, deps in remaining.items() if not deps])
        if not ready:
            raise RuntimeError("topo_levels: cycle detected (validator should have caught this)")
        levels.append(ready)
        for r in ready:
            del remaining[r]
        for deps in remaining.values():
            deps.difference_update(ready)
    return levels


def _share_output(node_spec: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    """Apply the YAML `share_output: [field1, field2]` projection.

    If `share_output` is unset, the whole output is published; otherwise only
    the listed fields. Mirrors AWP's DAG semantics.
    """
    share = node_spec.get("share_output")
    if share is None:
        return dict(output)
    if not isinstance(share, list):
        raise ValueError("share_output must be a list of field names")
    return {k: output.get(k) for k in share}


class DAGRunner:
    """Synchronous DAG runner.

    Construction:
        runner = DAGRunner(doc, engine=stub_engine)

    Each run:
        result = runner.run(inputs={...})
    """

    def __init__(
        self,
        doc: WorkflowDoc,
        *,
        engine: Any,
        audit_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.doc = doc
        self.engine = engine
        self._audit_sink = audit_sink
        self._audit_buffer: list[dict[str, Any]] = []

    def _audit(self, event: str, **details: Any) -> None:
        rec = {"event": event, **details}
        self._audit_buffer.append(rec)
        if self._audit_sink is not None:
            self._audit_sink(rec)

    def run(self, inputs: dict[str, Any] | None = None) -> RunResult:
        inputs = dict(inputs or {})
        graph = self.doc.graph
        node_specs = {n["id"]: n for n in graph}
        levels = _topo_levels(graph)

        run = RunResult(
            workflow=self.doc.name,
            state="complete",
            inputs=inputs,
            audit=self._audit_buffer,
        )
        state: dict[str, Any] = {}

        self._audit(
            "run.started",
            workflow=self.doc.name,
            input_keys=sorted(inputs.keys()),
            levels=len(levels),
            nodes=len(graph),
        )

        for level_index, level in enumerate(levels):
            self._audit("run.level", level=level_index, nodes=list(level))
            for nid in level:
                node = node_specs[nid]
                ntype = node.get("type", "agent")
                executor = NODE_TYPES[ntype]["execute"]

                t0 = perf_counter()
                self._audit("node.started", node_id=nid, node_type=ntype)
                try:
                    output = executor(
                        node=node,
                        engine=self.engine,
                        state=state,
                        inputs=inputs,
                        audit=self._audit,
                    )
                except Exception as e:  # noqa: BLE001 — every node failure is reported
                    t1 = perf_counter()
                    self._audit(
                        "node.failed",
                        node_id=nid,
                        node_type=ntype,
                        error=f"{type(e).__name__}: {e}",
                    )
                    run.nodes[nid] = NodeResult(
                        node_id=nid,
                        node_type=ntype,
                        started_at=t0,
                        finished_at=t1,
                        output={},
                        error=f"{type(e).__name__}: {e}",
                    )
                    run.state = "failed"
                    run.error = f"node {nid!r}: {e}"
                    run.final_state = state
                    self._audit(
                        "run.terminal",
                        state="failed",
                        node_id=nid,
                    )
                    return run

                t1 = perf_counter()
                # Publish projection into state
                state[nid] = _share_output(node, output)
                run.nodes[nid] = NodeResult(
                    node_id=nid,
                    node_type=ntype,
                    started_at=t0,
                    finished_at=t1,
                    output=output,
                )
                self._audit(
                    "node.completed",
                    node_id=nid,
                    node_type=ntype,
                    wall_ms=int((t1 - t0) * 1000),
                )

        run.final_state = state
        self._audit("run.terminal", state="complete", nodes=len(run.nodes))
        return run
