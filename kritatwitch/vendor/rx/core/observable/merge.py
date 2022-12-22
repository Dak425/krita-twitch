
from kritatwitch.vendor import rx
from kritatwitch.vendor.rx import operators as ops
from kritatwitch.vendor.rx.core import Observable


def _merge(*sources: Observable) -> Observable:
    return rx.from_iterable(sources).pipe(ops.merge_all())
