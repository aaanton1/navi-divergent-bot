import os
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ASSISTANT_CHAT_ID = os.environ.get("ASSISTANT_CHAT_ID", "").strip()  # optional (we’ll set later)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id if update.effective_chat else None
    logging.info(f"[START] from chat_id={chat_id}")
    await update.message.reply_text(
        "Привет, Антон. Я Нави.\n\n"
        "Я уже слушаю подключенные группы.\n"
        "Чтобы я писал выжимки сюда, нужно будет один раз сохранить ASSISTANT_CHAT_ID.\n"
        f"Твой chat_id: {chat_id}"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    await msg.reply_text("Принял ✅")

    chat = update.effective_chat
    user = update.effective_user

    chat_title = chat.title if chat and chat.title else (chat.username if chat else "unknown_chat")
    chat_id = chat.id if chat else None

    user_name = "unknown_user"
    if user:
        user_name = user.full_name or user.username or str(user.id)

    text = msg.text or msg.caption or ""
    is_voice = msg.voice is not None

    logging.info(
        f"[MSG] chat='{chat_title}' (id={chat_id}) | from='{user_name}' | "
        f"voice={is_voice} | text='{text[:200]}'"
    )

    # Optional: forward a short note to your “assistant HQ” chat (we’ll set ASSISTANT_CHAT_ID later)
    if ASSISTANT_CHAT_ID:
        try:
            preview = text.strip()
            if is_voice and not preview:
                preview = "(voice message)"
            if not preview:
                preview = "(empty message)"

            await context.bot.send_message(
                chat_id=int(ASSISTANT_CHAT_ID),
                text=(
                    "Ассистент\n"
                    f"Я понял так: пришло сообщение из '{chat_title}'.\n"
                    f"От: {user_name}\n"
                    f"Текст: {preview[:500]}"
                )
            )
        except Exception as e:
            logging.warning(f"Failed to forward to ASSISTANT_CHAT_ID: {e}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    logging.info("Navi bot starting (polling)...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

