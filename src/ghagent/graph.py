"""A small state-machine engine for agent workflows.

Nodes are functions ``state -> state``. Edges are either static or chosen by a router
function that inspects the state. The engine enforces a step budget so a mis-wired
loop fails loudly instead of running forever.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from ghagent.errors import GraphError

S = TypeVar("S")

END = "__end__"


class WorkflowGraph(Generic[S]):
    """Directed graph of nodes with static and conditional edges."""

    def __init__(self) -> None:
        self._nodes: dict[str, Callable[[S], S]] = {}
        self._edges: dict[str, str] = {}
        self._routers: dict[str, Callable[[S], str]] = {}
        self._entry: str | None = None

    def add_node(self, name: str, func: Callable[[S], S]) -> None:
        """Register ``func`` under ``name``."""
        if name in self._nodes or name == END:
            raise GraphError(f"invalid or duplicate node name: {name}")
        self._nodes[name] = func

    def set_entry(self, name: str) -> None:
        """Choose the first node to execute."""
        self._entry = name

    def add_edge(self, source: str, target: str) -> None:
        """Always go from ``source`` to ``target`` (use :data:`END` to stop)."""
        self._edges[source] = target

    def add_conditional_edges(self, source: str, router: Callable[[S], str]) -> None:
        """After ``source`` runs, ask ``router`` which node comes next."""
        self._routers[source] = router

    def validate(self) -> None:
        """Check that the entry point and every edge target exist."""
        if self._entry is None or self._entry not in self._nodes:
            raise GraphError("entry node is not set or unknown")
        for source, target in self._edges.items():
            if source not in self._nodes:
                raise GraphError(f"edge from unknown node: {source}")
            if target != END and target not in self._nodes:
                raise GraphError(f"edge to unknown node: {target}")
        for source in self._routers:
            if source not in self._nodes:
                raise GraphError(f"router on unknown node: {source}")

    def run(self, state: S, *, max_steps: int = 50) -> S:
        """Execute the graph from the entry node until :data:`END` is reached."""
        self.validate()
        current = self._entry
        for _ in range(max_steps):
            if current is None or current == END:
                return state
            if current not in self._nodes:
                raise GraphError(f"router selected unknown node: {current}")
            state = self._nodes[current](state)
            if current in self._routers:
                current = self._routers[current](state)
            elif current in self._edges:
                current = self._edges[current]
            else:
                raise GraphError(f"node has no outgoing edge: {current}")
        raise GraphError(f"workflow exceeded {max_steps} steps")
