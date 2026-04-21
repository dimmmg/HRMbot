from enum import IntEnum
from typing import Dict

class UserState(IntEnum):
    MAIN_MENU = 0

    # Оформление заказа
    ORDER_FORM = 10
    ORDER_PROJECT_NAME = 11
    ORDER_SERVICES = 12
    ORDER_CONFIRMATION = 13

    # Мастер добавления задачи (админ)
    ADD_TASK_SELECT_EMPLOYEE = 30
    ADD_TASK_PROJECT = 31
    ADD_TASK_TITLE = 32
    ADD_TASK_DEADLINE = 33
    ADD_TASK_DESCRIPTION = 34

# В памяти: user_data[user_id] = { state, username, order, ... }
user_data: Dict[int, dict] = {}

# Услуги из Google Sheets: services_data[id_str] = {name, price_physical, price_legal}
services_data: Dict[str, dict] = {}
