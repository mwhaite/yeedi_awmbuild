"""Shared helpers for Yeedi entity platforms.

Each platform maps event names from the polled state dict to entity values.
The polled state is shaped like `{event_name: value}` where value is what the
JS lib would have placed at `msg.payload.value` of the MQTT message.

In Node-RED, the ha-sensor's `state` field is a JS-style path into the
MQTT message. The path always starts with "payload." (since `msg.payload`
is the message body). Examples from Tomschin's flow:
  - BatteryInfo        -> "payload.value"
  - LastCleanLogs      -> "payload.value.totalTimeFormatted"
  - info (nickname)    -> "payload.value.nickname"

Our polled state already drops the `msg.payload` wrapper - it stores the
inner value directly under the event name. So `extract()` peels off the
"payload." prefix and walks the rest.
"""

from __future__ import annotations

from typing import Any


def extract(state: dict[str, Any], event: str, path: str) -> Any:
    """Pull a value from the polled state dict by event name + JS-style path.

    The polled state is keyed by event name (e.g. "BatteryInfo") and stores
    the inner value. The flow's sensor `state` field is a JS-style dotted
    path inside the MQTT message payload, e.g. "payload.value.totalTimeFormatted"
    - we strip the "payload." prefix and walk the rest.
    """
    raw = state.get(event)
    if raw is None:
        return None

    # Strip leading "payload.value." (most paths in the flow start there).
    if path.startswith("payload.value."):
        path = path[len("payload.value.") :]
    elif path == "payload.value":
        path = ""
    elif path.startswith("payload."):
        # Defensive: some unusual path may only have "payload." prefix.
        path = path[len("payload.") :]
    elif path.startswith("payload/"):
        path = path[len("payload/") :]

    # If nothing remains, the raw is the answer.
    if path == "":
        return raw

    parts = path.split(".")
    cur: Any = raw
    for part in parts:
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur