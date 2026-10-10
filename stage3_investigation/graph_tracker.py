from __future__ import annotations
import threading
from datetime import datetime
from typing import Dict, List, Tuple

import networkx as nx

from sentinel.core import get_logger
from stage3_investigation.schemas import GraphSummary, ProceedsHop

logger = get_logger(__name__)

# Defensive caps against combinatorial blowup on dense/cyclic real-world
# transaction graphs — a financial graph can easily contain enough cycles or
# fan-out branching to make an unbounded traversal pathological. These bound
# worst-case work per call without changing the public API contract.
_MAX_CYCLES_RETURNED = 500
_MAX_PROCEEDS_HOPS_RETURNED = 500


class GraphTracker:
    def __init__(self):
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        # Single lock guarding all graph mutation AND the point-in-time copy
        # taken for reads. Expensive algorithms (PageRank, cycle enumeration)
        # then run against that copy OUTSIDE the lock, so a slow analytical
        # read never blocks the real-time ingestion path from adding new
        # transaction edges. The tradeoff is analytical reads see a
        # slightly-stale snapshot rather than the absolute latest edge —
        # acceptable for investigation/graph-summary use cases.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #
    def add_transaction(self, sender: str, receiver: str, amount: float, timestamp: datetime) -> None:
        try:
            with self._lock:
                self._graph.add_edge(sender, receiver, amount=float(amount), timestamp=timestamp)
        except Exception as e:
            logger.error(f"Failed to add transaction edge {sender} -> {receiver}: {e}")

    # ------------------------------------------------------------------ #
    # Snapshotting
    # ------------------------------------------------------------------ #
    def _snapshot(self) -> nx.MultiDiGraph:
        with self._lock:
            return self._graph.copy()

    # ------------------------------------------------------------------ #
    # Private, snapshot-scoped computations (reused by multiple public
    # methods and by get_graph_summary, so a single caller can share one
    # snapshot across several analyses instead of copying the graph
    # repeatedly).
    # ------------------------------------------------------------------ #
    def _cycles_on(self, graph: nx.MultiDiGraph, max_length: int) -> List[List[str]]:
        if graph.number_of_nodes() == 0:
            return []
        try:
            try:
                cycle_iter = nx.simple_cycles(graph, length_bound=max_length)
            except TypeError:
                # Older networkx (<3.2) doesn't support length_bound — fall
                # back to unbounded enumeration with manual length filtering.
                cycle_iter = (c for c in nx.simple_cycles(graph) if len(c) <= max_length)

            cycles: List[List[str]] = []
            for c in cycle_iter:
                cycles.append(list(c))
                if len(cycles) >= _MAX_CYCLES_RETURNED:
                    logger.warning(
                        f"detect_cycles: cycle cap ({_MAX_CYCLES_RETURNED}) reached — "
                        f"result truncated for a dense/highly-cyclic graph"
                    )
                    break
            return cycles
        except Exception as e:
            logger.warning(f"Cycle detection failed: {e}")
            return []

    def _pagerank_on(self, graph: nx.MultiDiGraph, account_id: str) -> float:
        if graph.number_of_nodes() == 0 or account_id not in graph:
            return 0.0
        try:
            scores: Dict[str, float] = nx.pagerank(graph)
            val = float(scores.get(account_id, 0.0))
            return max(0.0, min(1.0, val))
        except Exception as e:
            # Covers nx.PowerIterationFailedConvergence and any other
            # numerical/algorithmic failure — centrality is a risk signal,
            # not a hard requirement, so degrade to 0.0 rather than crash.
            logger.warning(f"PageRank computation failed for {account_id}: {e}")
            return 0.0

    def _neighbor_counts_on(self, graph: nx.MultiDiGraph, account_id: str) -> Tuple[int, int]:
        if account_id not in graph:
            return 0, 0
        try:
            fan_in = len(set(graph.predecessors(account_id)))
            fan_out = len(set(graph.successors(account_id)))
            return fan_in, fan_out
        except Exception as e:
            logger.warning(f"Neighbor count failed for {account_id}: {e}")
            return 0, 0

    # ------------------------------------------------------------------ #
    # Public read API
    # ------------------------------------------------------------------ #
    def detect_cycles(self, max_length: int = 5) -> List[List[str]]:
        graph = self._snapshot()
        return self._cycles_on(graph, max_length)

    def get_account_centrality(self, account_id: str) -> float:
        graph = self._snapshot()
        return self._pagerank_on(graph, account_id)

    def is_mule_ring_member(self, account_id: str) -> Tuple[bool, float]:
        try:
            graph = self._snapshot()
            if account_id not in graph:
                return False, 0.0

            fan_in, fan_out = self._neighbor_counts_on(graph, account_id)
            high_fan_topology = fan_in >= 3 and fan_out >= 3

            cycles = self._cycles_on(graph, max_length=5)
            in_cycle = any(account_id in c for c in cycles)

            centrality = self._pagerank_on(graph, account_id)
            is_mule = bool(high_fan_topology or in_cycle)
            return is_mule, centrality
        except Exception as e:
            logger.warning(f"is_mule_ring_member failed for {account_id}: {e}")
            return False, 0.0

    def trace_proceeds(self, start_account: str, max_hops: int = 3) -> List[ProceedsHop]:
        """
        BFS forward-traversal reconstructing downstream fund movement from
        `start_account`. `timestamp_delta_hours` is measured relative to the
        timestamp of the very first edge encountered in the trace (i.e. hours
        elapsed since funds first left the origin account) — this surfaces
        rapid layering (funds moving through many hops within a short window)
        as a coherent, comparable signal across the whole trail.
        """
        graph = self._snapshot()
        hops: List[ProceedsHop] = []
        if start_account not in graph:
            return hops

        try:
            origin_ts: datetime | None = None
            visited_edges = set()  # guards against infinite loops on cyclic graphs
            frontier = [start_account]
            hop_number = 0

            while frontier and hop_number < max_hops:
                hop_number += 1
                next_frontier: List[str] = []

                for node in frontier:
                    if node not in graph:
                        continue
                    for _, target, key, data in graph.out_edges(node, keys=True, data=True):
                        edge_id = (node, target, key)
                        if edge_id in visited_edges:
                            continue
                        visited_edges.add(edge_id)

                        ts = data.get("timestamp")
                        amount = float(data.get("amount", 0.0))

                        if origin_ts is None and ts is not None:
                            origin_ts = ts
                        delta_hours = (
                            (ts - origin_ts).total_seconds() / 3600.0
                            if (ts is not None and origin_ts is not None)
                            else 0.0
                        )

                        node_data = graph.nodes.get(target, {})
                        metadata = {str(k): str(v) for k, v in node_data.items()} if node_data else {}

                        hops.append(ProceedsHop(
                            hop_number=hop_number,
                            from_account=node,
                            to_account=target,
                            amount=amount,
                            timestamp_delta_hours=round(delta_hours, 4),
                            account_metadata=metadata,
                        ))
                        next_frontier.append(target)

                        if len(hops) >= _MAX_PROCEEDS_HOPS_RETURNED:
                            logger.warning(
                                f"trace_proceeds: hop cap ({_MAX_PROCEEDS_HOPS_RETURNED}) reached for "
                                f"{start_account} — result truncated for a highly fanned-out graph"
                            )
                            return hops

                frontier = next_frontier

            return hops
        except Exception as e:
            logger.warning(f"trace_proceeds failed for {start_account}: {e}")
            return hops  # return whatever was safely collected before the failure

    def get_graph_summary(self, account_id: str) -> GraphSummary:
        """
        Compiles mule-ring flag, centrality, cycle membership, and neighbor
        count into a single GraphSummary. Takes exactly one snapshot and
        reuses it across all sub-computations, rather than calling the other
        public methods (which would each take their own snapshot) —
        materially cheaper on a large graph.
        """
        try:
            graph = self._snapshot()
            if account_id not in graph or graph.number_of_nodes() == 0:
                return GraphSummary()

            fan_in, fan_out = self._neighbor_counts_on(graph, account_id)
            high_fan_topology = fan_in >= 3 and fan_out >= 3

            cycles = self._cycles_on(graph, max_length=5)
            cycle_nodes: List[str] = []
            cycle_detected = False
            for c in cycles:
                if account_id in c:
                    cycle_detected = True
                    cycle_nodes = c
                    break

            centrality = self._pagerank_on(graph, account_id)
            mule_ring_flag = bool(high_fan_topology or cycle_detected)

            return GraphSummary(
                mule_ring_flag=mule_ring_flag,
                centrality_score=centrality,
                cycle_detected=cycle_detected,
                cycle_nodes=cycle_nodes,
                neighbor_count=fan_in + fan_out,
            )
        except Exception as e:
            logger.warning(f"get_graph_summary failed for {account_id}: {e}")
            return GraphSummary()
