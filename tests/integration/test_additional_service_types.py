import pytest
from fastapi.testclient import TestClient

from order_app.api import create_app
from order_app.eventing import InMemoryEventBus, OrderAuditLog, OrderCreatedEvent
from order_app.repository import InMemoryOrderRepository
from order_app.service import OrderService
from tests.helpers.inventory import ApiInventoryStub


def _build_client(mode: str = "ok") -> tuple[TestClient, InMemoryOrderRepository, ApiInventoryStub]:
    repository = InMemoryOrderRepository()
    inventory = ApiInventoryStub(mode=mode)
    service = OrderService(repository=repository, inventory_client=inventory)
    return TestClient(create_app(order_service=service)), repository, inventory


@pytest.mark.api
def test_graphql_mutation_and_query_cover_create_and_read_flow() -> None:
    client, repository, inventory = _build_client()

    create_response = client.post(
        "/graphql",
        json={
            "query": "mutation CreateOrder($input: OrderInput!) { createOrder(input: $input) { order_id customer_id total_price } }",
            "variables": {
                "input": {"customer_id": "cust-123", "sku": "sku-001", "quantity": 2, "unit_price": 19.99}
            },
        },
    )

    created_order_id = create_response.json()["data"]["createOrder"]["order_id"]
    get_response = client.post(
        "/graphql",
        json={
            "query": "query GetOrder($orderId: String!) { order(orderId: $orderId) { order_id sku quantity } }",
            "variables": {"orderId": created_order_id},
        },
    )

    assert create_response.status_code == 200
    assert get_response.status_code == 200
    assert get_response.json()["data"]["order"]["order_id"] == created_order_id
    assert repository.count() == 1
    assert inventory.calls == [("sku-001", 2)]


@pytest.mark.api
def test_soap_create_order_returns_xml_and_persists_order() -> None:
    client, repository, inventory = _build_client()
    body = """
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
      <soap:Body>
        <CreateOrderRequest>
          <CustomerId>cust-123</CustomerId>
          <Sku>sku-001</Sku>
          <Quantity>2</Quantity>
          <UnitPrice>19.99</UnitPrice>
        </CreateOrderRequest>
      </soap:Body>
    </soap:Envelope>
    """.strip()

    response = client.post("/soap/orders", content=body, headers={"Content-Type": "application/xml"})

    assert response.status_code == 200
    assert "<CreateOrderResponse>" in response.text
    assert "<CustomerId>cust-123</CustomerId>" in response.text
    assert repository.count() == 1
    assert inventory.calls == [("sku-001", 2)]


@pytest.mark.integration
def test_websocket_order_flow_pushes_created_event() -> None:
    client, repository, inventory = _build_client()

    with client.websocket_connect("/ws/orders") as websocket:
        websocket.send_json({"customer_id": "cust-123", "sku": "sku-001", "quantity": 2, "unit_price": 19.99})
        event = websocket.receive_json()

    assert event["event"] == "order.created"
    assert event["order"]["customer_id"] == "cust-123"
    assert repository.count() == 1
    assert inventory.calls == [("sku-001", 2)]


@pytest.mark.api
def test_webhook_endpoint_accepts_signed_callbacks_and_records_event() -> None:
    client, _, _ = _build_client()

    response = client.post(
        "/webhooks/inventory-events",
        json={"event_type": "inventory.adjusted", "sku": "sku-001", "quantity": 2, "status": "processed"},
        headers={"X-Webhook-Token": "practice-secret"},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert client.app.state.webhook_events == [
        {"event_type": "inventory.adjusted", "sku": "sku-001", "quantity": 2, "status": "processed"}
    ]


@pytest.mark.integration
def test_message_driven_order_created_event_reaches_audit_subscriber(order_request) -> None:
    repository = InMemoryOrderRepository()
    inventory = ApiInventoryStub(mode="ok")
    event_bus = InMemoryEventBus()
    audit_log = OrderAuditLog()
    event_bus.subscribe("order.created", audit_log.handle_order_created)
    service = OrderService(
        repository=repository,
        inventory_client=inventory,
        id_factory=lambda: "evt-order-001",
        event_bus=event_bus,
    )

    order = service.create_order(order_request)

    assert order.order_id == "evt-order-001"
    assert event_bus.published_events == [
        (
            "order.created",
            OrderCreatedEvent(
                order_id="evt-order-001",
                customer_id="cust-123",
                sku="sku-001",
                quantity=2,
                total_price=39.98,
            ),
        )
    ]
    assert audit_log.entries == [
        {
            "order_id": "evt-order-001",
            "customer_id": "cust-123",
            "sku": "sku-001",
            "quantity": 2,
            "total_price": 39.98,
        }
    ]
