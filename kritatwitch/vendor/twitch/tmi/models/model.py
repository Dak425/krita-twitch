from abc import ABCMeta
from dataclasses import dataclass
from typing import Optional, Dict, Any

from kritatwitch.vendor.twitch.api import API


@dataclass
class Model(metaclass=ABCMeta):
    _api: Optional[API]
    data: Optional[Dict[str, Any]]
