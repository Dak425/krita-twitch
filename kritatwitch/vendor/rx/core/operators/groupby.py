from typing import Callable, Optional

from kritatwitch.vendor import rx
from kritatwitch.vendor.rx import operators as ops
from kritatwitch.vendor.rx.core import Observable
from kritatwitch.vendor.rx.core.typing import Mapper
from kritatwitch.vendor.rx.subject import Subject


def _group_by(key_mapper: Mapper,
              element_mapper: Optional[Mapper] = None,
              subject_mapper: Optional[Callable[[], Subject]] = None,
              ) -> Callable[[Observable], Observable]:

    def duration_mapper(_):
        return rx.never()

    return ops.group_by_until(key_mapper, element_mapper, duration_mapper, subject_mapper)
