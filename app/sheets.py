# sheets.py — только Google Sheets (Drive полностью убран)
from datetime import datetime
from typing import Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials
from dateutil import parser as dateparser

from config import SERVICE_ACCOUNT_FILE, SPREADSHEET_ID

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SYSTEM_SHEETS = {
    "Логи", "Услуги", "ШАБЛОН фин.проект", "Корзины",
    "Задачи", "Заработок"
}

def _get_client():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return gspread.authorize(creds)

# ---------- УСЛУГИ ----------
def load_services_from_sheet(services_data: Dict[str, dict]):
    """
    Читает лист 'Услуги' с колонками: Название | Цена_Физ | Цена_Юр
    Наполняет словарь services_data (ключи - строковые индексы).
    """
    try:
        client = _get_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet("Услуги")
        records = ws.get_all_records()
        services_data.clear()
        for i, r in enumerate(records):
            services_data[str(i)] = {
                "name": r.get("Название", f"Услуга {i}"),
                "price_physical": r.get("Цена_Физ", 0),
                "price_legal": r.get("Цена_Юр", 0),
            }
    except Exception as e:
        print("[sheets] load_services_from_sheet error:", e)

# ---------- ЛОГИ ----------
def log_login(employee_name: str, user_id: int):
    try:
        client = _get_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        try:
            ws = sh.worksheet("Логи")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="Логи", rows=2000, cols=10)
            ws.append_row(["Сотрудник", "UserID", "Время", "Событие"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ws.append_row([employee_name, str(user_id), now, "Вход"])
    except Exception as e:
        print("[sheets] log_login error:", e)

# ---------- КОРЗИНА (persist) ----------
def save_cart_to_sheet(user_id: int, order: dict):
    """
    Сохраняет корзину пользователя в лист 'Корзины'.
    Структура строк: user_id | проект | форма | услуга | количество | timestamp
    Перед записью переписываем лист без строк данного user_id.
    """
    try:
        client = _get_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        try:
            ws = sh.worksheet("Корзины")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="Корзины", rows=2000, cols=10)
            ws.append_row(["user_id", "project", "form", "service", "qty", "saved_at"])

        all_vals = ws.get_all_records()
        new_rows = []
        for row in all_vals:
            if str(row.get("user_id")) != str(user_id):
                new_rows.append([
                    row.get("user_id"),
                    row.get("project"),
                    row.get("form"),
                    row.get("service"),
                    row.get("qty"),
                    row.get("saved_at"),
                ])

        saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        project = order.get("project_name", "")
        form = order.get("form_type", "")
        for svc in order.get("services", []):
            new_rows.append([
                str(user_id),
                project,
                form,
                svc["name"],
                int(svc.get("qty", 1)),
                saved_at,
            ])

        ws.clear()
        ws.append_row(["user_id", "project", "form", "service", "qty", "saved_at"])
        if new_rows:
            ws.append_rows(new_rows)
    except Exception as e:
        print("[sheets] save_cart_to_sheet error:", e)

def load_cart_from_sheet(user_id: int) -> List[dict]:
    """Возвращает список позиций из 'Корзины' данного пользователя: [{name, qty}]"""
    try:
        client = _get_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        try:
            ws = sh.worksheet("Корзины")
        except gspread.WorksheetNotFound:
            return []
        res = []
        for r in ws.get_all_records():
            if str(r.get("user_id")) == str(user_id):
                name = r.get("service")
                qty = int(r.get("qty") or 1)
                res.append({"name": name, "qty": qty})
        return res
    except Exception as e:
        print("[sheets] load_cart_from_sheet error:", e)
        return []

# ---------- Сохранение заказа в шаблон ----------
def copy_template_and_fill_data(username: str, order: dict):
    """
    Копирует шаблон 'ШАБЛОН фин.проект' и заполняет:
      A1 - project_name
      B1 - form_type
      A5.. - названия услуг
      C5.. - количествo
    """
    try:
        client = _get_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        try:
            template = sh.worksheet("ШАБЛОН фин.проект")
        except gspread.WorksheetNotFound:
            print("[sheets] шаблон 'ШАБЛОН фин.проект' не найден")
            return

        project_name = order.get("project_name", "Проект")
        safe_title = "".join(c for c in project_name if c.isalnum() or c in (" ", "-", "_")).strip()
        if len(safe_title) > 30:
            safe_title = safe_title[:30]

        new_ws = template.duplicate(insert_sheet_index=0)
        new_ws.update_title(safe_title)

        new_ws.update("A1", [[project_name]])
        new_ws.update("B1", [[order.get("form_type", "")]])

        start_row = 5
        for i, svc in enumerate(order.get("services", []), start=start_row):
            new_ws.update(f"A{i}", [[svc["name"]]])
            new_ws.update(f"C{i}", [[int(svc.get("qty", 1))]])

        print(f"[sheets] Проект создан: {safe_title}")
    except Exception as e:
        print("[sheets] copy_template_and_fill_data error:", e)

# ---------- ЗАДАЧИ ----------
def load_tasks_for_employee(employee_name: str, mode: str = "active") -> list[dict]:
    """
    Читает лист 'Задачи'
    Ожидаемые колонки: Сотрудник | Название | Описание | Дедлайн | Статус | Проект
    """
    try:
        client = _get_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        try:
            ws = sh.worksheet("Задачи")
        except gspread.WorksheetNotFound:
            return []
        out = []
        for r in ws.get_all_records():
            if (r.get("Сотрудник") or "").strip() != (employee_name or "").strip():
                continue
            out.append({
                "employee": r.get("Сотрудник", ""),
                "title": r.get("Название", ""),
                "description": r.get("Описание", ""),
                "deadline": r.get("Дедлайн", ""),
                "status": r.get("Статус", ""),
                "project": r.get("Проект", ""),
            })
        return out
    except Exception as e:
        print("[sheets] load_tasks_for_employee error:", e)
        return []

def set_task_status(employee_name: str, title: str, deadline_str: str, new_status: str) -> bool:
    """Ищет строку по (Сотрудник, Название, Дедлайн) и меняет Статус."""
    try:
        client = _get_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet("Задачи")
        cells = ws.get_all_records()
        for idx, r in enumerate(cells, start=2):  # 1 — заголовки
            if (r.get("Сотрудник") or "").strip() == (employee_name or "").strip() \
               and (r.get("Название") or "").strip() == (title or "").strip() \
               and (r.get("Дедлайн") or "").strip() == (deadline_str or "").strip():
                ws.update(f"E{idx}", [[new_status]])
                return True
        return False
    except Exception as e:
        print("[sheets] set_task_status error:", e)
        return False

def add_task_row(employee_name: str, title: str, project: str, deadline: str, description: str, status: str = "В работе") -> bool:
    """Добавляет новую задачу строкой в лист 'Задачи'."""
    try:
        client = _get_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        try:
            ws = sh.worksheet("Задачи")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="Задачи", rows=2000, cols=10)
            ws.append_row(["Сотрудник", "Название", "Описание", "Дедлайн", "Статус", "Проект"])
        ws.append_row([employee_name, title, description, deadline, status, project])
        return True
    except Exception as e:
        print("[sheets] add_task_row error:", e)
        return False

# ---------- «Мой заработок» ----------
def load_earnings_for_employee(employee_name: str) -> Optional[dict]:
    """Читает лист 'Заработок': Сотрудник | Сумма до вычета налога | Налог | Чистая зарплата"""
    try:
        client = _get_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        try:
            ws = sh.worksheet("Заработок")
        except gspread.WorksheetNotFound:
            return None
        for r in ws.get_all_records():
            if (r.get("Сотрудник") or "").strip() == (employee_name or "").strip():
                return {
                    "gross": r.get("Сумма до вычета налога", 0),
                    "tax": r.get("Налог", 0),
                    "net": r.get("Чистая зарплата", 0),
                }
        return None
    except Exception as e:
        print("[sheets] load_earnings_for_employee error:", e)
        return None

# ---------- Проекты (все вкладки кроме системных) ----------
def list_all_projects() -> List[str]:
    """Список вкладок-«проектов» (все листы, кроме системных)."""
    try:
        client = _get_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        titles = []
        for ws in sh.worksheets():
            if ws.title not in SYSTEM_SHEETS:
                titles.append(ws.title)
        return titles
    except Exception as e:
        print("[sheets] list_all_projects error:", e)
        return []
