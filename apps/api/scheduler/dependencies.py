from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any, Hashable


class DependencyCycleError(ValueError):
    pass


def find_cycle(nodes: Iterable[Hashable], edges: Iterable[tuple[Hashable, Hashable]]) -> list[Hashable] | None:
    """Return one cycle. An edge is (task, prerequisite)."""
    graph: dict[Hashable, list[Hashable]] = defaultdict(list)
    node_set = set(nodes)
    for task, prerequisite in edges:
        node_set.update((task, prerequisite))
        graph[task].append(prerequisite)
    state: dict[Hashable, int] = {}
    for node in sorted(node_set, key=str):
        if state.get(node, 0) != 0:
            continue
        path: list[Hashable] = [node]
        path_index: dict[Hashable, int] = {node: 0}
        state[node] = 1
        frames: list[tuple[Hashable, list[Hashable], int]] = [(node, sorted(graph[node], key=str), 0)]
        while frames:
            current, neighbors, index = frames[-1]
            if index >= len(neighbors):
                frames.pop()
                state[current] = 2
                path_index.pop(current, None)
                path.pop()
                continue
            neighbor = neighbors[index]
            frames[-1] = (current, neighbors, index + 1)
            neighbor_state = state.get(neighbor, 0)
            if neighbor_state == 0:
                state[neighbor] = 1
                path_index[neighbor] = len(path)
                path.append(neighbor)
                frames.append((neighbor, sorted(graph[neighbor], key=str), 0))
            elif neighbor_state == 1:
                start = path_index[neighbor]
                return path[start:] + [neighbor]
    return None


def assert_acyclic(nodes: Iterable[Hashable], edges: Iterable[tuple[Hashable, Hashable]]) -> None:
    cycle = find_cycle(nodes, edges)
    if cycle:
        raise DependencyCycleError("cyclic dependency: " + " -> ".join(map(str, cycle)))


def topological_sort(
    nodes: Iterable[Hashable],
    edges: Iterable[tuple[Hashable, Hashable]],
    sort_key: Callable[[Hashable], Any] | None = None,
) -> list[Hashable]:
    """Stable Kahn sort. An edge is represented as (task, prerequisite)."""
    node_set = set(nodes)
    prerequisites: dict[Hashable, set[Hashable]] = {node: set() for node in node_set}
    dependents: dict[Hashable, set[Hashable]] = defaultdict(set)
    for task, prerequisite in edges:
        node_set.update((task, prerequisite))
        prerequisites.setdefault(task, set()).add(prerequisite)
        prerequisites.setdefault(prerequisite, set())
        dependents[prerequisite].add(task)
    key = sort_key or (lambda item: str(item))
    ready = sorted((node for node in node_set if not prerequisites[node]), key=key)
    ordered: list[Hashable] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for dependent in sorted(dependents[node], key=key):
            prerequisites[dependent].discard(node)
            if not prerequisites[dependent] and dependent not in ordered and dependent not in ready:
                ready.append(dependent)
                ready.sort(key=key)
    if len(ordered) != len(node_set):
        assert_acyclic(node_set, edges)
    return ordered
