from typing import Callable

from kritatwitch.vendor import rx
from kritatwitch.vendor.rx.core import Observable


def _on_error_resume_next(second: Observable) -> Callable[[Observable], Observable]:
    def on_error_resume_next(source: Observable) -> Observable:
        return rx.on_error_resume_next(source, second)
    return on_error_resume_next
