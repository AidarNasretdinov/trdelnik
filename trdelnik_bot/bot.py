import nest_asyncio
nest_asyncio.apply()

import json
import os
import base64
import asyncio
from dotenv import load_dotenv
from telegram import (
    Update,
    WebAppInfo,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

load_dotenv()
BOT_TOKEN      = os.getenv("BOT_TOKEN")
OWNER_CHAT_ID  = int(os.getenv("OWNER_CHAT_ID"))
WEBAPP_URL     = os.getenv("WEBAPP_URL")
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER   = os.getenv("GITHUB_OWNER", "aidarnasretdinov")
GITHUB_REPO    = os.getenv("GITHUB_REPO", "trdelnik")

# Флаг приёма заказов (синхронизируется с status.json на GitHub Pages)
orders_open: bool = True

orders: dict[str, dict] = {}
_order_counter = 0


def next_order_id() -> str:
    global _order_counter
    _order_counter += 1
    return str(_order_counter)


# ── GitHub Pages status.json ───────────────────────────────────────────────────

async def update_github_status(open_status: bool) -> bool:
    """Обновляет status.json в репозитории через GitHub API."""
    if not GITHUB_TOKEN:
        return False
    try:
        import httpx
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/status.json"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            # Получаем текущий SHA файла
            get = await client.get(url, headers=headers)
            sha = get.json().get("sha", "") if get.status_code == 200 else ""

            content = json.dumps({"open": open_status}, indent=2) + "\n"
            content_b64 = base64.b64encode(content.encode()).decode()
            payload = {
                "message": "open orders" if open_status else "close orders",
                "content": content_b64,
            }
            if sha:
                payload["sha"] = sha

            put = await client.put(url, headers=headers, json=payload)
            return put.status_code in (200, 201)
    except Exception as e:
        print(f"GitHub API error: {e}")
        return False


# ── Форматирование заказа ──────────────────────────────────────────────────────

def format_item(item: dict) -> str:
    name  = item.get("name", "?")
    qty   = item.get("qty", 1)
    price = item.get("price", 0)
    if item.get("custom"):
        base     = item.get("base", "—")
        fillings = ", ".join(item.get("fillings", [])) or "—"
        toppings = ", ".join(item.get("toppings", [])) or "—"
        return (
            f"🔧 {name} ×{qty} — {price * qty}₽\n"
            f"   Основа: {base}\n"
            f"   Начинки: {fillings}\n"
            f"   Топинги: {toppings}"
        )
    return f"• {name} ×{qty} — {price * qty}₽"


def format_order(data: dict, order_id: str) -> str:
    name     = data.get("name", "не указано")
    phone    = data.get("phone", "не указано")
    location = data.get("location", "не указано")
    items    = data.get("items", [])
    total    = data.get("total", 0)
    lines = [f"📋 Заказ #{order_id}", f"👤 {name}  📞 {phone}", f"📍 {location}", ""]
    for item in items:
        lines.append(format_item(item))
    lines.append(f"\n💰 Итого: {total}₽")
    return "\n".join(lines)


# ── /start ─────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("🥐 Открыть меню", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "Привет! Нажми кнопку ниже, чтобы открыть меню Трдельников 👇",
        reply_markup=kb,
    )


# ── /open и /close (только владелец) ──────────────────────────────────────────

async def cmd_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global orders_open
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    orders_open = True
    success = await update_github_status(True)
    status_note = " (сайт обновлён ✅)" if success else " (GitHub не обновлён — проверь GITHUB_TOKEN)"
    await update.message.reply_text(f"✅ Приём заказов открыт!{status_note}")


async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global orders_open
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    orders_open = False
    success = await update_github_status(False)
    status_note = " (сайт обновлён ✅)" if success else " (GitHub не обновлён — проверь GITHUB_TOKEN)"
    await update.message.reply_text(f"🔴 Приём заказов приостановлен!{status_note}")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    state = "✅ Открыт" if orders_open else "🔴 Закрыт"
    await update.message.reply_text(
        f"Статус приёма заказов: {state}\n\n"
        "Команды:\n/open — открыть приём\n/close — закрыть приём"
    )


# ── Web App data ───────────────────────────────────────────────────────────────

async def on_web_app_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.web_app_data.data
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        await update.message.reply_text("❌ Ошибка: не удалось прочитать данные заказа.")
        return

    customer_chat_id = update.effective_chat.id

    # Проверяем статус приёма заказов
    if not orders_open:
        await ctx.bot.send_message(
            chat_id=customer_chat_id,
            text="😔 К сожалению, приём заказов сейчас приостановлен.\nПопробуйте позже — скоро откроемся!",
        )
        return

    oid = next_order_id()

    # Подтверждение клиенту
    customer_text = (
        f"✅ Заказ #{oid} принят!\n\n"
        + format_order(data, oid)
        + "\n\nОжидайте подтверждения от нашего менеджера 🙏"
    )
    customer_msg = await ctx.bot.send_message(chat_id=customer_chat_id, text=customer_text)

    # Уведомление владельцу
    owner_text = "🆕 Новый заказ!\n\n" + format_order(data, oid)
    owner_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Принять",   callback_data=f"accept:{oid}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{oid}"),
    ]])
    owner_msg = await ctx.bot.send_message(
        chat_id=OWNER_CHAT_ID, text=owner_text, reply_markup=owner_kb
    )

    orders[oid] = {
        "customer_chat_id": customer_chat_id,
        "customer_msg_id":  customer_msg.message_id,
        "owner_msg_id":     owner_msg.message_id,
        "status":           "new",
        "data":             data,
    }


# ── Callback кнопки ────────────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, oid = query.data.split(":", 1)
    order = orders.get(oid)

    if not order:
        await query.answer("Заказ не найден.", show_alert=True)
        return

    customer_id = order["customer_chat_id"]
    name = order["data"].get("name", "Клиент")

    if action == "accept":
        if order["status"] != "new":
            await query.answer("Статус уже изменён.", show_alert=True)
            return
        order["status"] = "accepted"

        await query.edit_message_text(
            text="✅ Принят\n\n" + format_order(order["data"], oid),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🍽 Готово — уведомить клиента", callback_data=f"ready:{oid}")
            ]]),
        )
        # PUSH клиенту — новое сообщение = пуш-уведомление
        await ctx.bot.send_message(
            chat_id=customer_id,
            text=f"👨‍🍳 {name}, ваш заказ #{oid} принят в работу!\nСкоро всё будет готово 🔥",
        )

    elif action == "ready":
        if order["status"] != "accepted":
            await query.answer("Статус уже изменён.", show_alert=True)
            return
        order["status"] = "ready"

        await query.edit_message_text(text="🏁 Готово\n\n" + format_order(order["data"], oid))
        # PUSH клиенту
        await ctx.bot.send_message(
            chat_id=customer_id,
            text=f"🎉 {name}, ваш заказ #{oid} готов!\nМожно забирать 🥐",
        )

    elif action == "reject":
        if order["status"] not in ("new", "accepted"):
            await query.answer("Статус уже изменён.", show_alert=True)
            return
        order["status"] = "rejected"

        await query.edit_message_text(text="❌ Отклонён\n\n" + format_order(order["data"], oid))
        # PUSH клиенту
        await ctx.bot.send_message(
            chat_id=customer_id,
            text=(
                f"😔 {name}, к сожалению ваш заказ #{oid} был отклонён.\n"
                "Пожалуйста, свяжитесь с нами для уточнения деталей."
            ),
        )


# ── Инициализация при запуске ──────────────────────────────────────────────────

async def post_init(app):
    """Устанавливает постоянную кнопку меню — открывает мини-апп в любой момент."""
    await app.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="🥐 Меню",
            web_app=WebAppInfo(url=WEBAPP_URL),
        )
    )
    print("✅ Menu button установлена.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("open",   cmd_open))
    app.add_handler(CommandHandler("close",  cmd_close))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_web_app_data))
    app.add_handler(CallbackQueryHandler(on_callback))

    print("✅ Бот запущен. Ctrl+C для остановки.")
    print(f"   Приём заказов: {'открыт' if orders_open else 'закрыт'}")
    app.run_polling()


if __name__ == "__main__":
    main()
