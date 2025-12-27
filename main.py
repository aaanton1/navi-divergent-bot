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

RAILWAY_PROJECT_ID = os.environ.get("RAILWAY_PROJECT_ID", "").strip()
RAILWAY_ENVIRONMENT_ID = os.environ.get("RAILWAY_ENVIRONMENT_ID", "").strip()
RAILWAY_SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "").strip()

WATCH_CHATS = os.environ.get("WATCH_CHATS", "").strip()  # comma-separated chat ids

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


# ---------- helpers ----------
def _railway_ok() -> bool:
    return bool(
        RAILWAY_TOKEN
        and RAILWAY_PROJECT_ID
        and RAILWAY_ENVIRONMENT_ID
        and RAILWAY_SERVICE_ID
    )


def _parse_watch_chats(raw: str) -> set[int]:
    raw = (raw or "").strip()
    if not raw:
        return set()
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def _format_watch_chats(ids: set[int]) -> str:
    return ",".join(str(i) for i in sorted(ids))


def railway_set_variable(key: str, value: str) -> None:
    if not _railway_ok():
        raise RuntimeError("Railway доступ не настроен (нет токена или IDs)")

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
            result = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode("utf-8", errors="replace")) from e

    if "errors" in result:
        raise RuntimeError(str(result["errors"]))

    logging.info(f"Railway variable set: {key}={value}")


# ---------- commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(
        "Navi готов.\n\n"
        "Команды:\n"
        "/set_hq — сделать этот чат HQ\n"
        "/on — включить текущий чат в прослушку\n"
        "/off — выключить текущий чат из прослушки\n"
        "/list — показать список прослушиваемых чатов\n"
        "/status — статус\n"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    hq = os.environ.get("ASSISTANT_CHAT_ID", "").strip()
    watch = _parse_watch_chats(os.environ.get("WATCH_CHATS", ""))
    await update.message.reply_text(
        "Статус:\n"
        f"- HQ chat_id: {hq if hq else 'не задан'}\n"
        f"- Railway доступ: {'OK' if _railway_ok() else 'нет'}\n"
        f"- WATCH_CHATS: {len(watch)} чат(ов)"
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


async def on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return

    # Включать будем именно чат, где написали /on
    cid = int(chat.id)
    watch = _parse_watch_chats(os.environ.get("WATCH_CHATS", ""))
    watch.add(cid)

    try:
        railway_set_variable("WATCH_CHATS", _format_watch_chats(watch))
    except Exception as e:
        await update.message.reply_text(f"Не смог включить чат:\n{e}")
        return

    await update.message.reply_text(f"Ок ✅ Чат включён в прослушку:\n{chat.title or chat.username or cid}")


async def off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return

    cid = int(chat.id)
    watch = _parse_watch_chats(os.environ.get("WATCH_CHATS", ""))
    if cid in watch:
        watch.remove(cid)

    try:
        railway_set_variable("WATCH_CHATS", _format_watch_chats(watch))
    except Exception as e:
        await update.message.reply_text(f"Не смог выключить чат:\n{e}")
        return

    await update.message.reply_text(f"Ок ✅ Чат выключен из прослушки:\n{chat.title or chat.username or cid}")


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    watch = sorted(_parse_watch_chats(os.environ.get("WATCH_CHATS", "")))
    if not watch:
        await update.message.reply_text("Список пуст. Добавь чат командой /on прямо в нужном чате.")
        return

    lines = ["Прослушиваемые chat_id:"]
    lines += [f"- {cid}" for cid in watch]
    await update.message.reply_text("\n".join(lines))


# ---------- forwarding ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat:
        return

    # бот нигде не отвечает
    # пересылаем только из групп/супергрупп
    if chat.type not in ("group", "supergroup"):
        return

    # только если чат включён
    watch = _parse_watch_chats(os.environ.get("WATCH_CHATS", ""))
    if int(chat.id) not in watch:
        return

    if not ASSISTANT_CHAT_ID:
        return

    chat_title = chat.title or chat.username or "unknown_chat"
    user_name = user.full_name if user and user.full_name else (user.username if user and user.username else "unknown_user")

    text = (msg.text or msg.caption or "").strip()
    is_voice = msg.voice is not None

    preview = text
    if is_voice and not preview:
        preview = "(voice message)"
    if not preview:
        preview = "(empty message)"

    try:
        await context.bot.send_message(
            chat_id=int(ASSISTANT_CHAT_ID),
            text=(
                "🧭 Navi (входящее)\n"
                f"Чат: {chat_title}\n"
                f"От: {user_name}\n\n"
                f"{preview[:1500]}"
            ),
        )
    except Exception as e:
        logging.warning(f"Failed to forward to HQ: {e}")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("set_hq", set_hq))
    app.add_handler(CommandHandler("on", on_cmd))
    app.add_handler(CommandHandler("off", off_cmd))
    app.add_handler(CommandHandler("list", list_cmd))

    app.add_handler(MessageHandler(filters.ALL, handle_message))

    logging.info("Navi bot started")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
