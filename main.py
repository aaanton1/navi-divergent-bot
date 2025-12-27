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

# ---------- LOGGING ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# ---------- ENV ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ASSISTANT_CHAT_ID = os.environ.get("ASSISTANT_CHAT_ID", "").strip()

RAILWAY_TOKEN = os.environ.get("RAILWAY_TOKEN", "").strip()

RAILWAY_PROJECT_ID = os.environ.get("RAILWAY_PROJECT_ID", "").strip()
RAILWAY_ENVIRONMENT_ID = os.environ.get("RAILWAY_ENVIRONMENT_ID", "").strip()
RAILWAY_SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


# ---------- RAILWAY API ----------
def railway_set_variable(key: str, value: str) -> None:
    if not RAILWAY_TOKEN:
        raise RuntimeError("RAILWAY_TOKEN is missing")

    if not (RAILWAY_PROJECT_ID and RAILWAY_ENVIRONMENT_ID and RAILWAY_SERVICE_ID):
        raise RuntimeError("Railway IDs are missing")

    url = "https://backboard.railway.app/graphql/v2"

    query = """
    mutation variableUpsert($input: VariableUpsertInput!) {
      variableUpsert(input: $input)
    }
    """

    payload = {
        "query": query,
        "variables": {
            "input": {
                "projectId": RAILWAY_PROJECT_ID,
                "environmentId": RAILWAY_ENVIRONMENT_ID,
                "serviceId": RAILWAY_SERVICE_ID,
                "name": key,
                "value": value,
            }
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {RAILWAY_TOKEN}")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode())

    if "errors" in result:
        raise RuntimeError(result["errors"])

    logging.info(f"Railway variable set: {key}={value}")


# ---------- HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    chat_id = update.effective_chat.id

    await update.message.reply_text(
        "Привет, Антон. Я Navi.\n\n"
        "Команды:\n"
        "/set_hq — сделать этот чат HQ\n"
        "/status — показать текущий статус\n\n"
        f"Текущий chat_id: {chat_id}"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    hq = os.environ.get("ASSISTANT_CHAT_ID", "").strip()
    railway_ok = bool(
        RAILWAY_TOKEN
        and RAILWAY_PROJECT_ID
        and RAILWAY_ENVIRONMENT_ID
        and RAILWAY_SERVICE_ID
    )

    await update.message.reply_text(
        "Статус:\n"
        f"- HQ chat_id: {hq if hq else 'не задан'}\n"
        f"- Railway доступ: {'OK' if railway_ok else 'нет'}"
    )


async def set_hq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return

    try:
        railway_set_variable("ASSISTANT_CHAT_ID", str(chat.id))
    except Exception as e:
        await update.message.reply_text(f"Не смог сохранить HQ:\n{e}")
        return

    await update.message.reply_text(
        "Готово ✅\n"
        f"Этот чат сохранён как HQ:\n{chat.id}\n\n"
        "Railway перезапустит сервис автоматически."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # бот НИГДЕ не отвечает, кроме команд
    return


# ---------- MAIN ----------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("set_hq", set_hq))
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    logging.info("Navi bot started")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
