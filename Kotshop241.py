# bothost_bot.py — запускается на BotHost
import asyncio
import os
import json
import logging
import traceback
import time
import math

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from dotenv import load_dotenv

load_dotenv()

# ─── Логирование ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("kotshop-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
VPS_API_URL = os.getenv("VPS_API_URL")
API_SECRET = os.getenv("API_SECRET", "change-me")

# ─── Группа отзывов по умолчанию ───
REVIEW_CHAT_ID = os.getenv("REVIEW_CHAT_ID", "@otzivkotshop241")

# ─── БАЛАНС: список ID администраторов (через запятую в .env) ───
ADMIN_IDS = set()
_admin_ids_raw = os.getenv("ADMIN_IDS", "")
if _admin_ids_raw:
    ADMIN_IDS = {int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip().isdigit()}
logger.info(f"ADMIN_IDS: {ADMIN_IDS if ADMIN_IDS else '(не заданы)'}")

# ─── БАЛАНС: глобальная переменная и файл ───
kotshop_balance: float | None = None
BALANCE_FILE = os.getenv("BALANCE_FILE", "kotshop_balance.json")

if not all([BOT_TOKEN, VPS_API_URL]):
    raise ValueError("Не заданы переменные окружения: BOT_TOKEN, VPS_API_URL")

logger.info(f"VPS_API_URL = {VPS_API_URL}")
logger.info(f"API_SECRET задан: {'да' if API_SECRET != 'change-me' else 'НЕТ (значение по умолчанию!)'}")
logger.info(f"REVIEW_CHAT_ID задан: {'да' if REVIEW_CHAT_ID else 'НЕТ'}")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ─── Глобальная HTTP-сессия для переиспользования соединений ───
http_session: aiohttp.ClientSession | None = None


async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            connector=aiohttp.TCPConnector(limit=20, limit_per_host=10),
        )
    return http_session


# ─── Каталог товаров ───
PRICE_MARKUP = 1.01  # накрутка 1% для недоступных товаров (на будущее)

def _apply_markup(price: int) -> int:
    """Применяет 1% наценки и округляет вверх до целого рубля."""
    return math.ceil(price * PRICE_MARKUP)

# ─── Список товаров, доступных для покупки ───
ALLOWED_PRODUCTS = {"60uc", "120uc", "180uc", "240uc"}

PRODUCTS = {
    # ── Доступные товары: фиксированные цены ──
    "60uc":   {"name": "60 UC",   "price": 85,  "amount_kopecks": 85  * 100},
    "120uc":  {"name": "120 UC",  "price": 171, "amount_kopecks": 171 * 100},
    "180uc":  {"name": "180 UC",  "price": 258, "amount_kopecks": 258 * 100},
    "240uc":  {"name": "240 UC",  "price": 345, "amount_kopecks": 345 * 100},
    # ── Недоступные товары: цены с наценкой (на будущее) ──
    "325uc":  {"name": "325 UC",  "price": _apply_markup(410),  "amount_kopecks": _apply_markup(410)  * 100},
    "385uc":  {"name": "385 UC",  "price": _apply_markup(502),  "amount_kopecks": _apply_markup(502)  * 100},
    "445uc":  {"name": "445 UC",  "price": _apply_markup(575),  "amount_kopecks": _apply_markup(575)  * 100},
    "660uc":  {"name": "660 UC",  "price": _apply_markup(819),  "amount_kopecks": _apply_markup(819)  * 100},
    "720uc":  {"name": "720 UC",  "price": _apply_markup(902),  "amount_kopecks": _apply_markup(902)  * 100},
    "985uc":  {"name": "985 UC",  "price": _apply_markup(1230), "amount_kopecks": _apply_markup(1230) * 100},
    "1320uc": {"name": "1320 UC", "price": _apply_markup(1639), "amount_kopecks": _apply_markup(1639) * 100},
    "1800uc": {"name": "1800 UC", "price": _apply_markup(2049), "amount_kopecks": _apply_markup(2049) * 100},
    "1920uc": {"name": "1920 UC", "price": _apply_markup(2214), "amount_kopecks": _apply_markup(2214) * 100},
    "2125uc": {"name": "2125 UC", "price": _apply_markup(2479), "amount_kopecks": _apply_markup(2479) * 100},
    "2460uc": {"name": "2460 UC", "price": _apply_markup(2870), "amount_kopecks": _apply_markup(2870) * 100},
    "3850uc": {"name": "3850 UC", "price": _apply_markup(4119), "amount_kopecks": _apply_markup(4119) * 100},
    "4510uc": {"name": "4510 UC", "price": _apply_markup(4979), "amount_kopecks": _apply_markup(4979) * 100},
}

PRODUCT_GRID = [
    "60uc", "120uc", "180uc",
    "240uc", "325uc", "385uc",
    "445uc", "660uc", "720uc",
    "985uc", "1320uc", "1800uc",
    "1920uc", "2125uc", "2460uc",
    "3850uc", "4510uc",
]


# ─── FSM ───
class OrderFlow(StatesGroup):
    waiting_for_id = State()
    waiting_for_rating = State()
    waiting_for_review_text = State()


# ─── Тексты ───
WELCOME_TEXT = (
    "Добро пожаловать в Telegram-бот KotShop241! Мы работаем официально через Т-Банк "
    "и даём возможность быстро и с гарантией пополнить любой сервис из нашего каталога. "
    "Также помогаем находить недоступные игры в Steam для пользователей из РФ."
)

MENU_TEXT = (
    "Магазин работает круглосуточно, за исключением технических работ. "
    "О проведении технических работ сообщается в группе @KotShop241 — "
    "подпишитесь, чтобы получать актуальную информацию."
)

POLICY_TEXT = (
    "Магазин KotShop241 является официальным продавцом виртуальных товаров и осуществляет "
    "все расчёты в строгом соответствии с действующим законодательством Российской Федерации, "
    "включая требования Федерального закона «О национальной платёжной системе» и иные "
    "нормативно-правовые акты, регулирующие оборот цифровых товаров и проведение платежей.\n\n"
    "Реализация товаров осуществляется исключительно в рамках заключённого публичного "
    "договора-оферты, размещённого на официальном ресурсе магазина. Приобретение игровой "
    "валюты, игр и иных виртуальных товаров подтверждает согласие покупателя с условиями "
    "оферты и правилами работы магазина.\n\n"
    "Любые действия, направленные на нарушение установленных правил, в том числе попытки "
    "неправомерного получения выгоды, обхода платёжных механизмов, использования "
    "мошеннических схем либо иного злоупотребления условиями предоставления услуг, "
    "расцениваются как существенное нарушение договорных обязательств и могут служить "
    "основанием для обращения в правоохранительные органы, а собранные материалы — быть использованы "
    "в качестве доказательной базы в рамках административного или уголовного производства "
    "в соответствии с Уголовным кодексом Российской Федерации и Кодексом Российской "
    "Федерации об административных правонарушениях.\n\n"
    "Магазин KotShop241 реализует игровую валюту, игровые аккаунты, внутриигровые предметы, "
    "ключи активации игр и иные виртуальные товары для популярных игровых платформ и "
    "сервисов. Ассортимент и условия реализации товаров определяются действующими правилами "
    "магазина и положениями оферты, обязательными для ознакомления перед совершением покупки."
)

SUPPORT_TEXT = (
    "Поддержка отвечает в течение 24 часов. Пожалуйста, не дублируйте сообщения — "
    "вместо этого следуйте инструкции ниже.\n\n"
    "1) Если бот не отправил товар, перешлите переписку с ботом в чат поддержки.\n"
    "2) Дождитесь ответа. Если подтвердится, что товар не был отправлен на указанный "
    "вами способ доставки, средства вернут.\n"
    "3) Не нервничайте и не ищите виноватых — просто опишите, что произошло, и укажите "
    "причину, которую вам назвали.\n"
    "4) Объясните ситуацию развёрнуто: что заказывали, когда, каким способом должны были "
    "получить товар и что ответил бот."
)

REVIEW_PROMPT_TEXT = (
    "Оплата прошла успешно, спасибо! 🎉 "
    "Если вам понравился сервис, будем рады вашему отзыву — он помогает нам становиться лучше.💙"
)

REVIEW_RATING_TEXT = (
    "Каждый отзыв — от реального покупателя, который уже получил товар, "
    "и для меня это очень ценно 💛\n\n"
    "Пожалуйста, перед тем как написать отзыв, оцените качество сервиса "
    "от 1 до 10. Просто отправьте число — это поможет мне стать лучше! 🙏"
)

REVIEW_WRITE_TEXT = (
    "Вы можете написать о нашем сервисе всё, что думаете — даже если "
    "хочется позлиться, мы готовы выслушать 🤬 В любом случае каждое "
    "сообщение помогает нам стать лучше, и мы благодарны за любую обратную связь! 💬"
)

BALANCE_INSUFFICIENT_TEXT = (
    "Сейчас на балансе недостаточно средств для отправки товара. "
    "Вы можете дождаться обновления на следующий день или обратиться в поддержку — "
    "мы отправим товар на ваш аккаунт вручную."
)

PRODUCT_UNAVAILABLE_TEXT = (
    "Сейчас доступны к покупке пакеты UC для PUBG Mobile: 60, 120, 180 и 240 UC. "
    "Ограничение связано с временными сложностями у поставщика.\n\n"
    "Как только ассортимент обновится — мы сразу сообщим в нашей Telegram-группе. 😊"
)

MENU_KW = {"меню"}
BUY_KW = {"купить", "закупиться", "товар"}
PUBG_KW = {"пабг", "пабджи", "pubg"}
UC_KW = {"юси", "uc"}
SUPPORT_KW = {
    "поддержка", "связь", "менеджер", "подержка",
    "проблема", "написать в поддержку",
}


# ─── Файл для сохранения ожидающих платежей ───
PENDING_FILE = os.getenv("PENDING_FILE", "pending_payments.json")
PAYMENT_TIMEOUT = 600
STARTUP_LOAD_WINDOW = 900

pending_payments: dict[str, dict] = {}


def save_pending_to_file():
    try:
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(pending_payments, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Не удалось сохранить pending_payments в файл: {e}")


def load_pending_from_file():
    global pending_payments
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        now = time.time()
        loaded = {}
        for order_id, info in data.items():
            created_at = info.get("created_at", 0)
            if now - created_at < STARTUP_LOAD_WINDOW:
                loaded[order_id] = info
            else:
                logger.info(f"Платёж {order_id} старше {STARTUP_LOAD_WINDOW} сек — пропущен при загрузке")
        pending_payments = loaded
        logger.info(f"Загружено {len(loaded)} ожидающих платежей из файла {PENDING_FILE}")
    except FileNotFoundError:
        logger.info(f"Файл {PENDING_FILE} не найден — стартуем с пустым списком")
    except Exception as e:
        logger.error(f"Не удалось загрузить pending_payments из файла: {e}")


# ─── БАЛАНС: сохранение и загрузка ───
def save_balance():
    try:
        with open(BALANCE_FILE, "w", encoding="utf-8") as f:
            json.dump({"balance": kotshop_balance}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Не удалось сохранить баланс в файл: {e}")


def load_balance():
    global kotshop_balance
    try:
        with open(BALANCE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        kotshop_balance = data.get("balance")
        logger.info(f"Баланс загружен из файла: {kotshop_balance}")
    except FileNotFoundError:
        logger.info(f"Файл баланса {BALANCE_FILE} не найден — стартуем без лимита")
    except Exception as e:
        logger.error(f"Не удалось загрузить баланс из файла: {e}")


# ─── Кэшированные клавиатуры ───
_kb_start:          object = None
_kb_menu:           object = None
_kb_buy:            object = None
_kb_pubg:           object = None
_kb_pubg_products:  object = None
_kb_pubg_other:     object = None
_kb_back_to_menu:   object = None
_kb_policy:         object = None
_kb_support:        object = None
_kb_review:         object = None
_kb_review_rating:  object = None
_kb_review_confirm: object = None


def init_keyboards():
    """Собирает все статические клавиатуры один раз при старте бота."""
    global _kb_start, _kb_menu, _kb_buy, _kb_pubg, _kb_pubg_products
    global _kb_pubg_other, _kb_back_to_menu, _kb_policy, _kb_support
    global _kb_review, _kb_review_rating, _kb_review_confirm

    # ── kb_start ──
    b = InlineKeyboardBuilder()
    b.button(text="Меню", callback_data="menu")
    b.button(text="Политика компании", callback_data="oferta")
    b.adjust(2)
    _kb_start = b.as_markup()

    # ── kb_menu ──
    b = InlineKeyboard
