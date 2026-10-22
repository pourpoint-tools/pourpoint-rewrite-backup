"""Ordered target identities and bounded worker-owned execution."""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from shapely.geometry.base import BaseGeometry

from pourpoint.fabric import HydroFabric
from pourpoint.results import BatchIssue, BatchResult, OperationError, Target
from pourpoint.storage import TransferStats

type Targets = dict[str, BaseGeometry] | list[BaseGeometry]


def workers(n_jobs: int) -> int:
    if type(n_jobs) is not int or n_jobs == 0 or n_jobs < -1:
        raise ValueError("n_jobs must be -1 or a positive integer")
    return (os.cpu_count() or 1) if n_jobs == -1 else n_jobs


def identify(targets: Targets) -> list[Target]:
    items = targets.items() if isinstance(targets, dict) else ((str(uuid4()), geometry) for geometry in targets)
    result = []
    for index, (name, geometry) in enumerate(items):
        if not isinstance(name, str) or not isinstance(geometry, BaseGeometry):
            raise TypeError("batch targets require string names and Shapely geometries")
        result.append(Target(index, name, geometry))
    return result


def execute[T](
    fabric: HydroFabric, targets: list[Target], operation: Callable[[HydroFabric, Target], T], n_jobs: int
) -> BatchResult[T]:
    count = min(workers(n_jobs), len(targets))
    fabric._ensure_open()

    def evaluate(reader: HydroFabric, target: Target) -> tuple[T | None, BatchIssue | None]:
        try:
            return operation(reader, target), None
        except OperationError as error:
            return None, BatchIssue(target.index, target.name, error.code, error.reason)

    if count <= 1:
        slots = [evaluate(fabric, target) for target in targets]
    else:

        def chunk(indices: list[int]) -> tuple[list[tuple[int, T | None, BatchIssue | None]], TransferStats]:
            with fabric._fork() as reader:
                values = [(index, *evaluate(reader, targets[index])) for index in indices]
                return values, reader.stats

        slots: list[tuple[T | None, BatchIssue | None]] = [(None, None)] * len(targets)
        with ThreadPoolExecutor(max_workers=count) as pool:
            # One context per worker chunk, reused for its targets, never shared.
            futures = [pool.submit(chunk, list(range(worker, len(targets), count))) for worker in range(count)]
            for future in futures:
                values, stats = future.result()
                fabric.stats.requests += stats.requests
                fabric.stats.transferred_bytes += stats.transferred_bytes
                fabric.stats.cache_hit_bytes += stats.cache_hit_bytes
                for index, value, issue in values:
                    slots[index] = value, issue
    return BatchResult([value for value, _ in slots], targets, [issue for _, issue in slots if issue is not None])
