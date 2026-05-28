import sys
import gspread
from google.oauth2.service_account import Credentials

from app.config import SERVICE_ACCOUNT_FILE, SPREADSHEET_ID

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def init_database():
    print("Подключение к Google Sheets...")
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        sh = client.open_by_key(SPREADSHEET_ID)
    except Exception as e:
        print(f"Ошибка подключения: {e}")
        print("Убедитесь, что service_account.json лежит в корне проекта, а SPREADSHEET_ID указан верно в config.py.")
        sys.exit(1)

    # Схема таблиц: название листа -> заголовки столбцов
    required_sheets = {
        "Услуги": ["Название", "Цена_Физ", "Цена_Юр"],
        "Заработок": ["Сотрудник", "Сумма до вычета налога", "Налог", "Чистая зарплата"],
        "ШАБЛОН фин.проект": ["Проект", "Форма работы", "", "", ""],
        "Задачи": ["Сотрудник", "Название", "Описание", "Дедлайн", "Статус", "Проект"],
        "Корзины": ["user_id", "project", "form", "service", "qty", "saved_at"],
        "Логи": ["Сотрудник", "UserID", "Время", "Событие"]
    }

    for title, headers in required_sheets.items():
        try:
            ws = sh.worksheet(title)
            print(f"Лист '{title}' уже существует.")
        except gspread.WorksheetNotFound:
            print(f"Создаю лист '{title}'...")
            ws = sh.add_worksheet(title=title, rows=1000, cols=10)
            if headers:
                ws.append_row(headers)

    # Разметка ячеек в шаблоне
    try:
        ws = sh.worksheet("ШАБЛОН фин.проект")
        ws.update("A4:C4", [["Услуга", "", "Количество"]])
    except Exception:
        pass

    # Очистка от мусора (удаляем дефолтный лист, если он остался)
    try:
        sheet1 = sh.worksheet("Лист 1")
        sh.del_worksheet(sheet1)
        print("Дефолтный 'Лист 1' удален.")
    except gspread.WorksheetNotFound:
        pass
    except Exception:
        pass

    print("База данных успешно инициализирована! Теперь можно наполнить лист 'Услуги' и запускать бота.")

if __name__ == "__main__":
    init_database()