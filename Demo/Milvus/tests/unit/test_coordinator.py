from threading import Event, Thread
from time import monotonic, sleep

import pytest

from knowledge_service.coordinator import MaintenanceActiveError, OperationCoordinator


def test_reads_can_overlap_but_maintenance_rejects_new_reads() -> None:
    coordinator = OperationCoordinator()
    first_read_started = Event()
    release_first_read = Event()
    maintenance_entered = Event()

    def reader() -> None:
        with coordinator.read():
            first_read_started.set()
            release_first_read.wait(timeout=2)

    def maintainer() -> None:
        with coordinator.maintenance():
            maintenance_entered.set()

    read_thread = Thread(target=reader)
    read_thread.start()
    assert first_read_started.wait(timeout=1)

    maintenance_thread = Thread(target=maintainer)
    maintenance_thread.start()
    deadline = monotonic() + 1
    while not coordinator.maintenance_pending and monotonic() < deadline:
        sleep(0.01)
    with pytest.raises(MaintenanceActiveError):
        with coordinator.read():
            pass

    release_first_read.set()
    read_thread.join(timeout=1)
    assert maintenance_entered.wait(timeout=1)
    maintenance_thread.join(timeout=1)


def test_mutations_are_serialized() -> None:
    coordinator = OperationCoordinator()
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    def first() -> None:
        with coordinator.mutation():
            first_entered.set()
            release_first.wait(timeout=2)

    def second() -> None:
        with coordinator.mutation():
            second_entered.set()

    first_thread = Thread(target=first)
    second_thread = Thread(target=second)
    first_thread.start()
    assert first_entered.wait(timeout=1)
    second_thread.start()
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)
    assert second_entered.is_set()
