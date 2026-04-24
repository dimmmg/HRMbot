import pytest
from app.states import user_data
from packages.core.cart import (
    add_or_set_service,
    change_qty,
    remove_service,
    get_service_qty
)


@pytest.fixture(autouse=True)
def setup_user_state():
    user_data.clear()
    user_data[123] = {
        "order": {"services": []}
    }
    yield
    user_data.clear()


def test_add_new_service_to_cart():
    add_or_set_service(user_id=123, svc_name="Аудит", price=5000.0, qty=1)

    assert get_service_qty(123, "Аудит") == 1

    services = user_data[123]["order"]["services"]
    assert len(services) == 1
    assert services[0]["name"] == "Аудит"
    assert services[0]["price"] == 5000.0


def test_change_service_quantity():
    add_or_set_service(123, "Консультация", 2000.0, 1)
    change_qty(123, "Консультация", 2)

    assert get_service_qty(123, "Консультация") == 3


def test_remove_service_from_cart():
    add_or_set_service(123, "Услуга А", 100.0, 1)
    add_or_set_service(123, "Услуга Б", 200.0, 1)

    remove_service(123, "Услуга А")

    assert get_service_qty(123, "Услуга А") == 0
    assert get_service_qty(123, "Услуга Б") == 1