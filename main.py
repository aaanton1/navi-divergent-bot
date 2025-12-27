import os
import re
import json
import time
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------- LOGGING ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ---------- ENV ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ASSISTANT_CHAT_ID = os.environ.get("ASSISTANT_CHAT_ID", "").strip()
TODOIST_TOKEN = os.environ.get("TODOIST_TOKEN", "").strip()

RAILWAY_TOKEN = os.environ.get("RAILWAY_TOKEN", "").strip()
RAILWAY_PROJECT_ID = os.environ.get("RAILWAY_PROJECT_ID", "").strip()
RAILWAY_ENVIRONMENT_ID = os.environ.get("RAILWAY_ENVIRONMENT_ID", "").strip()
RAILWAY_SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "").strip()

OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "").strip()  # will be set by /set_me

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not TODOIST_TOKEN:
    raise RuntimeError("TODOIST_TOKEN is not set")

# user timezone (+03:00)
LOCAL_TZ = timezone(timedelta(hours=3))

FORWARD_TEXT_LIMIT = 1500
MAX_TASK_CANDIDATES = 300

IMPORTANT_KEYWORDS = [
    "надо", "нужно", "сделай", "сделать", "задача", "поставь",
    "дедлайн", "срок", "проверь", "проверить", "проверим",
    "созвонимся", "встреча", "оплатить", "оплата", "договорились",
    "отправь", "пришли", "жду", "ждём", "ответь", "ответить",
    "согласуй", "озон", "ozon", "заказ", "партия", "поставка",
    "карточка", "размещение", "срочно", "важно", "сегодня", "завтра", "послезавтра"
]

DATE_PATTERNS = [
    re.compile(r"\b\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?\b"),     # 27.12, 27/12/2025
    re.compile(r"\b\d{1,2}:\d{2}\b"),                          # 10:30
    re.compile(r"\b\d{1,2}\s?(утра|вечера|дня|ночью)\b", re.I),# 10 утра
    re.compile(r"\b(сегодня|завтра|послезавтра)\b", re.I),
]
QUESTION_PATTERN = re.compile(r"\?")
MONEY_PATTERN = re.compile(r"(\b\d+[ ]?(₽|руб|р)\b)|(\b₽\s?\d+\b)", re.I)

# ---------- MEMORY (kept in Railway var MEMORY_JSON) ----------
MEMORY_JSON_RAW = os.environ.get("MEMORY_JSON", "").strip()

def _load_memory() -> dict:
    if not MEMORY_JSON_RAW:
        return {"version": 2, "updated_at": None, "task_candidates": []}
    try:
        mem = json.loads(MEMORY_JSON_RAW)
        if not isinstance(mem, dict):
            return {"version": 2, "updated_at": None, "task_candidates": []}
        mem.setdefault("version", 2)
        mem.setdefault("updated_at", None)
        mem.setdefault("task_candidates", [])
        return mem
    except Exception:
        return {"version": 2, "updated_at": None, "task_candidates": []}

MEMORY = _load_memory()
LAST_MEMORY_SAVE_TS = 0.0

def _memory_touch():
    MEMORY["updated_at"] = datetime.utcnow().isoformat()

def memory_add_task_candidate(payload: dict):
    arr = MEMORY.get("task_candidates", [])
    arr.append(payload)
    if len(arr) > MAX_TASK_CANDIDATES:
        arr = arr[-MAX_TASK_CANDIDATES:]
    MEMORY["task_candidates"] = arr
    _memory_touch()

def memory_get_candidate(cid: str):
    for item in reversed(MEMORY.get("task_candidates", [])):
        if item.get("candidate_id") == cid:
            return item
    return None

# ---------- Railway variableUpsert ----------
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
    if not force and (now - LAST_MEMORY_SAVE_TS) < 20:
        return
    if not _railway_ok():
        return
    try:
        railway_set_variable("MEMORY_JSON", json.dumps(MEMORY, ensure_ascii=False))
        LAST_MEMORY_SAVE_TS = now
        logging.info("MEMORY_JSON saved to Railway")
    except Exception as e:
        logging.warning(f"Failed to save MEMORY_JSON: {e}")

# ---------- Todoist REST v2 ----------
def todoist_create_task(content: str, description: str, due_string: str | None = None) -> dict:
    url = "https://api.todoist.com/rest/v2/tasks"
    body = {"content": content, "description": description}
    if due_string:
        body["due_string"] = due_string

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {TODOIST_TOKEN}")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Todoist HTTPError: {e.code} {e.reason} | {b}") from e

# ---------- Importance + draft extraction ----------
def analyze_importance(text: str) -> tuple[bool, str]:
    t = (text or "").strip()
    if not t:
        return (False, "empty")
    if QUESTION_PATTERN.search(t):
        return (True, "question")
    if MONEY_PATTERN.search(t):
        return (True, "money")
    for p in DATE_PATTERNS:
        if p.search(t):
            return (True, "date/time")
    low = t.lower()
    for kw in IMPORTANT_KEYWORDS:
        if kw in low:
            return (True, f"keyword:{kw}")
    if len(t) >= 280:
        return (True, "long")
    return (False, "not_important")

def guess_due_string(text: str) -> str | None:
    """
    Очень простой парсер: 'завтра 10:00', 'сегодня', 'послезавтра', 'в 10 утра'.
    Todoist хорошо понимает due_string по-русски НЕ всегда, поэтому делаем на английском.
    """
    t = (text or "").lower()

    day = None
    if "послезавтра" in t:
        day = 2
    elif "завтра" in t:
        day = 1
    elif "сегодня" in t:
        day = 0

    # time like 10:30
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", t)
    hh = mm = None
    if m:
        hh = int(m.group(1)); mm = int(m.group(2))
    else:
        # "10 утра"
        m2 = re.search(r"\b(\d{1,2})\s?(утра|вечера|дня|ночью)\b", t)
        if m2:
            hh = int(m2.group(1)); mm = 0
            part = m2.group(2)
            if part in ("вечера",) and hh < 12:
                hh += 12

    if day is None and hh is None:
        return None

    now = datetime.now(LOCAL_TZ)
    target = now.replace(second=0, microsecond=0)
    if day is not None:
        target = target + timedelta(days=day)
    if hh is not None:
        target = target.replace(hour=hh, minute=mm or 0)
        # если получилось “в прошлом” (например сегодня 10:00 уже прошло) — сдвинем на завтра
        if target < now:
            target = target + timedelta(days=1)

    # Todoist понимает ISO-like "YYYY-MM-DD HH:MM"
    return target.strftime("%Y-%m-%d %H:%M")

def make_task_draft(text: str, source_chat: str, source_user: str) -> tuple[str, str | None]:
    """
    Контент задачи: первая строка / короткая формулировка.
    """
    t = (text or "").strip()
    if not t:
        return ("Задача (из чата)", None)
    # обрежем
    first = t.splitlines()[0].strip()
    if len(first) > 120:
        first = first[:120].rstrip() + "…"
    content = first
    due = guess_due_string(t)
    return (content, due)

# ---------- Commands (private only) ----------
def _is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == "private")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_private(update):
        return
    await update.message.reply_text(
        "Navi на связи.\n\n"
        "Команды:\n"
        "/status — статус\n"
        "/set_me — привязать эту личку как твою (куда слать черновики)\n"
        "/memory — последние кандидаты\n\n"
        "Важно: в рабочих чатах я молчу, всё отправляю в HQ и в личку."
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_private(update):
        return
    await update.message.reply_text(
        "Статус:\n"
        f"- HQ chat_id: {ASSISTANT_CHAT_ID if ASSISTANT_CHAT_ID else 'не задан'}\n"
        f"- OWNER_CHAT_ID: {OWNER_CHAT_ID if OWNER_CHAT_ID else 'не задан (сделай /set_me)'}\n"
        f"- Railway доступ: {'OK' if _railway_ok() else 'нет'}\n"
        f"- Память: кандидатов={len(MEMORY.get('task_candidates', []))}"
    )

async def set_me_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_private(update):
        return
    if not _railway_ok():
        await update.message.reply_text("Railway доступ не настроен — не могу сохранить OWNER_CHAT_ID.")
        return
    me_id = str(update.effective_chat.id)
    try:
        railway_set_variable("OWNER_CHAT_ID", me_id)
        await update.message.reply_text("Готово ✅ Привязал эту личку как твою. Теперь буду присылать сюда черновики задач.")
    except Exception as e:
        await update.message.reply_text(f"Не смог сохранить OWNER_CHAT_ID:\n{e}")

async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_private(update):
        return
    items = MEMORY.get("task_candidates", [])[-10:]
    if not items:
        await update.message.reply_text("Пока нет кандидатов задач.")
        return
    lines = ["Последние 10 кандидатов:"]
    for it in reversed(items):
        ts = datetime.fromtimestamp(it["ts"], tz=LOCAL_TZ).strftime("%d.%m %H:%M")
        lines.append(f"- {ts} | {it.get('reason')} | {it.get('content','')}")
    await update.message.reply_text("\n".join(lines))

# ---------- Buttons / callbacks ----------
async def send_task_draft_to_owner(context: ContextTypes.DEFAULT_TYPE, candidate: dict):
    if not OWNER_CHAT_ID:
        return

    cid = candidate["candidate_id"]
    content = candidate["content"]
    due = candidate.get("due_string")
    reason = candidate.get("reason")
    source = candidate.get("chat_title")
    raw = candidate.get("raw_text", "")

    text = (
        "🧾 Черновик задачи\n"
        f"Источник: {source}\n"
        f"Причина: {reason}\n\n"
        f"**{content}**\n"
    )
    if due:
        text += f"\nСрок: {due}\n"
    text += f"\nТекст:\n{raw[:600]}"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Создать в Todoist", callback_data=f"todo_create:{cid}"),
            InlineKeyboardButton("❌ Пропустить", callback_data=f"todo_skip:{cid}"),
        ]
    ])

    await context.bot.send_message(
        chat_id=int(OWNER_CHAT_ID),
        text=text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if not data.startswith(("todo_create:", "todo_skip:")):
        return

    action, cid = data.split(":", 1)
    cand = memory_get_candidate(cid)
    if not cand:
        await query.edit_message_text("Не нашёл этот черновик (возможно, он старый).")
        return

    if action == "todo_skip":
        cand["status"] = "skipped"
        save_memory_to_railway(force=True)
        await query.edit_message_text("Ок, пропустил ✅")
        return

    # create
    try:
        task = todoist_create_task(
            content=cand["content"],
            description=cand["description"],
            due_string=cand.get("due_string"),
        )
        cand["status"] = "created"
        cand["todoist_task_id"] = task.get("id")
        save_memory_to_railway(force=True)

        msg = "Создал задачу в Todoist ✅"
        if cand.get("due_string"):
            msg += f"\nСрок: {cand['due_string']}"
        await query.edit_message_text(msg)
    except Exception as e:
        await query.edit_message_text(f"Не получилось создать в Todoist:\n{e}")

# ---------- Main message flow ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat:
        return

    # 1) В личке ничего не делаем (кроме команд)
    if chat.type == "private":
        return

    # 2) В рабочих группах молчим
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

    important, reason = analyze_importance(text)
    if not important and not is_voice:
        return

    if is_voice and not important:
        reason = "voice"

    preview = text if text else "(voice message)"

    # 4) В HQ — короткая выжимка (тихо)
    hq_payload = (
        "🧭 Navi • важное\n"
        f"Источник: {chat_title}\n"
        f"От: {user_name}\n"
        f"Причина: {reason}\n\n"
        f"{preview[:FORWARD_TEXT_LIMIT]}"
    )
    try:
        await context.bot.send_message(chat_id=int(ASSISTANT_CHAT_ID), text=hq_payload)
    except Exception as e:
        logging.warning(f"Failed to forward to HQ: {e}")
        return

    # 5) Сформировать черновик задачи и отправить владельцу (если привязан)
    content, due = make_task_draft(preview, chat_title, user_name)
    candidate_id = f"{int(time.time())}-{chat.id}"

    candidate = {
        "ts": int(time.time()),
        "candidate_id": candidate_id,
        "chat_id": chat.id,
        "chat_title": chat_title,
        "from": user_name,
        "raw_text": preview,
        "reason": reason,
        "content": content,
        "due_string": due,
        "description": f"Источник: {chat_title}\nОт: {user_name}\n\n{preview}",
        "status": "drafted",
    }

    memory_add_task_candidate(candidate)
    save_memory_to_railway(force=False)

    await send_task_draft_to_owner(context, candidate)

# ---------- MAIN ----------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("set_me", set_me_cmd))
    app.add_handler(CommandHandler("memory", memory_cmd))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    logging.info("Navi bot started (HQ + drafts -> Todoist)")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
