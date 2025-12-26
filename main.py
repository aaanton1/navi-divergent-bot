import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------- ENV ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ASSISTANT_CHAT_ID = os.environ.get("ASSISTANT_CHAT_ID", "").strip()  # optional

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")

# ---------- LOGGING ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# ---------- HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    chat_id = chat.id if chat else None

    logging.info(f"[START] chat_id={chat_id}")

    await update.message.reply_text(
        "Привет, Антон. Я Нави 👋\n\n"
        "Я уже слушаю подключённые группы.\n"
        "Чтобы я присылал выжимки в ассистент-чат,\n"
        "нужно один раз сохранить ASSISTANT_CHAT_ID.\n\n"
        f"Твой chat_id: {chat_id}"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    chat = update.effective_chat
    user = update.effective_user

    # --- 1. Отвечаем ТОЛЬКО в личке ---
    if chat and chat.type == "private":
        await msg.reply_text("Принял ✅")

    # --- 2. Готовим данные ---
    chat_title = (
        chat.title if chat and chat.title
        else chat.username if chat
        else "unknown_chat"
    )

    user_name = (
        user.full_name if user and user.full_name
        else user.username if user and user.username
        else str(user.id) if user
        else "unknown_user"
    )

    text = msg.text or msg.caption or ""
    is_voice = msg.voice is not None

    preview = text.strip()
    if is_voice and not preview:
        preview = "(voice message)"
    if not preview:
        preview = "(empty message)"

    # --- 3. Пересылаем в ассистент-чат ---
    if ASSISTANT_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ASSISTANT_CHAT_ID),
                text=(
                    "🧭 Ассистент\n"
                    f"Из чата: {chat_title}\n"
                    f"От: {user_name}\n\n"
                    f"Текст:\n{preview[:500]}"
                )
            )
        except Exception as e:
            logging.warning(f"Failed to forward to ASSISTANT_CHAT_ID: {e}")


# ---------- MAIN ----------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    logging.info("Navi bot starting (polling)...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
