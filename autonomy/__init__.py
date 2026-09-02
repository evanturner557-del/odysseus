"""Autonomous OS V1 — closed-loop autonomy loaded by Odysseus.

Named ``autonomy`` because ``os`` shadows the Python standard library.
HTTP APIs live under ``/api/os/``. Dashboard lives at ``/os``.
"""

__version__ = "1.0.0"

from autonomy.constants import (
    AUTONOMY_LEVELS,
    DEFAULT_AUTONOMY_LEVEL,
    ERROR_CLASSES,
    MEMORY_CLASSES,
    RISK_LEVELS,
)

__all__ = [
    "AUTONOMY_LEVELS",
    "DEFAULT_AUTONOMY_LEVEL",
    "ERROR_CLASSES",
    "MEMORY_CLASSES",
    "RISK_LEVELS",
    "__version__",
]
