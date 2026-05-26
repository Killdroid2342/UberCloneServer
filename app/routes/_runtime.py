import sys
from types import ModuleType


def rideops_runtime() -> ModuleType:
    return sys.modules["app.main"]
