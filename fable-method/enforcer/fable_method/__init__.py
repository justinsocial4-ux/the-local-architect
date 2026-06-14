"""
fable_method — reasoning-rigor enforcement engine.

Public surface:
    Engine                  — class; instantiate with store_dir
    create_session          — module-level convenience wrapper
    get_state               — module-level convenience wrapper
    submit                  — module-level convenience wrapper
    finalize                — module-level convenience wrapper
    set_rigor               — module-level convenience wrapper
"""

from .engine import (
    Engine,
    create_session,
    get_state,
    submit,
    finalize,
    set_rigor,
)

__all__ = [
    "Engine",
    "create_session",
    "get_state",
    "submit",
    "finalize",
    "set_rigor",
]
