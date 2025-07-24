"""
Thread‑safe task scheduling for BPY‑MCP.

Anything that touches Blender data MUST be executed on the main thread.
"""
from __future__ import annotations

import queue
import traceback
from concurrent.futures import Future
from types import TracebackType
from typing import Callable, TypeAlias

import bpy
import bpy.app.timers

_Task: TypeAlias = tuple[Callable[[], object], Future[object]]
_TASKS: queue.Queue[_Task] = queue.Queue()

def _runner() -> float | None:  # called by bpy.app.timers on main thread
    while not _TASKS.empty():
        func, fut = _TASKS.get_nowait()
        if fut.cancelled():
            continue
        try:
            fut.set_result(func())
        except Exception as exc:  # noqa: BLE001 – we want *any* error
            fut.set_exception(exc)
            traceback.print_exc()
    # keep the timer alive
    return 0.0  # run again on next idle frame

def submit(func: Callable[[], object]) -> Future[object]:
    """Schedule *func* to run on Blender’s main thread and return a Future."""
    import concurrent.futures
    fut: Future[object] = concurrent.futures.Future()
    _TASKS.put((func, fut))
    if not bpy.app.timers.is_registered(_runner):
        bpy.app.timers.register(_runner, first_interval=0.0, persistent=True)
    return fut
