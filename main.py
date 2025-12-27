import os
import logging
import json
import urllib.request
import urllib.error

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ASSISTANT_CHAT_ID = os.environ.get("ASSISTANT_CHAT_ID", "").strip()

RAILWAY_TOKEN = os.environ.get("RAILWAY_TOKEN", "").strip()
RAILWAY_SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")


def railway_set_service_variable(key: str, value: str) -> None:
    """
    Sets/updates a service variable in Railway via GraphQL API.
    Requires RAILWAY_TOKEN and RAILWAY_SERVICE_ID.
    """
    if not RAILWAY_TOKEN or not RAILWAY_SERVICE_ID:
        raise RuntimeError("RAILWAY_TOKEN or RAILWAY_SERVICE_ID is missing")

    # Railway GraphQL endpoint
    url = "https://backboard.railway.app/graphql/v2"

    # This mutation name/shape is stable in Railway GraphQL for setting variables.
    # If Railway changes it in the future, logs will show the GraphQL error text.
    query = """
    mutation UpsertServiceVariables($serviceId: String!, $variables: [ServiceVariableInput!]!) {
      serviceVariablesUpsert(serviceId: $serviceId, variables: $variables)
    }
    """

    payload = {
        "query": query,
        "variables": {
            "serviceId": RAILWAY_SERVICE_ID,
            "variables": [{"key": key, "value": value}],
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {RAILWAY_TOKEN}")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            result = json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Railway HTTPError: {e.code} {e.reason} | {body}") from e
    except Exception as e:
        raise RuntimeError(f"Railway request failed: {e}") from e

    if "errors" in result and result["errors"]:
        raise RuntimeError(f"Railway GraphQL errors: {result['errors']}")

    logging.info(f"Railway variable upsert OK: {key}={value}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # отвечаем только в личке
    if update.effective_chat and update.effective_chat.type != "private":
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    await update.message.reply_text(
        "Привет! Я Navi.\n\n"
        "Команды:\n"
        "/set_hq — сделать этот чат «Ассистент HQ» (запомню chat_id)\n"
        "/status — показать текущие настройки\n\n"
        f"Текущий chat_id: {chat_id}"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # отвечаем только в личке
    if update.effective_chat and update.effective_chat.type != "private":
        return

    hq = os.environ.get("ASSISTANT_CHAT_ID", "").strip()
    railway_ok = bool(RAILWAY_TOKEN and RAILWAY_SERVICE_ID)
    await update.message.reply_text(
        "Статус:\n"
        f"- HQ chat_id: {hq if hq else 'не задан'}\n"
        f"- Railway доступ: {'OK' if railway_ok else 'нет (нужны RAILWAY_TOKEN и RAILWAY_SERVICE_ID)'}"
    )


async def set_hq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Run this command inside the chat you want to be HQ.
    Usually you call it from your personal HQ chat where the bot is a member.
    """
    chat = update.effective_chat
    if not chat:
        return

    # ВАЖНО: чтобы не засорять группы, отвечаем только в личке,
    # но сам set_hq можно вызывать и в нужном чате (например, HQ группе/канале),
    # тогда бот просто тихо обновит переменную и пришлет подтверждение в личку (если есть).
    target_chat_id = str(chat.id)

    try:
        railway_set_service_variable("ASSISTANT_CHAT_ID", target_chat_id)
    except Exception as e:
        logging.exception("Failed to set HQ")
        # отвечаем в тот чат, где команда вызвана (это редкая команда, можно)
        await update.message.reply_text(f"Не смог сохранить HQ: {e}")
        return

    # подтверждение
    await update.message.reply_text(
        "Готово ✅\n"
        f"Этот чат сохранён как HQ: {target_chat_id}\n\n"
        "Важно: Railway применит переменную и сервис перезапустится автоматически."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return

    # Бот НЕ отвечает нигде, кроме лички с ботом
    if chat.type != "private":
        return

    text = (msg.text or msg.caption or "").strip()
    if not text:
        return

    # Минимальный ответ в личке
    await msg.reply_text("Принял ✅")

    # Если HQ задан — отправим туда заметку
    hq = os.environ.get("ASSISTANT_CHAT_ID", "").strip()
    if hq:
        try:
            await context.bot.send_message(
                chat_id=int(hq),
                text=f"Заметка от Антона:\n{text[:2000]}",
            )
        except Exception as e:
            logging.warning(f"Failed to forward to HQ: {e}")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("set_hq", set_hq))
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    logging.info("Navi bot starting (polling)...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
