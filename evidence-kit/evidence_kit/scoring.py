"""Completion scoring (the arithmetic part of the rubric in method.md).

Input: a CSV with one row per acceptance criterion and the reviewer's status:

    V  Verified       observed in the running system (account, CI/CD run history, live endpoint)
    S  Supported      only in repo / IaC / documents, not observed running
    M  Missing        no evidence that it was delivered
    C  Contradicted   evidence shows the opposite of the claim
    U  Unable         reviewer could not assess (no access, outside the record window)

The statuses are human judgments. This module only adds them up, so the
numbers in a report can be re-computed by anyone from the CSV.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

STATUSES = {"V": "Verified", "S": "Supported", "M": "Missing", "C": "Contradicted", "U": "Unable to assess"}


@dataclass
class Task:
    task_id: str
    name: str
    weight: float
    criteria: list[dict] = field(default_factory=list)

    def count(self, code: str) -> int:
        return sum(1 for c in self.criteria if c["status"] == code)

    @property
    def n(self) -> int:
        return len(self.criteria)

    @property
    def verified(self) -> float:
        return self.count("V") / self.n

    @property
    def supported(self) -> float:
        return (self.count("V") + self.count("S")) / self.n

    @property
    def status(self) -> str:
        codes = {c["status"] for c in self.criteria}
        if codes == {"V"}:
            return "Complete, verified"
        if codes <= {"V", "S"}:
            return "Complete, unverified"
        if codes & {"V", "S"}:
            return "Partial"
        if codes == {"U"}:
            return "Unable to assess"
        return "Not started"


def load(path: Path) -> list[Task]:
    tasks: dict[str, Task] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            status = row["status"].strip().upper()
            if status not in STATUSES:
                raise ValueError(f"criterion {row['criterion_id']}: unknown status {status!r}")
            tid = row["task_id"].strip()
            task = tasks.get(tid)
            weight = float(row["weight"])
            if task is None:
                task = tasks[tid] = Task(tid, row["task"].strip(), weight)
            elif task.weight != weight:
                raise ValueError(f"task {tid}: weight differs between rows")
            task.criteria.append({**row, "status": status})
    if not tasks:
        raise ValueError("no criteria in file")
    return list(tasks.values())


def project_range(tasks: list[Task]) -> tuple[float, float]:
    total = sum(t.weight for t in tasks)
    low = sum(t.weight * t.verified for t in tasks) / total
    high = sum(t.weight * t.supported for t in tasks) / total
    return low, high


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


def markdown_table(tasks: list[Task]) -> str:
    lines = [
        "| Task | Weight | Criteria V/S/M/C/U | Status | Verified | Supported |",
        "|---|---:|:---:|---|---:|---:|",
    ]
    for t in tasks:
        counts = "/".join(str(t.count(c)) for c in "VSMCU")
        lines.append(f"| {t.task_id} {t.name} | {t.weight:g} | {counts} | {t.status} | {pct(t.verified)} | {pct(t.supported)} |")
    low, high = project_range(tasks)
    total_w = sum(t.weight for t in tasks)
    lines.append(f"| **Project (weighted)** | {total_w:g} | | | **{pct(low)}** | **{pct(high)}** |")
    return "\n".join(lines)
