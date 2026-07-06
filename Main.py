"""
Shadow Monarch: Idle System — Telegram RPG Bot
aiogram 3 + aiosqlite (SQLite-персистентность).
"""

import asyncio
import logging
import os
import random
from functools import wraps

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. КОНФИГУРАЦИЯ И КОНСТАНТЫ
# ═══════════════════════════════════════════════════════════════════════════════

# Твой НОВЫЙ рабочий токен жестко прописан здесь!
TOKEN = "8616312461:AAEiBYj4VqUpul7jy--aeYzIGU9tMr0zRZs"

DB_PATH           = "game.db"
PROFILE_IMAGE_URL = "https://i.imgur.com/83uA2XF.jpeg"

# Админ-IDs (можешь вписать свой ID в кавычках)
ADMIN_IDS_STR: set[str] = {"7309915832"} 

def is_admin(user_id: int) -> bool:
    return str(user_id).strip() in ADMIN_IDS_STR

RANK_ORDER = ["E", "D", "C", "B", "A", "S", "SS", "SSS", "Национальный", "Монарх"]

MONSTERS_E = [
    {
        "name":  "Гоблин-разведчик",
        "emoji": "👺",
        "hp":    180,
        "dmg":   (5, 10),
        "gold":  150,
        "xp":    30,
        "image": "https://upload.wikimedia.org/wikipedia/commons/6/6e/Goblin_by_Arthur_Rackham.jpg",
    },
    {
        "name":  "Пещерный паук",
        "emoji": "🕷️",
        "hp":    280,
        "dmg":   (8, 15),
        "gold":  250,
        "xp":    50,
        "image": "https://upload.wikimedia.org/wikipedia/commons/1/16/Goliath_Bird-eating_Spider.jpg",
    },
    {
        "name":  "Снежный оборотень",
        "emoji": "🐺",
        "hp":    420,
        "dmg":   (12, 20),
        "gold":  450,
        "xp":    90,
        "image": "https://upload.wikimedia.org/wikipedia/commons/5/5a/Howlsnow.jpg",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# 2. БАЗА ДАННЫХ
# ═══════════════════════════════════════════════════════════════════════════════

async def db_init() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id       INTEGER PRIMARY KEY,
                name          TEXT UNIQUE NOT NULL COLLATE NOCASE,
                level         INTEGER NOT NULL DEFAULT 1,
                rank          TEXT    NOT NULL DEFAULT 'E',
                xp            INTEGER NOT NULL DEFAULT 0,
                gold          INTEGER NOT NULL DEFAULT 0,
                str_stat      INTEGER NOT NULL DEFAULT 5,
                agi_stat      INTEGER NOT NULL DEFAULT 5,
                vit_stat      INTEGER NOT NULL DEFAULT 5,
                int_stat      INTEGER NOT NULL DEFAULT 5,
                stat_points   INTEGER NOT NULL DEFAULT 5,
                hp            INTEGER NOT NULL DEFAULT 160,
                max_hp        INTEGER NOT NULL DEFAULT 160,
                photo_msg_id  INTEGER,
                photo_chat_id INTEGER,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.commit()

async def db_get(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def db_create(user_id: int, name: str) -> dict:
    init_hp = 100 + 5 * 12   
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO players (user_id, name, hp, max_hp) VALUES (?, ?, ?, ?)",
            (user_id, name, init_hp, init_hp),
        )
        await db.commit()
    return await db_get(user_id)

async def db_update(user_id: int, **kwargs) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE players SET {cols} WHERE user_id = ?", vals)
        await db.commit()

async def db_name_taken(name: str, exclude_id: int | None = None) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        if exclude_id is not None:
            async with db.execute(
                "SELECT 1 FROM players WHERE name = ? COLLATE NOCASE AND user_id != ?",
                (name, exclude_id),
            ) as cur:
                return await cur.fetchone() is not None
        async with db.execute("SELECT 1 FROM players WHERE name = ? COLLATE NOCASE", (name,)) as cur:
            return await cur.fetchone() is not None

async def db_all() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM players ORDER BY level DESC, xp DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]

async def db_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM players") as cur:
            row = await cur.fetchone()
            return row[0]

# ═══════════════════════════════════════════════════════════════════════════════
# 3. ИГРОВЫЕ ФОРМУЛЫ
# ═══════════════════════════════════════════════════════════════════════════════

def calc_max_hp(vit: int) -> int:
    return 100 + vit * 12

def calc_phys_dmg(str_stat: int) -> tuple[int, int]:
    base = 10 + str_stat * 2
    return (max(1, base - 5), base + 5)

def calc_crit_chance(agi: int) -> float:
    return min(50.0, 5.0 + agi * 0.5)

def calc_magic_dmg(int_stat: int) -> int:
    return 20 + int_stat * 3

def xp_for_level(level: int) -> int:
    return level * 100

def apply_levelups(p: dict) -> list[str]:
    notices: list[str] = []
    while p["xp"] >= xp_for_level(p["level"]):
        p["xp"]         -= xp_for_level(p["level"])
        p["level"]      += 1
        p["stat_points"] += 5
        new_max          = calc_max_hp(p["vit_stat"])
        p["max_hp"]      = new_max
        p["hp"]          = new_max   
        notices.append(
            f"🎉 <b>УРОВЕНЬ ПОВЫШЕН!</b> Вы достигли <b>{p['level']}</b> уровня!\n"
            f"❤️ HP полностью восстановлено! Получено <b>+5 очков</b> характеристик!"
        )
    return notices

def reset_stats(p: dict) -> dict:
    total   = 5 + (p["level"] - 1) * 5
    new_max = calc_max_hp(5)
    return {
        "str_stat": 5, "agi_stat": 5, "vit_stat": 5, "int_stat": 5,
        "stat_points": total,
        "max_hp": new_max,
        "hp": min(p["hp"], new_max),
    }

# ═══════════════════════════════════════════════════════════════════════════════
# 4. FSM-СОСТОЯНИЯ
# ═══════════════════════════════════════════════════════════════════════════════

class RegState(StatesGroup):
    waiting_name = State()
    waiting_path = State()

class AdminState(StatesGroup):
    waiting_gold = State()
    waiting_xp   = State()
    waiting_rank = State()

# ═══════════════════════════════════════════════════════════════════════════════
# 5. UI-ХЭЛПЕРЫ
# ═══════════════════════════════════════════════════════════════════════════════

def _hp_bar(hp: int, max_hp: int, length: int = 10) -> str:
    filled = min(length, round(max(hp, 0) / max_hp * length)) if max_hp > 0 else 0
    return "🟩" * filled + "⬛" * (length - filled)

def cap_menu(p: dict) -> str:
    xp_need = xp_for_level(p["level"])
    return (
        f"👤 <b>{p['name']}</b>  •  🏷 Ранг <b>{p['rank']}</b>  •  ⭐ Ур. <b>{p['level']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"❤️ {_hp_bar(p['hp'], p['max_hp'])}  {p['hp']}/{p['max_hp']} HP\n"
        f"📊 XP: <b>{p['xp']} / {xp_need}</b>\n"
        f"💰 Золото: <b>{p['gold']} G</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Выберите раздел:"
    )

def cap_stats(p: dict) -> str:
    dmg_min, dmg_max = calc_phys_dmg(p["str_stat"])
    crit   = calc_crit_chance(p["agi_stat"])
    magic  = calc_magic_dmg(p["int_stat"])
    max_hp = calc_max_hp(p["vit_stat"])
    return (
        f"📊 <b>Характеристики — {p['name']}</b>\n"
        f"Ур. <b>{p['level']}</b>  •  Ранг <b>{p['rank']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💪 Сила:          <b>{p['str_stat']}</b>  →  Урон: {dmg_min}–{dmg_max}\n"
        f"🏃 Ловкость:      <b>{p['agi_stat']}</b>  →  Крит: {crit:.1f}%\n"
        f"🫀 Выносливость:  <b>{p['vit_stat']}</b>  →  HP макс: {max_hp}\n"
        f"🧠 Интеллект:     <b>{p['int_stat']}</b>  →  Магия: {magic}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✨ Свободных очков: <b>{p['stat_points']}</b>"
    )

def cap_gates() -> str:
    return (
        "🚪 <b>Врата Охотников</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>Доступные Врата:</b>\n\n"
        "🟢 E-ранг — Низшее подземелье\n"
        "    👺 Гоблины · 🕷️ Пауки · 🐺 Оборотни\n"
        "    Ур. 0+  •  150–450 G / 30–90 XP\n\n"
        "🔒 D-ранг — <i>Требуется ранг D</i>\n"
        "🔒 C-ранг — <i>Требуется ранг C</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите врата:"
    )

def cap_combat(p_name: str, p_hp: int, p_max_hp: int, m: dict, battle_log: list[str]) -> str:
    p_bar = _hp_bar(p_hp, p_max_hp)
    m_bar = _hp_bar(m["current_hp"], m["hp"])
    lines = [
        f"{m['emoji']} <b>{m['name']}</b>",
        f"❤️ {m_bar}  {max(m['current_hp'], 0)}/{m['hp']} HP",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"👤 <b>{p_name}</b>",
        f"❤️ {p_bar}  {p_hp}/{p_max_hp} HP",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    lines.extend(battle_log[-6:])
    return "\n".join(lines)

def kb_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚪 Врата",   callback_data="gates"),
            InlineKeyboardButton(text="⚔️ Арена",   callback_data="arena"),
        ],
        [
            InlineKeyboardButton(text="📊 Статы",   callback_data="stats"),
            InlineKeyboardButton(text="🛒 Магазин", callback_data="shop"),
        ],
        [
            InlineKeyboardButton(text="⚖️ Рынок",   callback_data="market"),
            InlineKeyboardButton(text="⭐ Донат",   callback_data="donate"),
        ],
    ])

def kb_stats(p: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💪 Сила +1  ({p['str_stat']})",         callback_data="up_str")],
        [InlineKeyboardButton(text=f"🏃 Ловкость +1  ({p['agi_stat']})",     callback_data="up_agi")],
        [InlineKeyboardButton(text=f"🫀 Выносливость +1  ({p['vit_stat']})", callback_data="up_vit")],
        [InlineKeyboardButton(text=f"🧠 Интеллект +1  ({p['int_stat']})",    callback_data="up_int")],
        [InlineKeyboardButton(text="🔄 Сбросить характеристики",             callback_data="reset_stats")],
        [InlineKeyboardButton(text="◀️ В меню",                               callback_data="menu")],
    ])

def kb_gates() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ Начать зачистку E-ранга", callback_data="gate_run_e")],
        [InlineKeyboardButton(text="◀️ В меню",                   callback_data="menu")],
    ])

def kb_combat() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ Атаковать", callback_data="battle_attack")],
        [InlineKeyboardButton(text="🏃 Сбежать",   callback_data="battle_flee")],
    ])

def kb_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика",    callback_data="adm_stats")],
        [InlineKeyboardButton(text="💰 Выдать золото", callback_data="adm_gold")],
        [InlineKeyboardButton(text="✨ Выдать опыт",   callback_data="adm_xp")],
        [InlineKeyboardButton(text="🏷 Изменить ранг", callback_data="adm_rank")],
    ])

# ═══════════════════════════════════════════════════════════════════════════════
# 6. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БОТА
# ═══════════════════════════════════════════════════════════════════════════════

async def edit_photo(bot: Bot, p: dict, caption: str, kb: InlineKeyboardMarkup, new_photo_url: str = None) -> None:
    mid = p.get("photo_msg_id")
    cid = p.get("photo_chat_id")
    if not mid or not cid:
        return
    try:
        if new_photo_url:
            from aiogram.types import InputMediaPhoto
            await bot.edit_message_media(
                chat_id=cid, message_id=mid,
                media=InputMediaPhoto(media=new_photo_url, caption=caption, parse_mode="HTML"),
                reply_markup=kb
            )
        else:
            await bot.edit_message_caption(chat_id=cid, message_id=mid, caption=caption, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return
        if "message to edit not found" in err or "message can't be edited" in err or "media_caption_blank" in err:
            url = new_photo_url if new_photo_url else PROFILE_IMAGE_URL
            msg = await bot.send_photo(chat_id=cid, photo=url, caption=caption, reply_markup=kb, parse_mode="HTML")
            p["photo_msg_id"] = msg.message_id
            if p.get("user_id"):
                await db_update(p["user_id"], photo_msg_id=msg.message_id)

async def send_game_photo(bot: Bot, chat_id: int, uid: int, caption: str, kb: InlineKeyboardMarkup, photo_url: str = PROFILE_IMAGE_URL) -> None:
    msg = await bot.send_photo(chat_id=chat_id, photo=photo_url, caption=caption, reply_markup=kb, parse_mode="HTML")
    await db_update(uid, photo_msg_id=msg.message_id, photo_chat_id=chat_id)

async def edit_combat_photo(bot: Bot, chat_id: int, msg_id: int, caption: str, kb: InlineKeyboardMarkup) -> None:
    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=caption, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            log.error("edit_combat_photo ошибка: %s", e)

def admin_required(handler):
    @wraps(handler)
    async def wrapper(event, *args, **kwargs):
        from_user = getattr(event, "from_user", None)
        uid = from_user.id if from_user else None
        if uid is None or not is_admin(uid):
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ запрещён.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("⛔ У вас нет прав администратора.")
            return
        return await handler(event, *args, **kwargs)
    return wrapper

# ═══════════════════════════════════════════════════════════════════════════════
# 7. РЕГИСТРАЦИЯ И ХЭНДЛЕРЫ
# ═══════════════════════════════════════════════════════════════════════════════

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    p   = await db_get(uid)

    if p:
        await message.answer(f"⚡ Приветствуем снова, Охотник <b>{p['name']}</b>!\nСистема готова к работе.", parse_mode="HTML")
        await send_game_photo(bot, message.chat.id, uid, cap_menu(p), kb_menu())
    else:
        await message.answer(
            "⚡ <b>Система обнаружения пробудилась.</b>\n\nПриветствуем тебя, <b>Охотник</b>!\nСистема выбрала тебя среди тысяч.\n\n❓ <i>Как нам называть тебя в игре?</i>\nОтправь своё имя текстовым сообщением.",
            parse_mode="HTML",
        )
        await state.set_state(RegState.waiting_name)

@router.message(RegState.waiting_name)
async def reg_name(message: Message, bot: Bot, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or name.startswith("/") or len(name) > 32:
        return await message.answer("⚠️ Введи корректное имя (1–32 символа, без команд).")

    if await db_name_taken(name):
        return await message.answer(f"⚠️ Имя <b>{name}</b> уже занято другим Охотником.\nПридумай другое и отправь снова.", parse_mode="HTML")

    uid = message.from_user.id
    await db_create(uid, name)
    await state.set_state(RegState.waiting_path)

    try:
        await message.delete()
    except Exception:
        pass

    await message.answer(
        f"✅ Отлично, <b>{name}</b>!\n\nТвой путь начинается здесь. Что ты выберешь?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚔️ Начать сражаться",     callback_data="onboard_fight")],
            [InlineKeyboardButton(text="📖 Узнать больше об игре", callback_data="onboard_learn")],
        ]),
    )

@router.callback_query(RegState.waiting_path, F.data == "onboard_fight")
async def cb_onboard_fight(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    uid = callback.from_user.id
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_game_photo(bot, callback.message.chat.id, uid, cap_gates(), kb_gates())
    await callback.answer()

@router.callback_query(RegState.waiting_path, F.data == "onboard_learn")
async def cb_onboard_learn(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    uid = callback.from_user.id
    p   = await db_get(uid)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_game_photo(bot, callback.message.chat.id, uid, cap_menu(p), kb_menu())
    await callback.answer()

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    p = await db_get(callback.from_user.id)
    await edit_photo(bot, p, cap_menu(p), kb_menu(), PROFILE_IMAGE_URL)
    await callback.answer()

@router.callback_query(F.data == "gates")
async def cb_gates(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    p = await db_get(callback.from_user.id)
    await edit_photo(bot, p, cap_gates(), kb_gates(), PROFILE_IMAGE_URL)
    await callback.answer()

@router.callback_query(F.data.in_({"shop", "market", "donate", "arena"}))
async def cb_stub(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    p = await db_get(callback.from_user.id)
    labels = {"shop": "🛒 Магазин", "market": "⚖️ Рынок", "donate": "⭐ Донат", "arena": "⚔️ Арена"}
    caption = f"{labels[callback.data]}\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Раздел в разработке.</i>\n\nСледи за обновлениями, Охотник!"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В меню", callback_data="menu")]])
    await edit_photo(bot, p, caption, kb, PROFILE_IMAGE_URL)
    await callback.answer()

@router.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    p = await db_get(callback.from_user.id)
    await edit_photo(bot, p, cap_stats(p), kb_stats(p), PROFILE_IMAGE_URL)
    await callback.answer()

_STAT_MAP = {"up_str": "str_stat", "up_agi": "agi_stat", "up_vit": "vit_stat", "up_int": "int_stat"}

@router.callback_query(F.data.in_(set(_STAT_MAP.keys())))
async def cb_upgrade(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    uid = callback.from_user.id
    p   = await db_get(uid)

    if p["stat_points"] <= 0:
        return await callback.answer("У вас нет свободных очков характеристик!", show_alert=True)

    col     = _STAT_MAP[callback.data]
    new_val = p[col] + 1
    updates = {col: new_val, "stat_points": p["stat_points"] - 1}
    if col == "vit_stat":
        new_max = calc_max_hp(new_val)
        ratio   = p["hp"] / p["max_hp"] if p["max_hp"] > 0 else 1.0
        updates["max_hp"] = new_max
        updates["hp"]     = min(new_max, round(ratio * new_max))

    await db_update(uid, **updates)
    p = await db_get(uid)
    await edit_photo(bot, p, cap_stats(p), kb_stats(p))
    await callback.answer(f"✅ {col.split('_')[0].upper()} +1")

@router.callback_query(F.data == "reset_stats")
async def cb_reset_stats(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    uid = callback.from_user.id
    p   = await db_get(uid)
    await db_update(uid, **reset_stats(p))
    p = await db_get(uid)
    await edit_photo(bot, p, cap_stats(p), kb_stats(p))
    await callback.answer("🔄 Характеристики сброшены!")

# ═══════════════════════════════════════════════════════════════════════════════
# 10. ВРАТА — БОЙ
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "gate_run_e")
async def cb_gate_run_e(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    uid     = callback.from_user.id
    p       = await db_get(uid)
    chat_id = callback.message.chat.id

    template = random.choice(MONSTERS_E)
    monster  = {**template, "current_hp": template["hp"]}

    p_hp = p["max_hp"]
    await db_update(uid, hp=p_hp)

    battle_log = ["⚔️ <b>Бой начался!</b>", f"{monster['emoji']} <b>{monster['name']}</b> встаёт перед вами!"]
    caption = cap_combat(p["name"], p_hp, p["max_hp"], monster, battle_log)
    photo_url = monster["image"]

    try:
        await callback.message.delete()
    except Exception:
        pass

    try:
        msg = await bot.send_photo(chat_id=chat_id, photo=photo_url, caption=caption, reply_markup=kb_combat(), parse_mode="HTML")
    except Exception:
        msg = await bot.send_photo(chat_id=chat_id, photo=PROFILE_IMAGE_URL, caption=caption, reply_markup=kb_combat(), parse_mode="HTML")

    await state.update_data(monster=monster, player_hp=p_hp, battle_log=battle_log, combat_msg_id=msg.message_id, combat_chat_id=chat_id)
    await callback.answer()

@router.callback_query(F.data == "battle_attack")
async def cb_battle_attack(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("monster"):
        await callback.answer("Сессия боя не найдена. Начни заново.", show_alert=True)
        return

    uid            = callback.from_user.id
    p              = await db_get(uid)
    monster        = data["monster"]
    p_hp           = data["player_hp"]
    battle_log     = data["battle_log"]
    combat_msg_id  = data["combat_msg_id"]
    combat_chat_id = data["combat_chat_id"]

    dmg_min, dmg_max = calc_phys_dmg(p["str_stat"])
    p_dmg = random.randint(dmg_min, dmg_max)
    is_crit = random.random() * 100 < calc_crit_chance(p["agi_stat"])
    if is_crit:
        p_dmg *= 2
        battle_log.append(f"⚡ <b>КРИТ!</b> Вы нанесли <b>{p_dmg}</b> урона!")
    else:
        battle_log.append(f"⚔️ Вы нанесли <b>{p_dmg}</b> урона.")

    monster["current_hp"] = max(0, monster["current_hp"] - p_dmg)

    if monster["current_hp"] <= 0:
        await state.clear()
        p_dict          = dict(p)
        p_dict["gold"] += monster["gold"]
        p_dict["xp"]   += monster["xp"]
        p_dict["hp"]    = p_hp
        notices = apply_levelups(p_dict)

        await db_update(uid, hp=p_dict["hp"], gold=p_dict["gold"], xp=p_dict["xp"], level=p_dict["level"], stat_points=p_dict["stat_points"], max_hp=p_dict["max_hp"])
        xp_need = xp_for_level(p_dict["level"])
        result_lines = [
            "🎉 <b>Победа!</b>", "━━━━━━━━━━━━━━━━━━━━━━",
            f"Вы уничтожили {monster['emoji']} <b>{monster['name']}</b>!", "",
            f"💰 +{monster['gold']} G  •  ✨ +{monster['xp']} XP",
            f"⭐ Ур. {p_dict['level']}  •  XP: {p_dict['xp']}/{xp_need}",
            f"💰 Всего: {p_dict['gold']} G",
        ]
        if notices:
            result_lines += [""] + notices

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚔️ Ещё раз!",  callback_data="gate_run_e")],
            [InlineKeyboardButton(text="◀️ В меню",    callback_data="menu")],
        ])
        await edit_combat_photo(bot, combat_chat_id, combat_msg_id, "\n".join(result_lines), kb)
        await callback.answer("🎉 Победа!")
        return

    m_dmg = random.randint(monster["dmg"][0], monster["dmg"][1])
    p_hp  = max(0, p_hp - m_dmg)
    battle_log.append(f"{monster['emoji']} {monster['name']} нанёс вам <b>{m_dmg}</b> урона.")

    if p_hp <= 0:
        await state.clear()
        await db_update(uid, hp=1)
        caption = "💀 <b>Вы потеряли сознание во Вратах!</b>\n━━━━━━━━━━━━━━━━━━━━━━\nСистема восстанавливает ваши силы...\n\n<i>Прокачайте характеристики и вернитесь, Охотник!</i>"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Прокачать статы", callback_data="stats")],[InlineKeyboardButton(text="◀️ В меню", callback_data="menu")]])
        await edit_combat_photo(bot, combat_chat_id, combat_msg_id, caption, kb)
        await callback.answer("💀 Поражение...")
        return

    await state.update_data(monster=monster, player_hp=p_hp, battle_log=battle_log)
    await db_update(uid, hp=p_hp)
    caption = cap_combat(p["name"], p_hp, p["max_hp"], monster, battle_log)
    await edit_combat_photo(bot, combat_chat_id, combat_msg_id, caption, kb_combat())
    await callback.answer()

@router.callback_query(F.data == "battle_flee")
async def cb_battle_flee(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("monster"):
        await callback.answer("Сессия боя не найдена. Начни заново.", show_alert=True)
        return

    uid            = callback.from_user.id
    p              = await db_get(uid)
    monster        = data["monster"]
    p_hp           = data["player_hp"]
    battle_log     = data["battle_log"]
    combat_msg_id  = data["combat_msg_id"]
    combat_chat_id = data["combat_chat_id"]

    if random.randint(1, 100) <= 20:
        await state.clear()
        caption = "🏃 <b>Вы успешно сбежали из Врат</b>\nи вернулись в безопасное место!\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Прогресс зачистки потерян.</i>"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚪 К Вратам", callback_data="gates")],[InlineKeyboardButton(text="◀️ В меню", callback_data="menu")]])
        await edit_combat_photo(bot, combat_chat_id, combat_msg_id, caption, kb)
        await callback.answer("🏃 Побег удался!")
        return

    battle_log.append("❌ <b>Побег провалился!</b> Монстр блокирует выход и бьет в ответ!")
    m_dmg = random.randint(monster["dmg"][0], monster["dmg"][1])
    p_hp  = max(0, p_hp - m_dmg)
    battle_log.append(f"{monster['emoji']} {monster['name']} нанёс вам <b>{m_dmg}</b> урона.")

    if p_hp <= 0:
        await state.clear()
        await db_update(uid, hp=1)
        caption = "💀 <b>Вы потеряли сознание во Вратах!</b>\n━━━━━━━━━━━━━━━━━━━━━━\nСистема восстанавливает ваши силы...\n\n<i>Прокачайте характеристики и вернитесь, Охотник!</i>"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Прокачать статы", callback_data="stats")],[InlineKeyboardButton(text="◀️ В меню", callback_data="menu")]])
        await edit_combat_photo(bot, combat_chat_id, combat_msg_id, caption, kb)
        await callback.answer("💀 Поражение...")
        return

    await state.update_data(monster=monster, player_hp=p_hp, battle_log=battle_log)
    await db_update(uid, hp=p_hp)
    caption = cap_combat(p["name"], p_hp, p["max_hp"], monster, battle_log)
    await edit_combat_photo(bot, combat_chat_id, combat_msg_id, caption, kb_combat())
    await callback.answer(f"❌ Побег провалился!")

# ═══════════════════════════════════════════════════════════════════════════════
# 12. АДМИН-ПАНЕЛЬ
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    if not is_admin(uid):
        return await message.answer(f"⛔ У вас нет прав администратора.\n<code>Ваш ID: {uid}</code>", parse_mode="HTML")
    await state.clear()
    total = await db_count()
    await message.answer(f"🔐 <b>Панель администратора</b>\n\nВсего Охотников: <b>{total}</b>\n\nВыберите действие:", parse_mode="HTML", reply_markup=kb_admin())

@router.callback_query(F.data == "adm_stats")
@admin_required
async def adm_stats(callback: CallbackQuery) -> None:
    players = await db_all()
    lines   = [f"📊 <b>Статистика</b>  •  Всего: <b>{len(players)}</b> Охотников\n"]
    if players:
        lines.append("<b>Топ-5 по уровню:</b>")
        for i, pl in enumerate(players[:5], 1):
            lines.append(f"{i}. <b>{pl['name']}</b>  —  Ур.{pl['level']} | {pl['gold']} G | Ранг {pl['rank']}")
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb_admin())
    await callback.answer()

@router.callback_query(F.data == "adm_gold")
@admin_required
async def adm_gold_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminState.waiting_gold)
    await callback.message.answer("💰 Введи: <b>имя_игрока количество_золота</b>\nПример: Дамир 5000", parse_mode="HTML")
    await callback.answer()

@router.message(AdminState.waiting_gold)
@admin_required
async def adm_gold_apply(message: Message, state: FSMContext) -> None:
    await state.clear()
    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        return await message.answer("⚠️ Формат: <b>имя количество</b>", parse_mode="HTML")
    name, delta = parts[0], int(parts[1])
    target = next((p for p in await db_all() if p["name"].lower() == name.lower()), None)
    if not target:
        return await message.answer(f"❌ Игрок <b>{name}</b> не найден.", parse_mode="HTML")
    new_gold = max(0, target["gold"] + delta)
    await db_update(target["user_id"], gold=new_gold)
    await message.answer(f"🔐 <b>{target['name']}</b>: {target['gold']} G → <b>{new_gold} G</b>", parse_mode="HTML")

@router.callback_query(F.data == "adm_xp")
@admin_required
async def adm_xp_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminState.waiting_xp)
    await callback.message.answer("✨ Введи: <b>имя_игрока количество_опыта</b>\nПример: Дамир 500", parse_mode="HTML")
    await callback.answer()

@router.message(AdminState.waiting_xp)
@admin_required
async def adm_xp_apply(message: Message, state: FSMContext) -> None:
    await state.clear()
    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        return await message.answer("⚠️ Формат: <b>имя количество</b>", parse_mode="HTML")
    name, delta = parts[0], int(parts[1])
    target = next((p for p in await db_all() if p["name"].lower() == name.lower()), None)
    if not target:
        return await message.answer(f"❌ Игрок <b>{name}</b> не найден.", parse_mode="HTML")
    
    p_dict = dict(target)
    p_dict["xp"] += delta
    notices = apply_levelups(p_dict)
    
    await db_update(target["user_id"], xp=p_dict["xp"], level=p_dict["level"], stat_points=p_dict["stat_points"], max_hp=p_dict["max_hp"], hp=p_dict["hp"])
    msg_text = f"✨ <b>{target['name']}</b>: Получил <b>{delta} XP</b>."
    if notices:
        msg_text += "\n" + "\n".join(notices)
    await message.answer(msg_text, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════════════════
# 13. ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    await db_init()
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
