"""Status comparison helper — tolerant of enum vs .value representations.

Nodes write status as AgentStatus.X.value (string) so State stays JSON/msgpack
safe. But framework lifecycle nodes or a resumed checkpoint may present status
as either the enum or the string. This helper normalizes before comparison so
upstream-error pass-through never misses (and never accidentally trips).
"""

from __future__ import annotations

from framework.schemas.agent_status import AgentStatus


def status_value(status) -> str:
    """Return the string form of a status (enum or str), '' if None."""
    if status is None:
        return ""
    if isinstance(status, AgentStatus):
        return status.value
    return str(status)


def is_error(status) -> bool:
    """True when status represents ERROR, regardless of enum/str form."""
    return status_value(status) == AgentStatus.ERROR.value
