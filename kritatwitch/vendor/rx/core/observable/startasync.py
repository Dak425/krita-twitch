from typing import Callable
from asyncio import Future

from kritatwitch.vendor.rx import throw, from_future
from kritatwitch.vendor.rx.core import Observable


def _start_async(function_async: Callable[[], Future]) -> Observable:
    try:
        future = function_async()
    except Exception as ex:  # pylint: disable=broad-except
        return throw(ex)

    return from_future(future)
