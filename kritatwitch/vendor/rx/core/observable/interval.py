from typing import Optional

from kritatwitch.vendor.rx import timer
from kritatwitch.vendor.rx.core import Observable, typing


def _interval(period: typing.RelativeTime,
              scheduler: Optional[typing.Scheduler] = None
              ) -> Observable:

    return timer(period, period, scheduler)
