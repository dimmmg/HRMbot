from app.states import user_data


def get_service_status(user_id: int, service_name: str) -> bool:
    order = user_data.get(user_id, {}).get('order', {})
    services = order.get('services', [])
    return any(s['name'] == service_name for s in services)


def get_service_qty(user_id: int, service_name: str) -> int:
    order = user_data.get(user_id, {}).get('order', {})
    for s in order.get('services', []):
        if s['name'] == service_name:
            return int(s.get('qty', 1))
    return 0


def add_or_set_service(user_id: int, svc_name: str, price: float, qty: int = 1):
    if 'order' not in user_data[user_id]:
        user_data[user_id]['order'] = {}
    if 'services' not in user_data[user_id]['order']:
        user_data[user_id]['order']['services'] = []

    services = user_data[user_id]['order']['services']
    for s in services:
        if s['name'] == svc_name:
            s['qty'] = max(1, int(qty))
            s['price'] = float(price)
            return
    services.append({'name': svc_name, 'price': float(price), 'qty': max(1, int(qty))})


def remove_service(user_id: int, svc_name: str):
    services = user_data[user_id]['order'].get('services', [])
    user_data[user_id]['order']['services'] = [s for s in services if s['name'] != svc_name]


def change_qty(user_id: int, svc_name: str, delta: int):
    for s in user_data[user_id]['order'].get('services', []):
        if s['name'] == svc_name:
            s['qty'] = max(1, int(s.get('qty', 1)) + int(delta))
            return