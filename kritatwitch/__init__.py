from .kritatwitch import KritaTwitch
from krita import Krita

Krita.instance().addExtension(KritaTwitch(Krita.instance()))