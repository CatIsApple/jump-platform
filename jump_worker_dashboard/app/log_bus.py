from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from queue import SimpleQueue


@dataclass
class LogEvent:
    timestamp: str
    level: str
    message: str


class LogBus:
    def __init__(self) -> None:
        self._queue: SimpleQueue[LogEvent] = SimpleQueue()

    def emit(self, message: str, level: str = "INFO") -> None:
        event = LogEvent(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            level=level.upper(),
            message=message,
        )
        self._queue.put(event)

    def poll(self) -> list[LogEvent]:
        events: list[LogEvent] = []
        while not self._queue.empty():
            events.append(self._queue.get())
        return events
