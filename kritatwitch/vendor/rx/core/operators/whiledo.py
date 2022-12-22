from asyncio import Future
from typing import cast, Callable, Union
import itertools

from kritatwitch.vendor import rx
from kritatwitch.vendor.rx.core import Observable
from kritatwitch.vendor.rx.core.typing import Predicate
from kritatwitch.vendor.rx.internal.utils import is_future, infinite


def _while_do(condition: Predicate) -> Callable[[Observable], Observable]:
    def while_do(source: Union[Observable, Future]) -> Observable:
        """Repeats source as long as condition holds emulating a while
        loop.

        Args:
            source: The observable sequence that will be run if the
                condition function returns true.

        Returns:
            An observable sequence which is repeated as long as the
            condition holds.
        """
        if is_future(source):
            obs = rx.from_future(cast(Future, source))
        else:
            obs = cast(Observable, source)
        it = itertools.takewhile(condition, (obs for _ in infinite()))
        return rx.concat_with_iterable(it)
    return while_do
