"""Trabajo en segundo plano sin bloquear la ventana.

`run(fn, ...)` ejecuta `fn` en un hilo y entrega el resultado (o el error) en el hilo
de la interfaz. `on_main(fn)` lleva cualquier llamada al hilo de la interfaz.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal


class _Bridge(QObject):
    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def _run(fn: Callable[[], None]) -> None:
        fn()


_bridge: _Bridge | None = None
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="beatshell")


def init() -> None:
    """Llamar una vez desde el hilo principal, con la QApplication ya creada."""
    global _bridge
    _bridge = _Bridge()


def on_main(fn: Callable[[], None]) -> None:
    if _bridge is not None:
        _bridge.call.emit(fn)


def run(fn: Callable[..., Any], *args: Any,
        done: Callable[[Any], None] | None = None,
        fail: Callable[[Exception], None] | None = None) -> None:
    def job() -> None:
        try:
            result = fn(*args)
        except Exception as exc:  # noqa: BLE001 - se lo contamos a quien llamó
            if fail is not None:
                on_main(lambda exc=exc: fail(exc))
            return
        if done is not None:
            on_main(lambda: done(result))

    _pool.submit(job)


def shutdown() -> None:
    _pool.shutdown(wait=False, cancel_futures=True)
