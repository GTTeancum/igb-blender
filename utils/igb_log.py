"""Central IGB operation log.

Importers/exporters report through per-module _report helpers; those now
also append here so users can SEE what happened (and copy errors) without
opening the system console. Shown by the "IGB Log" panel in the IGB tab.
"""

import time
from collections import deque

_MAX = 200
_entries = deque(maxlen=_MAX)


def log(level, message):
    """Append a log entry. level: 'INFO' | 'WARNING' | 'ERROR'."""
    _entries.append((time.strftime("%H:%M:%S"), str(level), str(message)))


def entries(last=None):
    items = list(_entries)
    return items[-last:] if last else items


def clear():
    _entries.clear()


def as_text():
    return "\n".join(f"[{t}] {lv:7} {m}" for t, lv, m in _entries)
