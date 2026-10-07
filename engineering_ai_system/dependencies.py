"""Deterministic dependency-graph validation for ProjectRun task plans."""

from __future__ import annotations


def validate_dependencies(task_specs: list[dict]) -> dict[str, list[str]]:
    """Return canonical predecessor lists, rejecting malformed or cyclic graphs."""
    task_ids: list[str] = []
    for item in task_specs:
        if not isinstance(item, dict):
            raise ValueError("Project-run task specification must be an object")
        task_id = item.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("Project-run task IDs must be nonempty strings")
        task_ids.append(task_id)
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("Project-run task IDs must be unique")

    task_id_set = set(task_ids)
    graph: dict[str, list[str]] = {}
    for item in task_specs:
        task_id = item["task_id"]
        predecessors = item.get("depends_on", [])
        if not isinstance(predecessors, list):
            raise ValueError("Task depends_on must be a list of predecessor task IDs")
        if any(not isinstance(value, str) or not value.strip() for value in predecessors):
            raise ValueError("Predecessor task IDs must be nonempty strings")
        if len(predecessors) != len(set(predecessors)):
            raise ValueError("Task dependency list contains a duplicate predecessor")
        if task_id in predecessors:
            raise ValueError("A task cannot depend on itself")
        unknown = set(predecessors) - task_id_set
        if unknown:
            raise ValueError(
                "Project-run dependency references an unknown predecessor: "
                + ", ".join(sorted(unknown))
            )
        graph[task_id] = sorted(predecessors)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError("Project-run dependency graph contains a cycle")
        if task_id in visited:
            return
        visiting.add(task_id)
        for predecessor in graph[task_id]:
            visit(predecessor)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in sorted(graph):
        visit(task_id)
    return graph
