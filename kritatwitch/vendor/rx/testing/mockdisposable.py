from typing import List

from kritatwitch.vendor.rx.core import typing
from kritatwitch.vendor.rx.scheduler import VirtualTimeScheduler


class MockDisposable:
    def __init__(self, scheduler: VirtualTimeScheduler):
        self.scheduler: VirtualTimeScheduler = scheduler
        self.disposes: List[typing.AbsoluteTime] = []
        self.disposes.append(self.scheduler.clock)

    def dispose(self):
        self.disposes.append(self.scheduler.clock)
