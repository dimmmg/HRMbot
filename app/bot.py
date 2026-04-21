# bot.py
import os
import logging
from datetime import time as dtime

# На Linux ускоряет event loop (на Windows просто игнорируется)
try:
    import uvloop  # type: ignore
    uvloop.install()
except Exception:
    pass

from logging.handlers import RotatingFileHandler
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import BOT_TOKEN
import handlers
import sheets
from states import services_data  # <-- добавили

# ---------- Логирование ----------
LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)
log_path = os.path.join(LOGS_DIR, "bot.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
        logging.StreamHandler()
    ],
)

async def log_error(update, context):
    logging.exception("Unhandled error", exc_info=context.error)

def main():
    # Предзагрузка услуг + быстрых мап цен
    sheets.load_services_from_sheet(services_data)  # <-- передаём dict
    handlers.prime_price_maps()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(log_error)

    # Команды
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("rotate_logs", handlers.clear_logs))  # ручная очистка логов

    # Текстовые кнопки
    app.add_handler(MessageHandler(filters.Regex(r"^(Оформить заказ)$"), handlers.start_order))
    app.add_handler(MessageHandler(filters.Regex(r"^(Мои задачи)$"), handlers.my_tasks))
    app.add_handler(MessageHandler(filters.Regex(r"^(Мой заработок)$"), handlers.my_earnings))
    app.add_handler(MessageHandler(filters.Regex(r"^(➕ Добавить задачу)$"), handlers.start_add_task))

    # CallbackQuery по паттернам
    app.add_handler(CallbackQueryHandler(handlers.handle_callback_query, pattern=r"^task_emp:\d+$"))
    app.add_handler(CallbackQueryHandler(handlers.handle_callback_query, pattern=r"^page:\d+$"))
    app.add_handler(CallbackQueryHandler(handlers.handle_callback_query, pattern=r"^form_(physical|legal)$"))
    app.add_handler(CallbackQueryHandler(handlers.handle_callback_query, pattern=r"^(select|manage|qty_inc|qty_dec|qty_set):"))
    app.add_handler(CallbackQueryHandler(handlers.handle_callback_query, pattern=r"^(show_cart|reset_services|finish_services|confirm_order|cancel_order)$"))
    app.add_handler(CallbackQueryHandler(handlers.handle_callback_query, pattern=r"^task_done:\d+$"))
    app.add_handler(CallbackQueryHandler(handlers.handle_callback_query, pattern=r"^tasks_filter:(active|done)$"))
    # fallback
    app.add_handler(CallbackQueryHandler(handlers.handle_callback_query))

    # Текст (ввод названия проекта, мастер задач)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text))

    # --- Джобы: логи и напоминания ---
    app.job_queue.run_repeating(handlers.logs_push_job, interval=6*3600, first=60, name="logs_push_job")
    app.job_queue.run_repeating(handlers.reminders_hourly, interval=3600, first=300, name="reminders_hourly")
    # Ежедневно 09:00
    app.job_queue.run_daily(handlers.reminders_daily, time=dtime(hour=9, minute=0), name="reminders_daily")
    # Еженедельно по субботам 10:00
    app.job_queue.run_daily(handlers.reminders_weekly, time=dtime(hour=10, minute=0), days=(6,), name="reminders_weekly")

    print("Бот запущен...")
    app.run_polling(drop_pending_updates=True, timeout=20, poll_interval=0.0)

if __name__ == "__main__":
    main()
