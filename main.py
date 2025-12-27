import os
import re
import json
import time
import logging
import urllib.request
import urllib.error
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------- LOGGING ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ---------- ENV ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ASSISTANT_CHAT_ID = os.environ.get("ASSISTANT_CHAT_ID", "").strip()

RAILWAY_TOKEN = os.environ.get("RAILWAY_TOKEN", "").strip()
RAILWAY_PROJECT_ID = os.environ.get("RAILWAY_PROJECT_ID", "").strip()
RAILWAY_ENVIRONMENT_ID = os.environ.get("RAILWAY_ENVIRONMENT_ID", "").strip()
RAILWAY_SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "").strip()

MEMORY_JSON_RAW = os.environ.get("MEMORY_JSON", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# ---------- CONSTANTS ----------
MAX_LAST_MESSAGES_PER_CHAT = 50
MAX_TASK_CANDIDATES = 200
FORWARD_TEXT_LIMIT = 1500  # telegram message limit is bigger, but keep HQ readable

IMPORTANT_KEYWORDS = [
    "надо", "нужно", "сделай", "сделать", "задача", "задачи", "поставь",
    "дедлайн", "срок", "до ", "к ", "проверь", "проверить", "проверим",
    "созвонимся", "встреча", "оплатить", "оплата", "договор", "договорились",
    "отправь", "пришли", "жду", "ждём", "ответь", "ответить", "согласуй",
    "озон", "ozon", "заказ", "партия", "поставка", "карточка", "размещение",
    "срочно", "важно", "сегодня", "завтра", "послезавтра"
]

DATE_PATTERNS = [
    re.compile(r"\b\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?\b"),     # 27.12, 27/12/2025
    re.compile(r"\b\d{1,2}:\d{2}\b"),                          # 10:30
    re.compile(r"\b\d{1,2}\s?(утра|вечера|дня|ночью)\b", re.I), # 10 утра
    re.compile(r"\b(сегодня|завтра|послезавтра)\b", re.I),
]

QUESTION_PATTERN = re.compile(r"\?")
MONEY_PATTERN = re.compile(r"(\b\d+[ ]?(₽|руб|р)\b)|(\b₽\s?\d+\b)", re.I)

# ---------- MEMORY ----------
def _load_memory() -> dict:
    if not MEMORY_JSON_RAW:
        return {"version": 1, "updated_at": None, "chats": {}, "task_candidates": []}
    try:
        mem = json.loads(MEMORY_JSON_RAW)
        if not isinstance(mem, dict):
            return {"version": 1, "updated_at": None, "chats": {}, "task_candidates": []}
        mem.setdefault("version", 1)
        mem.setdefault("updated_at", None)
        mem.setdefault("chats", {})
        mem.setdefault("task_candidates", [])
        return mem
    except Exception:
        return {"version": 1, "updated_at": None, "chats": {}, "task_candidates": []}


MEMORY = _load_memory()
LAST_MEMORY_SAVE_TS = 0.0

def _memory_touch():
    MEMORY["updated_at"] = datetime.utcnow().isoformat()

def _chat_key(chat_id: int) -> str:
    return str(chat_id)

def memory_add_message(chat_id: int, chat_title: str, user_name: str, text: str, is_voice: bool):
    ck = _chat_key(chat_id)
    chats = MEMORY["chats"]
    if ck not in chats:
        chats[ck] = {"title": chat_title, "last_messages": []}
    chats[ck]["title"] = chat_title

    item = {
        "ts": int(time.time()),
        "from": user_name,
        "text": (text or "")[:2000],
        "voice": bool(is_voice),
    }
    chats[ck]["last_messages"].append(item)
    # cap
    if len(chats[ck]["last_messages"]) > MAX_LAST_MESSAGES_PER_CHAT:
        chats[ck]["last_messages"] = chats[ck]["last_messages"][-MAX_LAST_MESSAGES_PER_CHAT:]

    _memory_touch()

def memory_add_task_candidate(chat_id: int, chat_title: str, user_name: str, text: str, reason: str):
    arr = MEMORY.get("task_candidates", [])
    arr.append({
        "ts": int(time.time()),
        "chat_id": chat_id,
        "chat_title": chat_title,
        "from": user_name,
        "text": (text or "")[:2000],
        "reason": reason,
        "status": "new",
    })
    # cap
    if len(arr) > MAX_TASK_CANDIDATES:
        arr = arr[-MAX_TASK_CANDIDATES:]
    MEMORY["task_candidates"] = arr
    _memory_touch()

# ---------- RAILWAY API ----------
def _railway_ok() -> bool:
    return bool(RAILWAY_TOKEN and RAILWAY_PROJECT_ID and RAILWAY_ENVIRONMENT_ID and RAILWAY_SERVICE_ID)

def railway_set_variable(name: str, value: str) -> None:
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
                "name": name,
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
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Railway HTTPError: {e.code} {e.reason} | {body}") from e

    if "errors" in result and result["errors"]:
        raise RuntimeError(f"Railway GraphQL errors: {result['errors']}")

def save_memory_to_railway(force: bool = False):
    global LAST_MEMORY_SAVE_TS
    now = time.time()

    # чтобы не дергать Railway слишком часто
    if not force and (now - LAST_MEMORY_SAVE_TS) < 20:
        return

    try:
        railway_set_variable("MEMORY_JSON", json.dumps(MEMORY, ensure_ascii=False))
        LAST_MEMORY_SAVE_TS = now
        logging.info("MEMORY_JSON saved to Railway")
    except Exception as e:
        logging.warning(f"Failed to save MEMORY_JSON: {e}")

# ---------- IMPORTANCE FILTER ----------
def analyze_importance(text: str) -> tuple[bool, str]:
    """
    Возвращает (важно ли, причина).
    """
    t = (text or "").strip()
    if not t:
        return (False, "empty")

    low = t.lower()

    # вопрос — часто требует ответа
    if QUESTION_PATTERN.search(t):
        return (True, "question")

    # деньги/оплата
    if MONEY_PATTERN.search(t):
        return (True, "money")

    # даты/время/сроки
    for p in DATE_PATTERNS:
        if p.search(t):
            return (True, "date/time")

    # ключевые слова задач/договоренностей
    for kw in IMPORTANT_KEYWORDS:
        if kw in low:
            return (True, f"keyword:{kw}")

    # если длинное сообщение — иногда это “суть/объяснение”
    if len(t) >= 280:
        return (True, "long")

    return (False, "not_important")

# ---------- COMMANDS (только личка) ----------
def _is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == "private")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_private(update):
        return
    await update.message.reply_text(
        "Navi работает.\n\n"
        "Команды:\n"
        "/status — статус\n"
        "/set_hq — назначить HQ (обычно 1 раз)\n"
        "/memory — кратко показать память (последние задачи)\n"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_private(update):
        return
    hq = os.environ.get("ASSISTANT_CHAT_ID", "").strip()
    railway_ok = "OK" if _railway_ok() else "нет"
    watched = "все добавленные рабочие чаты (без /on /off)"
    await update.message.reply_text(
        "Статус:\n"
        f"- HQ chat_id: {hq if hq else 'не задан'}\n"
        f"- Railway доступ: {railway_ok}\n"
        f"- Режим: {watched}\n"
        f"- Память: чатов={len(MEMORY.get('chats', {}))}, задач-кандидатов={len(MEMORY.get('task_candidates', []))}"
    )

async def set_hq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Можно вызвать в любом чате, но чтобы не шуметь — подтверждение делаем ТОЛЬКО в личке.
    """
    chat = update.effective_chat
    if not chat:
        return

    target_id = str(chat.id)
    try:
        railway_set_variable("ASSISTANT_CHAT_ID", target_id)
    except Exception as e:
        # если команда вызвана в личке — скажем там
        if _is_private(update) and update.message:
            await update.message.reply_text(f"Не смог сохранить HQ:\n{e}")
        logging.warning(f"/set_hq failed: {e}")
        return

    # подтверждаем только в личке
    if _is_private(update) and update.message:
        await update.message.reply_text(f"Готово ✅ HQ = {target_id}\nRailway перезапустит сервис автоматически.")

async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_private(update):
        return
    tasks = MEMORY.get("task_candidates", [])[-10:]
    if not tasks:
        await update.message.reply_text("Память: задач-кандидатов пока нет.")
        return

    lines = ["Последние задач-кандидаты (до 10):"]
    for t in reversed(tasks):
        ts = datetime.utcfromtimestamp(t["ts"]).strftime("%d.%m %H:%M")
        lines.append(f"- {ts} | {t['chat_title']} | {t['reason']} | {t['text'][:80]}")
    await update.message.reply_text("\n".join(lines))

# ---------- MAIN FLOW ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat:
        return

    # 1) В личке ничего не делаем (кроме команд) — чтобы не было шума
    if chat.type == "private":
        return

    # 2) В рабочих группах/супергруппах молчим всегда
    if chat.type not in ("group", "supergroup"):
        return

    # 3) Нужен HQ
    if not ASSISTANT_CHAT_ID:
        return

    chat_title = chat.title or chat.username or "unknown_chat"
    user_name = (
        user.full_name if user and user.full_name
        else (user.username if user and user.username else "unknown_user")
    )

    text = (msg.text or msg.caption or "").strip()
    is_voice = msg.voice is not None

    # сохраняем контекст (память) всегда
    memory_add_message(chat.id, chat_title, user_name, text or "(voice/empty)", is_voice)

    # фильтр важности
    important, reason = analyze_importance(text)
    if not important and not is_voice:
        # не важно и не голос — не шлем в HQ
        return

    # если голос — считаем важным (пока без транскрибации)
    if is_voice and not important:
        reason = "voice"

    # кладем в память как кандидат задачи/дела (на будущее)
    memory_add_task_candidate(chat.id, chat_title, user_name, text or "(voice message)", reason)

    # отправляем в HQ
    preview = text if text else "(voice message)"
    payload = (
        "🧭 Navi • важное\n"
        f"Источник: {chat_title}\n"
        f"От: {user_name}\n"
        f"Причина: {reason}\n\n"
        f"{preview[:FORWARD_TEXT_LIMIT]}"
    )

    try:
        await context.bot.send_message(chat_id=int(ASSISTANT_CHAT_ID), text=payload)
    except Exception as e:
        logging.warning(f"Failed to forward to HQ: {e}")
        return

    # сохраняем память в Railway (редко, чтобы не спамить)
    save_memory_to_railway(force=False)

    logging.info(f"[FWD] chat='{chat_title}' from='{user_name}' reason='{reason}' text='{preview[:80]}'")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # команды — только в личке (чтобы не писать в рабочих чатах)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("set_hq", set_hq))
    app.add_handler(CommandHandler("memory", memory_cmd))

    # слушаем всё
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    logging.info("Navi bot started (silent groups -> HQ summaries + memory)")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
