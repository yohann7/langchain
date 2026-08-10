"""Concurrency coordination for reads, mutations, and maintenance."""

from contextlib import contextmanager
from threading import Condition, Lock
from typing import Iterator


class MaintenanceActiveError(RuntimeError):
    """New work is rejected while exclusive maintenance is pending."""


class OperationCoordinator:
    def __init__(self) -> None:
        self._condition = Condition()
        self._mutation_lock = Lock()
        self._maintenance_lock = Lock()
        self._active_operations = 0
        self._maintenance_pending = False
        self._maintenance_active = False

    @property
    def maintenance_pending(self) -> bool:
        with self._condition:
            return self._maintenance_pending or self._maintenance_active

    @contextmanager
    def read(self) -> Iterator[None]:
        with self._condition:
            if self._maintenance_pending or self._maintenance_active:
                raise MaintenanceActiveError("Knowledge maintenance is active")
            self._active_operations += 1
        try:
            yield
        finally:
            with self._condition:
                self._active_operations -= 1
                if self._active_operations == 0:
                    self._condition.notify_all()

    @contextmanager
    def mutation(self) -> Iterator[None]:
        with self._mutation_lock:
            with self.read():
                yield

    @contextmanager
    def maintenance(self) -> Iterator[None]:
        with self._maintenance_lock:
            with self._condition:
                self._maintenance_pending = True
                while self._active_operations:
                    self._condition.wait()
                self._maintenance_active = True
            try:
                yield
            finally:
                with self._condition:
                    self._maintenance_active = False
                    self._maintenance_pending = False
                    self._condition.notify_all()
