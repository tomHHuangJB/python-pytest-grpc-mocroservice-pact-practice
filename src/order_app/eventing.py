from dataclasses import asdict, dataclass
from typing import Callable


@dataclass(frozen=True)
class OrderCreatedEvent:
    order_id: str
    customer_id: str
    sku: str
    quantity: int
    total_price: float


class InMemoryEventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[object], None]]] = {}
        self.published_events: list[tuple[str, object]] = []

    def subscribe(self, event_name: str, handler: Callable[[object], None]) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    def publish(self, event_name: str, payload: object) -> None:
        self.published_events.append((event_name, payload))
        for handler in self._handlers.get(event_name, []):
            handler(payload)


class OrderAuditLog:
    def __init__(self) -> None:
        self.entries: list[dict[str, object]] = []

    def handle_order_created(self, event: OrderCreatedEvent) -> None:
        self.entries.append(asdict(event))
