import os
import io
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InputFile,
)
from telegram.ext import ContextTypes

from app import sheets
from app.config import ADMINS, EMPLOYEE_NAMES
from app.states import user_data, UserState, services_data

from packages.core.cart import (
    get_service_status,
    get_service_qty,
    add_or_set_service,
    remove_service,
    change_qty
)

PAGE_SIZE = 5
LOGS_DIR = "logs"
LOG_TAIL_LINES = 300

PRICE_PHYSICAL: Dict[str, float] = {}
PRICE_LEGAL: Dict[str, float] = {}

def rebuild_price_maps():
    PRICE_PHYSICAL.clear()
    PRICE_LEGAL.clear()
    for s in services_data.values():
        name = s.get("name", "")
        if not name:
            continue
        PRICE_PHYSICAL[name] = float(s.get("price_physical", 0) or 0)
        PRICE_LEGAL[name] = float(s.get("price_legal", 0) or 0)

def prime_price_maps():
    rebuild_price_maps()

def _ensure_session(update: Update) -> int:
    user = update.effective_user
    user_id = user.id
    if user_id not in user_data:
        user_data[user_id] = {
            "state": UserState.MAIN_MENU,
            "username": EMPLOYEE_NAMES.get(user_id, user.full_name),
            "order": {},
            "tmp_task": {},
        }
    return user_id

def _build_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("Оформить заказ")],
        [KeyboardButton("Мои задачи"), KeyboardButton("Мой заработок")],
    ]
    if user_id in ADMINS:
        rows.append([KeyboardButton("➕ Добавить задачу")])
        rows.append([KeyboardButton("📄 Логи сейчас"), KeyboardButton("♻️ Очистить логи")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

async def _safe_edit(query, text, reply_markup=None, parse_mode="Markdown"):
    try:
        msg = query.message
        if msg and msg.text == text:
            if reply_markup is not None:
                await query.edit_message_reply_markup(reply_markup=reply_markup)
            return
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        pass

def _flush_log_handlers():
    try:
        logger = logging.getLogger()
        for h in getattr(logger, "handlers", []):
            try:
                h.flush()
            except Exception:
                pass
    except Exception:
        pass

def _tail_file(path: str, lines: int = 200) -> bytes:
    try:
        with open(path, "rb") as f:
            data = f.read()
        parts = data.splitlines()[-lines:]
        return b"\n".join(parts) + b"\n"
    except Exception:
        return b""

def _schedule_cart_sync(context: ContextTypes.DEFAULT_TYPE, user_id: int, delay: float = 1.5):
    name = f"cart_sync_{user_id}"
    for job in context.job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    context.job_queue.run_once(persist_cart_job, delay, name=name, data={"user_id": user_id})

async def persist_cart_job(context):
    user_id = context.job.data["user_id"]
    order = user_data.get(user_id, {}).get("order", {})
    if not order:
        return
    await asyncio.to_thread(sheets.save_cart_to_sheet, user_id, order)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = _ensure_session(update)
    user_data[user_id]['state'] = UserState.MAIN_MENU
    await update.message.reply_text(
        f"👋 Привет, {user_data[user_id]['username']}!",
        reply_markup=_build_main_keyboard(user_id)
    )

async def start_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = _ensure_session(update)
    user_data[user_id]['state'] = UserState.ORDER_FORM
    kb = [
        [InlineKeyboardButton("Физическое лицо", callback_data='form_physical')],
        [InlineKeyboardButton("Юридическое лицо", callback_data='form_legal')],
    ]
    await update.message.reply_text("Выберите форму работы:", reply_markup=InlineKeyboardMarkup(kb))

async def my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = _ensure_session(update)
    await _render_tasks(update, context, mode="active")

async def my_earnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = _ensure_session(update)
    employee = user_data[user_id]['username']
    row = await asyncio.to_thread(sheets.load_earnings, employee)
    if not row:
        await update.message.reply_text("Пока нет данных о заработке.")
        return
    gross = row.get("Сумма до вычета налога") or row.get("gross") or row.get("До налога")
    tax = row.get("Налог") or row.get("tax")
    net = row.get("Чистая зарплата") or row.get("net") or row.get("К выплате")
    text = (
        "💰 *Мой заработок:*\n\n"
        f"Сумма до налога: {gross}\n"
        f"Налог: {tax}\n"
        f"Чистая зарплата: *{net}*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def start_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = _ensure_session(update)
    if user_id not in ADMINS:
        await update.message.reply_text("⛔ Доступно только администраторам.")
        return
    kb = []
    for uid, name in EMPLOYEE_NAMES.items():
        kb.append([InlineKeyboardButton(name, callback_data=f"task_emp:{uid}")])
    await update.message.reply_text("Кому поставить задачу?", reply_markup=InlineKeyboardMarkup(kb))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = _ensure_session(update)
    text = (update.message.text or "").strip()

    if text == "📄 Логи сейчас" and user_id in ADMINS:
        await send_logs_now(update, context)
        return

    if text == "♻️ Очистить логи" and user_id in ADMINS:
        await clear_logs(update, context)
        return

    if user_data[user_id].get('state') == UserState.ORDER_PROJECT_NAME:
        project = text
        if not project:
            await update.message.reply_text("❌ Название проекта не может быть пустым.")
            return
        user_data[user_id]['order']['project_name'] = project

        saved = await asyncio.to_thread(sheets.load_cart_from_sheet, user_id)
        if saved:
            restored = []
            for item in saved:
                name = item['name']
                qty = int(item.get('qty', 1))
                price = PRICE_PHYSICAL.get(name, 0.0)
                if user_data[user_id]['order'].get('form_type') == 'Юридическое лицо':
                    price = PRICE_LEGAL.get(name, 0.0)
                restored.append({'name': name, 'price': price, 'qty': qty})
            user_data[user_id]['order']['services'] = restored

        user_data[user_id]['state'] = UserState.ORDER_SERVICES
        await show_services_page(update, context, page=0)
        return

    adding = user_data[user_id].get("tmp_task", {})
    stage = adding.get("stage")
    if stage == "title":
        adding["title"] = text
        adding["stage"] = "deadline"
        await update.message.reply_text("⏰ Введите дедлайн (формат: ДД.ММ.ГГГГ ЧЧ:ММ):")
        return
    if stage == "deadline":
        adding["deadline"] = text
        adding["stage"] = "project"
        await update.message.reply_text("📦 Введите название проекта для задачи:")
        return
    if stage == "project":
        adding["project"] = text
        employee = adding.get("employee_name")
        title = adding.get("title")
        deadline = adding.get("deadline")
        project = adding.get("project")
        ok = await asyncio.to_thread(sheets.add_task_row, employee, title, deadline, project)
        if ok:
            await update.message.reply_text("✅ Задача добавлена.")
            target_uid = adding.get("employee_id")
            if target_uid:
                try:
                    await context.bot.send_message(
                        chat_id=target_uid,
                        text=f"🆕 Вам поставлена задача:\n*{title}*\nПроект: {project}\nДедлайн: {deadline}",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
        else:
            await update.message.reply_text("❌ Не удалось добавить задачу.")
        user_data[user_id]["tmp_task"] = {}
        return

    await update.message.reply_text("⚠️ Я не понял это сообщение. Используйте меню.", reply_markup=_build_main_keyboard(user_id))

async def show_services_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    all_ids = list(services_data.keys())
    if not all_ids:
        await (update.callback_query.message.edit_text if update.callback_query else update.message.reply_text)(
            "❌ Услуги не найдены."
        )
        return

    total_pages = max(1, (len(all_ids) - 1) // PAGE_SIZE + 1)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_ids = all_ids[start:start + PAGE_SIZE]

    user_id = _ensure_session(update)
    form_type = user_data[user_id]['order'].get('form_type', 'Физическое лицо')

    message = f"📋 *Доступные услуги (страница {page+1}/{total_pages})*\n\n"
    keyboard = []

    for sid in page_ids:
        svc = services_data[sid]
        name = svc['name']
        price = PRICE_PHYSICAL.get(name, 0.0) if form_type == 'Физическое лицо' else PRICE_LEGAL.get(name, 0.0)
        selected = get_service_status(user_id, name)
        mark = "✅" if selected else "❌"
        qty = get_service_qty(user_id, name)
        message += f"{mark} *{name}* — {int(price)} руб./шт."
        if selected:
            message += f"  (×{qty})"
        message += "\n"
        if selected:
            keyboard.append([InlineKeyboardButton(f"✏️ {name}", callback_data=f"manage:{sid}")])
        else:
            keyboard.append([InlineKeyboardButton(f"{name}", callback_data=f"select:{sid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"page:{page-1}"))
    nav.append(InlineKeyboardButton("🛒 Корзина", callback_data="show_cart"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"page:{page+1}"))
    keyboard.append(nav)
    keyboard.append([
        InlineKeyboardButton("🔄 Сбросить выбор", callback_data="reset_services"),
        InlineKeyboardButton("✅ Завершить выбор", callback_data="finish_services"),
    ])
    markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await _safe_edit(update.callback_query, message, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(message, reply_markup=markup, parse_mode="Markdown")

async def show_manage_qty_ui(update: Update, context: ContextTypes.DEFAULT_TYPE, sid: str):
    svc = services_data.get(sid)
    if not svc:
        await update.callback_query.answer("Услуга не найдена")
        return
    user_id = _ensure_session(update)
    form_type = user_data[user_id]['order'].get('form_type', 'Физическое лицо')
    name = svc['name']
    price = PRICE_PHYSICAL.get(name, 0.0) if form_type == 'Физическое лицо' else PRICE_LEGAL.get(name, 0.0)

    qty = get_service_qty(user_id, name)
    if qty <= 0:
        add_or_set_service(user_id, name, price, 1)
        _schedule_cart_sync(context, user_id)

    qty = get_service_qty(user_id, name)
    message = f"*{name}* — {int(price)} руб./шт.\n\nКоличество: *{qty}*"
    kb = [
        [
            InlineKeyboardButton("➖", callback_data=f"qty_dec:{sid}"),
            InlineKeyboardButton(str(qty), callback_data=f"qty_nop:{sid}"),
            InlineKeyboardButton("➕", callback_data=f"qty_inc:{sid}")
        ],
        [
            InlineKeyboardButton("✅ Готово", callback_data=f"page:0"),
            InlineKeyboardButton("❌ Убрать из заказа", callback_data=f"qty_set:{sid}")
        ],
        [InlineKeyboardButton("🛒 Корзина", callback_data="show_cart")]
    ]
    if update.callback_query:
        await _safe_edit(update.callback_query, message, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = _ensure_session(update)
    order = user_data[user_id]['order']
    services = order.get('services', [])
    if not services:
        await _safe_edit(update.callback_query, "🛒 Ваша корзина пуста.") if update.callback_query else update.message.reply_text("🛒 Ваша корзина пуста.")
        return

    form_type = order.get('form_type', 'Физическое лицо')
    message = "🛒 *Ваша корзина:*\n\n"
    total = 0
    for i, s in enumerate(services, 1):
        name = s['name']
        qty = int(s.get('qty', 1))
        price_per = PRICE_PHYSICAL.get(name, 0.0) if form_type == 'Физическое лицо' else PRICE_LEGAL.get(name, 0.0)
        line_sum = int(price_per) * qty
        total += line_sum
        message += f"{i}. *{name}* × {qty} = {line_sum} руб.\n"
    message += f"\n*Итого:* {total} руб."

    kb = []
    for sid, sd in services_data.items():
        in_cart = any(s['name'] == sd['name'] for s in services)
        if in_cart:
            kb.append([
                InlineKeyboardButton(f"➕ {sd['name']}", callback_data=f"cart_inc:{sid}"),
                InlineKeyboardButton(f"➖", callback_data=f"cart_dec:{sid}"),
                InlineKeyboardButton("❌ Удалить", callback_data=f"cart_del:{sid}")
            ])
    kb.append([InlineKeyboardButton("✅ Подтвердить заказ", callback_data="confirm_order")])
    kb.append([InlineKeyboardButton("⬅️ Назад к услугам", callback_data="page:0")])

    if update.callback_query:
        await _safe_edit(update.callback_query, message, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def show_order_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = _ensure_session(update)
    order = user_data[user_id]['order']
    services = order.get('services', [])
    if not services:
        await _safe_edit(update.callback_query, "❌ Вы не выбрали ни одной услуги.")
        return

    message = f"📦 *Подтверждение заказа*\n\n"
    message += f"*Проект:* {order.get('project_name','')}\n"
    message += f"*Форма:* {order.get('form_type','')}\n\n"
    total = 0
    form_type = order.get('form_type', 'Физическое лицо')
    for i, s in enumerate(services, 1):
        name = s['name']
        qty = int(s.get('qty', 1))
        price_per = PRICE_PHYSICAL.get(name, 0.0) if form_type == 'Физическое лицо' else PRICE_LEGAL.get(name, 0.0)
        line_sum = int(price_per) * qty
        total += line_sum
        message += f"{i}. {name} × {qty} = {line_sum} руб.\n"
    message += f"\n*Итого:* {total} руб.\n\n"

    kb = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_order")],
        [InlineKeyboardButton("❌ Отменить", callback_data="cancel_order")]
    ]
    await _safe_edit(update.callback_query, message, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def _render_tasks(update_or_query, context: ContextTypes.DEFAULT_TYPE, mode: str = "active"):
    if isinstance(update_or_query, Update):
        user_id = _ensure_session(update_or_query)
        chat = update_or_query.message
        query = None
    else:
        query = update_or_query
        user_id = query.from_user.id

    employee = user_data[user_id]['username']
    tasks = await asyncio.to_thread(sheets.load_tasks_for_employee, employee, mode=mode)

    if not tasks:
        text = "Нет задач." if mode == "active" else "Нет завершённых задач."
        if query:
            await _safe_edit(query, text)
        else:
            await chat.reply_text(text)
        return

    idx_map = {}
    lines = [f"📋 *{'Актуальные' if mode=='active' else 'Завершённые'} задачи:*", ""]
    for idx, t in enumerate(tasks, 1):
        title = t.get("title") or t.get("Название") or t.get("Task") or "Без названия"
        deadline = t.get("deadline") or t.get("Дедлайн") or ""
        project = t.get("project") or t.get("Проект") or ""
        status = t.get("status") or t.get("Статус") or ""
        lines.append(f"{idx}. *{title}* ·  {deadline}  ·  {project}  ·  {status}")
        idx_map[idx] = {"title": title, "deadline": deadline, "project": project}
    user_data[user_id]['tasks_idx_map'] = idx_map
    text = "\n".join(lines)

    kb = [
        [InlineKeyboardButton("Актуальные", callback_data="tasks_filter:active"),
         InlineKeyboardButton("Завершённые", callback_data="tasks_filter:done")]
    ]
    if mode == "active":
        kb.append([InlineKeyboardButton("Отметить 1-ю выполненной", callback_data="task_done:1")])

    if isinstance(update_or_query, Update):
        await chat.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await _safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = _ensure_session(update)
    data = query.data

    if data.startswith("form_"):
        form_type = 'Физическое лицо' if data == 'form_physical' else 'Юридическое лицо'
        user_data[user_id]['order']['form_type'] = form_type
        user_data[user_id]['state'] = UserState.ORDER_PROJECT_NAME
        await _safe_edit(query, f"Выбрана форма: *{form_type}*\n\nВведите название проекта:", parse_mode="Markdown")
        return

    if data.startswith("task_emp:"):
        try:
            target_id = int(data.split(":")[1])
        except Exception:
            await query.answer("Некорректный выбор")
            return
        employee_name = EMPLOYEE_NAMES.get(target_id, f"ID {target_id}")
        user_data[user_id]['tmp_task'] = {
            "employee_id": target_id,
            "employee_name": employee_name,
            "stage": "title",
        }
        await _safe_edit(query, f"📝 Введите *название* задачи для {employee_name}:", parse_mode="Markdown")
        return

    if data.startswith("page:"):
        try:
            p = int(data.split(":")[1])
        except Exception:
            p = 0
        await show_services_page(update, context, p)
        return

    if data.startswith("select:"):
        sid = data.split(":")[1]
        svc = services_data.get(sid)
        if not svc:
            await query.answer("Услуга не найдена")
            return
        form = user_data[user_id]['order'].get('form_type', 'Физическое лицо')
        name = svc['name']
        price = PRICE_PHYSICAL.get(name, 0.0) if form == 'Физическое лицо' else PRICE_LEGAL.get(name, 0.0)
        add_or_set_service(user_id, name, price, 1)
        _schedule_cart_sync(context, user_id)
        await show_manage_qty_ui(update, context, sid)
        return

    if data.startswith("manage:"):
        sid = data.split(":")[1]
        await show_manage_qty_ui(update, context, sid)
        return

    if data.startswith("qty_inc:") or data.startswith("qty_dec:") or data.startswith("qty_set:"):
        sid = data.split(":")[1]
        svc = services_data.get(sid)
        if not svc:
            await query.answer("Ошибка")
            return
        name = svc['name']
        if data.startswith("qty_inc:"):
            change_qty(user_id, name, 1)
        elif data.startswith("qty_dec:"):
            change_qty(user_id, name, -1)
        else:
            remove_service(user_id, name)
        _schedule_cart_sync(context, user_id)
        await show_manage_qty_ui(update, context, sid)
        return

    if data == "show_cart":
        await show_cart(update, context)
        return

    if data == "reset_services":
        user_data[user_id]['order']['services'] = []
        _schedule_cart_sync(context, user_id)
        await show_services_page(update, context, 0)
        return

    if data == "finish_services":
        await show_order_confirmation(update, context)
        return

    if data == "confirm_order":
        order = user_data[user_id]['order']
        username = user_data[user_id].get('username', '')
        await asyncio.to_thread(sheets.copy_template_and_fill_data, username, order)
        user_data[user_id]['order']['services'] = []
        await _safe_edit(query, "✅ Заказ сохранён! Возврат в меню.")
        user_data[user_id]['state'] = UserState.MAIN_MENU
        try:
            await context.bot.send_message(chat_id=user_id, text="Возврат в меню.", reply_markup=_build_main_keyboard(user_id))
        except Exception:
            pass
        return

    if data == "cancel_order":
        user_data[user_id]['order']['services'] = []
        await _safe_edit(query, "❌ Заказ отменён.")
        return

    if data.startswith("tasks_filter:"):
        _, mode = data.split(":", 1)
        if mode not in ("active", "done"):
            mode = "active"
        await _render_tasks(query, context, mode=mode)
        return

    if data.startswith("task_done:"):
        try:
            idx = int(data.split(":")[1])
        except Exception:
            await query.answer("Ошибка индекса")
            return
        mapping = user_data.get(user_id, {}).get('tasks_idx_map', {})
        item = mapping.get(idx)
        if not item:
            await query.answer("Задача не найдена")
            return
        employee_name = user_data[user_id]['username']
        ok = await asyncio.to_thread(
            sheets.set_task_status,
            employee_name,
            item['title'],
            item['deadline'],
            "Готово",
            item.get('project', "")
        )
        if ok:
            await _safe_edit(query, "✅ Задача отмечена как выполненная.")
            await _render_tasks(query, context, mode="active")
        else:
            await _safe_edit(query, "❌ Не удалось обновить статус в таблице.")
        return

    await query.answer("Неизвестная команда")

async def send_logs_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    os.makedirs(LOGS_DIR, exist_ok=True)
    path = os.path.join(LOGS_DIR, "bot.log")
    if not os.path.exists(path):
        await update.message.reply_text("Лог-файл ещё не создан.")
        return
    _flush_log_handlers()
    blob = _tail_file(path, LOG_TAIL_LINES)
    if not blob:
        await update.message.reply_text("Лог пуст.")
        return
    fname = f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    bio = io.BytesIO(blob); bio.name = fname
    await context.bot.send_document(chat_id=update.effective_user.id, document=InputFile(bio, filename=fname), caption="Лог сейчас")

async def clear_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    os.makedirs(LOGS_DIR, exist_ok=True)
    path = os.path.join(LOGS_DIR, "bot.log")
    if not os.path.exists(path):
        await update.message.reply_text("Лог-файл ещё не создан.")
        return
    try:
        with open(path, "w", encoding="utf-8"):
            pass
        await update.message.reply_text("🧹 Логи очищены.")
    except Exception as e:
        await update.message.reply_text(f"Не удалось очистить: {e}")

async def logs_push_job(context):
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        log_path = os.path.join(LOGS_DIR, "bot.log")
        if not os.path.exists(log_path):
            return
        _flush_log_handlers()
        blob = _tail_file(log_path, LOG_TAIL_LINES)
        if not blob:
            return
        fname = f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        for admin_id in ADMINS:
            try:
                bio = io.BytesIO(blob); bio.name = fname
                await context.bot.send_document(chat_id=admin_id, document=InputFile(bio, filename=fname), caption="Авто-лог (каждые 6 часов)")
            except Exception:
                pass
        try:
            with open(log_path, "w", encoding="utf-8"):
                pass
        except Exception:
            pass
    except Exception:
        logging.exception("logs_push_job failed")

async def reminders_hourly(context):
    return

async def reminders_daily(context):
    return

async def reminders_weekly(context):
    return