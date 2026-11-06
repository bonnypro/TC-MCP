from __future__ import annotations

from collections import defaultdict, deque

from .models import Plan, ProjectSpec, Task, TaskKind


class PlanError(ValueError):
    pass


def default_plan(spec: ProjectSpec) -> Plan:
    tasks = (
        Task("copy", TaskKind.COPY_PROJECT, resource="fs", description="copy template project"),
        Task("open", TaskKind.OPEN_SOLUTION, ("copy",), "xae", "open solution"),
        Task("write", TaskKind.WRITE_PLC, ("open",), "xae", "write PLC source"),
        Task("build", TaskKind.BUILD, ("write",), "xae", "build solution"),
        Task("verify", TaskKind.VERIFY_SYMBOL, ("build",), "xae", "verify built symbols"),
        Task("activate", TaskKind.ACTIVATE_SIMULATION, ("verify",), "xar", "activate simulation"),
        Task("ads", TaskKind.ADS_TEST, ("activate",), "ads", "read ADS symbol"),
        Task("snapshot", TaskKind.SNAPSHOT, ("ads",), "fs", "save result snapshot"),
    )
    return Plan(spec=spec, tasks=tasks)


def topological_order(tasks: tuple[Task, ...]) -> tuple[Task, ...]:
    by_id = {task.id: task for task in tasks}
    if len(by_id) != len(tasks):
        raise PlanError("duplicate task id")

    indegree: dict[str, int] = {task.id: 0 for task in tasks}
    children: dict[str, list[str]] = defaultdict(list)
    for task in tasks:
        for dependency in task.depends_on:
            if dependency not in by_id:
                raise PlanError(f"missing dependency {dependency!r} for {task.id!r}")
            indegree[task.id] += 1
            children[dependency].append(task.id)

    queue = deque(task_id for task_id, value in indegree.items() if value == 0)
    ordered: list[Task] = []
    while queue:
        task_id = queue.popleft()
        ordered.append(by_id[task_id])
        for child in children[task_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if len(ordered) != len(tasks):
        raise PlanError("task graph contains a cycle")
    return tuple(ordered)
