from typing import Any, Callable, Optional

from kritatwitch.vendor.rx import operators as ops
from kritatwitch.vendor.rx.core import Observable, pipe
from kritatwitch.vendor.rx.core.typing import Comparer
from kritatwitch.vendor.rx.internal.basic import default_comparer


def _contains(value: Any,
              comparer: Optional[Comparer] = None
              ) -> Callable[[Observable], Observable]:
    comparer_ = comparer or default_comparer

    filtering = ops.filter(lambda v: comparer_(v, value))
    something = ops.some()

    return pipe(filtering, something)
