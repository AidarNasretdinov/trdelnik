import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonWebApp,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from db import create_order, get_order, init_db, list_orders, update_order
from verify import verify_init_data

load_dotenv()

BOT_TOKEN     = os.getenv("BOT_TOKEN")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID"))
WEBAPP_URL    = os.getenv("WEBAPP_URL")
GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER  = os.getenv("GITHUB_OWNER", "aidarnasretdinov")
GITHUB_REPO   = os.getenv("GITHUB_REPO", "trdelnik")
PORT          = int(os.getenv("PORT", 8000))

# Флаг приёма заказов
orders_open: bool = True

# Начинки которые добавляем при клиенте (названия, как хранит мини-апп)
COLD_FILLINGS = {"Мороженое", "Взбитые сливки"}
# Пресеты с мороженым или сливками
COLD_PRESET_IDS = {"s1", "s2", "s3", "s4"}

# ── Telegram Application ───────────────────────────────────────────────────────

tg_app = ApplicationBuilder().token(BOT_TOKEN).build()


# ── Helpers ────────────────────────────────────────────────────────────────────

def has_cold_filling(order_data: dict) -> bool:
    """Есть ли в заказе мороженое или взбитые сливки."""
    for item in order_data.get("items", []):
        if item.get("custom"):
            if any(f in COLD_FILLINGS for f in item.get("fillings", [])):
                return True
        elif item.get("id") in COLD_PRESET_IDS:
            return True
    return False


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


def format_order(data: dict, order_id: int) -> str:
    name     = data.get("name", "не указано")
    phone    = data.get("phone", "не указано")
    location = data.get("location", "не указано")
    items    = data.get("items", [])
    total    = data.get("total", 0)
    lines = [
        f"📋 Заказ #{order_id}",
        f"👤 {name}  📞 {phone}",
        f"📍 {location}",
        "",
    ]
    for item in items:
        lines.append(format_item(item))
    lines.append(f"\n💰 Итого: {total}₽")
    if has_cold_filling(data):
        lines.append("\n🧊 Мороженое/сливки — добавляем при клиенте!")
    return "\n".join(lines)


# ── GitHub Pages status.json ───────────────────────────────────────────────────

async def update_github_status(open_status: bool) -> bool:
    if not GITHUB_TOKEN:
        return False
    try:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/status.json"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            get = await client.get(url, headers=headers)
            sha = get.json().get("sha", "") if get.status_code == 200 else ""
            content = json.dumps({"open": open_status}, indent=2) + "\n"
            payload = {
                "message": "open orders" if open_status else "close orders",
                "content": base64.b64encode(content.encode()).decode(),
            }
            if sha:
                payload["sha"] = sha
            put = await client.put(url, headers=headers, json=payload)
            return put.status_code in (200, 201)
    except Exception as e:
        print(f"GitHub API error: {e}")
        return False


# ── Bot commands ───────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("🥐 Открыть меню", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "Привет! Нажми кнопку ниже, чтобы открыть меню Трдельников 👇",
        reply_markup=kb,
    )


async def cmd_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global orders_open
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    orders_open = True
    ok = await update_github_status(True)
    note = " (сайт обновлён ✅)" if ok else " (GitHub не обновлён — проверь GITHUB_TOKEN)"
    await update.message.reply_text(f"✅ Приём заказов открыт!{note}")


async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global orders_open
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    orders_open = False
    ok = await update_github_status(False)
    note = " (сайт обновлён ✅)" if ok else " (GitHub не обновлён — проверь GITHUB_TOKEN)"
    await update.message.reply_text(f"🔴 Приём заказов приостановлен!{note}")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    state = "✅ Открыт" if orders_open else "🔴 Закрыт"
    pending = list_orders(status="new")
    await update.message.reply_text(
        f"Статус приёма заказов: {state}\n"
        f"Новых заказов в очереди: {len(pending)}\n\n"
        "Команды:\n/open — открыть\n/close — закрыть"
    )


# ── Callback кнопки ────────────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, oid_str = query.data.split(":", 1)
    oid   = int(oid_str)
    order = get_order(oid)

    if not order:
        await query.answer("Заказ не найден.", show_alert=True)
        return

    customer_id = order["telegram_user_id"]
    name        = order["name"]
    data = {
        "name":     order["name"],
        "phone":    order["phone"],
        "location": order["location"],
        "items":    order["items"],
        "total":    order["total"],
    }
    cold = has_cold_filling(data)

    # ── Принять ──────────────────────────────────────────────────────────────
    if action == "accept":
        if order["status"] != "new":
            await query.answer("Статус уже изменён.", show_alert=True)
            return
        update_order(oid, status="accepted")

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⏱ Готово через 10 мин", callback_data=f"tenmin:{oid}"),
            InlineKeyboardButton("❌ Отклонить",           callback_data=f"reject:{oid}"),
        ]])
        await query.edit_message_text(
            text="✅ Принят\n\n" + format_order(data, oid),
            reply_markup=kb,
        )

        msg = f"👨‍🍳 {name}, ваш заказ #{oid} принят в работу!\nСкоро всё будет готово 🔥"
        if cold:
            msg += "\n\n🍦 Мороженое/сливки добавим прямо при вас — не переживайте!"
        await ctx.bot.send_message(chat_id=customer_id, text=msg)

    # ── Готово через 10 минут ─────────────────────────────────────────────────
    elif action == "tenmin":
        if order["status"] != "accepted":
            await query.answer("Статус уже изменён.", show_alert=True)
            return
        update_order(oid, status="tenmin")

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🏁 Выдать заказ", callback_data=f"ready:{oid}"),
        ]])
        await query.edit_message_text(
            text="⏱ Готовится\n\n" + format_order(data, oid),
            reply_markup=kb,
        )
        await ctx.bot.send_message(
            chat_id=customer_id,
            text=f"⏱ {name}, ваш заказ #{oid} будет готов примерно через 10 минут!\nПодходите 🚶",
        )

    # ── Выдать заказ ─────────────────────────────────────────────────────────
    elif action == "ready":
        if order["status"] not in ("accepted", "tenmin"):
            await query.answer("Статус уже изменён.", show_alert=True)
            return
        update_order(oid, status="ready")

        await query.edit_message_text(text="🏁 Выдан\n\n" + format_order(data, oid))

        msg = f"🎉 {name}, ваш заказ #{oid} готов! Можно забирать 🥐"
        if cold:
            msg += "\n\n🍦 Подходите — добавим мороженое/сливки прямо при вас!"
        await ctx.bot.send_message(chat_id=customer_id, text=msg)

    # ── Отклонить ─────────────────────────────────────────────────────────────
    elif action == "reject":
        if order["status"] in ("ready", "rejected"):
            await query.answer("Статус уже изменён.", show_alert=True)
            return
        update_order(oid, status="rejected")

        await query.edit_message_text(text="❌ Отклонён\n\n" + format_order(data, oid))
        await ctx.bot.send_message(
            chat_id=customer_id,
            text=(
                f"😔 {name}, к сожалению ваш заказ #{oid} был отклонён.\n"
                "Пожалуйста, свяжитесь с нами для уточнения деталей."
            ),
        )


# ── Register handlers ──────────────────────────────────────────────────────────

tg_app.add_handler(CommandHandler("start",  cmd_start))
tg_app.add_handler(CommandHandler("open",   cmd_open))
tg_app.add_handler(CommandHandler("close",  cmd_close))
tg_app.add_handler(CommandHandler("status", cmd_status))
tg_app.add_handler(CallbackQueryHandler(on_callback))


# ── FastAPI ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()
    await tg_app.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="🥐 Меню", web_app=WebAppInfo(url=WEBAPP_URL))
    )
    print(f"✅ Бот и API запущены. Порт: {PORT}")
    yield
    await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()


api = FastAPI(lifespan=lifespan)

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)


@api.post("/order")
async def receive_order(request: Request):
    global orders_open

    body       = await request.json()
    init_data  = body.get("initData", "")
    order_data = body.get("order", {})

    # Верификация подписи Telegram
    if init_data:
        parsed = verify_init_data(init_data, BOT_TOKEN)
        if not parsed:
            print(f"[ORDER] initData verification FAILED")
            raise HTTPException(status_code=403, detail="Invalid Telegram initData")
        user = parsed.get("user", {})
        if isinstance(user, str):
            try:
                user = json.loads(user)
            except Exception:
                user = {}
        telegram_user_id = user.get("id", 0)
        print(f"[ORDER] telegram_user_id from initData: {telegram_user_id}")
    else:
        telegram_user_id = order_data.get("telegram_user_id", 0)
        print(f"[ORDER] no initData, telegram_user_id: {telegram_user_id}")

    if not telegram_user_id:
        return {"ok": False, "error": "no_user_id", "detail": "Could not identify Telegram user"}

    if not orders_open:
        return {"ok": False, "error": "orders_closed"}

    name     = order_data.get("name", "")
    phone    = order_data.get("phone", "")
    location = order_data.get("location", "")
    items    = order_data.get("items", [])
    total    = order_data.get("total", 0)

    oid = create_order(telegram_user_id, name, phone, location, items, total)
    print(f"[ORDER] created order #{oid} for user {telegram_user_id}")

    data_fmt = {"name": name, "phone": phone, "location": location, "items": items, "total": total}
    cold     = has_cold_filling(data_fmt)

    # Подтверждение клиенту
    customer_text = (
        f"✅ Заказ #{oid} принят!\n\n"
        + format_order(data_fmt, oid)
        + "\n\nОжидайте подтверждения от нашего менеджера 🙏"
    )
    if cold:
        customer_text += "\n\n🍦 Мороженое/взбитые сливки добавим прямо при вас!"

    try:
        customer_msg = await tg_app.bot.send_message(
            chat_id=telegram_user_id, text=customer_text
        )
        customer_msg_id = customer_msg.message_id
    except Exception as e:
        print(f"[ORDER] failed to send customer message: {e}")
        customer_msg_id = None

    # Уведомление владельцу
    owner_text = "🆕 Новый заказ!\n\n" + format_order(data_fmt, oid)
    owner_kb   = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Принять",   callback_data=f"accept:{oid}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{oid}"),
    ]])
    try:
        owner_msg = await tg_app.bot.send_message(
            chat_id=OWNER_CHAT_ID, text=owner_text, reply_markup=owner_kb
        )
        owner_msg_id = owner_msg.message_id
    except Exception as e:
        print(f"[ORDER] failed to send owner message: {e}")
        owner_msg_id = None

    update_order(
        oid,
        customer_msg_id=customer_msg_id,
        owner_msg_id=owner_msg_id,
    )

    return {"ok": True, "order_id": oid}


@api.get("/health")
async def health():
    return {"ok": True, "orders_open": orders_open}


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(api, host="0.0.0.0", port=PORT)
