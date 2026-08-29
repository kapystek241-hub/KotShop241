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
REVIEW_CHAT_ID = os.getenv("REVIEW_CHAT_ID", "")

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

# ─── НОВОЕ: глобальный HTTP-сессия для переиспользования соединений ───
http_session: aiohttp.ClientSession | None = None


async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            connector=aiohttp.TCPConnector(limit=20, limit_per_host=10),
        )
    return http_session


# ─── ИЗМЕНЕНО: Каталог товаров (+1% к цене) ───
PRICE_MARKUP = 1.01  # накрутка 1%

def _apply_markup(price: int) -> int:
    """Применяет 1% наценки и округляет вверх до целого рубля."""
    return math.ceil(price * PRICE_MARKUP)

PRODUCTS = {
    "60uc":   {"name": "60 UC",   "price": _apply_markup(82),   "amount_kopecks": _apply_markup(82)   * 100, "deliveries": ["60_uc"]},
    "120uc":  {"name": "120 UC",  "price": _apply_markup(164),  "amount_kopecks": _apply_markup(164)  * 100, "deliveries": ["60_uc", "60_uc"]},
    "180uc":  {"name": "180 UC",  "price": _apply_markup(246),  "amount_kopecks": _apply_markup(246)  * 100, "deliveries": ["60_uc", "60_uc", "60_uc"]},
    "240uc":  {"name": "240 UC",  "price": _apply_markup(328),  "amount_kopecks": _apply_markup(328)  * 100, "deliveries": ["60_uc", "60_uc", "60_uc", "60_uc"]},
    "325uc":  {"name": "325 UC",  "price": _apply_markup(410),  "amount_kopecks": _apply_markup(410)  * 100, "deliveries": ["325_uc"]},
    "385uc":  {"name": "385 UC",  "price": _apply_markup(502),  "amount_kopecks": _apply_markup(502)  * 100, "deliveries": ["325_uc", "60_uc"]},
    "445uc":  {"name": "445 UC",  "price": _apply_markup(575),  "amount_kopecks": _apply_markup(575)  * 100, "deliveries": ["60_uc", "60_uc", "325_uc"]},
    "660uc":  {"name": "660 UC",  "price": _apply_markup(819),  "amount_kopecks": _apply_markup(819)  * 100, "deliveries": ["660_uc"]},
    "720uc":  {"name": "720 UC",  "price": _apply_markup(902),  "amount_kopecks": _apply_markup(902)  * 100, "deliveries": ["660_uc", "60_uc"]},
    "985uc":  {"name": "985 UC",  "price": _apply_markup(1230), "amount_kopecks": _apply_markup(1230) * 100, "deliveries": ["660_uc", "325_uc"]},
    "1320uc": {"name": "1320 UC", "price": _apply_markup(1639), "amount_kopecks": _apply_markup(1639) * 100, "deliveries": ["660_uc", "660_uc"]},
    "1800uc": {"name": "1800 UC", "price": _apply_markup(2049), "amount_kopecks": _apply_markup(2049) * 100, "deliveries": ["1800_uc"]},
    "1920uc": {"name": "1920 UC", "price": _apply_markup(2214), "amount_kopecks": _apply_markup(2214) * 100, "deliveries": ["1800_uc", "60_uc", "60_uc"]},
    "2125uc": {"name": "2125 UC", "price": _apply_markup(2479), "amount_kopecks": _apply_markup(2479) * 100, "deliveries": ["1800_uc", "325_uc"]},
    "2460uc": {"name": "2460 UC", "price": _apply_markup(2870), "amount_kopecks": _apply_markup(2870) * 100, "deliveries": ["1800_uc", "660_uc"]},
    "3850uc": {"name": "3850 UC", "price": _apply_markup(4119), "amount_kopecks": _apply_markup(4119) * 100, "deliveries": ["3850_uc"]},
    "4510uc": {"name": "4510 UC", "price": _apply_markup(4979), "amount_kopecks": _apply_markup(4979) * 100, "deliveries": ["3850_uc", "660_uc"]},
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
    "нормативно‑правовые акты, регулирующие оборот цифровых товаров и проведение платежей.\n\n"
    "Реализация товаров осуществляется исключительно в рамках заключённого публичного "
    "договора‑оферты, размещённого на официальном ресурсе магазина. Приобретение игровой "
    "валюты, игр и иных виртуальных товаров подтверждает согласие покупателя с условиями "
    "оферты и правилами работы магазина.\n\n"
    "Любые действия, направленные на нарушение установленных правил, в том числе попытки "
    "неправомерного получения выгоды, обхода платёжных механизмов, использования "
    "мошеннических схем либо иного злоупотребления условиями предоставления услуг, "
    "расцениваются как существенное нарушение договорных обязательств и могут "
    "квалифицироваться как противоправные деяния. Такие действия могут служить основанием "
    "для обращения в правоохранительные органы, а собранные материалы — быть использованы "
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
    "сообщение помогает нам стать лучше, и мы благодарны за любую "
    "обратную связь! 💬"
)

# ─── БАЛАНС: сообщение при нехватке средств ───
BALANCE_INSUFFICIENT_TEXT = (
    "Сейчас на балансе недостаточно средств для отправки товара. "
    "Вы можете дождаться обновления на следующий день или обратиться в поддержку — "
    "мы отправим товар на ваш аккаунт вручную."
)

# ─── НОВОЕ: наборы ключевых слов для текстовых команд ───
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


# ─── ИЗМЕНЕНО: Кэшированные клавиатуры ───
# Статические клавиатуры собираются один раз при старте и переиспользуются.

_kb_start:       object = None
_kb_menu:        object = None
_kb_buy:         object = None
_kb_pubg:        object = None
_kb_pubg_products: object = None
_kb_pubg_other:  object = None
_kb_back_to_menu: object = None
_kb_policy:      object = None
_kb_support:     object = None
_kb_review:      object = None
_kb_review_rating: object = None
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
    b = InlineKeyboardBuilder()
    b.button(text="Купить", callback_data="buy")
    b.button(text="Поддержка", callback_data="support")
    b.button(text="Турнир", callback_data="tournament")
    b.button(text="Назад", callback_data="back_start")
    b.adjust(2, 1, 1)
    _kb_menu = b.as_markup()

    # ── kb_buy ──
    b = InlineKeyboardBuilder()
    b.button(text="PUBG Mobile", callback_data="pubg")
    b.button(text="Назад", callback_data="back_menu")
    b.adjust(1)
    _kb_buy = b.as_markup()

    # ── kb_pubg ──
    b = InlineKeyboardBuilder()
    b.button(text="Купить UC по ID", callback_data="pubg_buy_uc")
    b.button(text="Другие товары", callback_data="pubg_other")
    b.button(text="Назад", callback_data="back_buy")
    b.adjust(1)
    _kb_pubg = b.as_markup()

    # ── kb_pubg_products ──
    b = InlineKeyboardBuilder()
    for key in PRODUCT_GRID:
        product = PRODUCTS[key]
        b.button(text=f"UC {product['name'].split(' ')[0]} — {product['price']}₽", callback_data=f"pubg_prod:{key}")
    b.button(text="Назад", callback_data="back_pubg")
    b.adjust(2, 2, 2, 2, 2, 2, 2, 2, 1, 1)
    _kb_pubg_products = b.as_markup()

    # ── kb_pubg_other ──
    b = InlineKeyboardBuilder()
    b.button(text="Назад", callback_data="back_pubg")
    b.adjust(1)
    _kb_pubg_other = b.as_markup()

    # ── kb_back_to_menu ──
    b = InlineKeyboardBuilder()
    b.button(text="Назад", callback_data="back_menu")
    b.adjust(1)
    _kb_back_to_menu = b.as_markup()

    # ── kb_policy ──
    b = InlineKeyboardBuilder()
    b.button(text="Меню", callback_data="menu")
    b.adjust(1)
    _kb_policy = b.as_markup()

    # ── kb_support ──
    b = InlineKeyboardBuilder()
    b.button(text="Поддержка", url="https://t.me/KotShop2415")
    b.button(text="Назад", callback_data="back_menu")
    b.adjust(1)
    _kb_support = b.as_markup()

    # ── kb_review ──
    b = InlineKeyboardBuilder()
    b.button(text="Оценить", callback_data="review_start")
    b.button(text="В меню", callback_data="menu")
    b.adjust(1)
    _kb_review = b.as_markup()

    # ── kb_review_rating ──
    b = InlineKeyboardBuilder()
    b.button(text="Написать отзыв", callback_data="review_write")
    b.button(text="Отправить без текста", callback_data="review_send_stars_only")
    b.button(text="Отменить", callback_data="review_cancel")
    b.adjust(1)
    _kb_review_rating = b.as_markup()

    # ── kb_review_confirm ──
    b = InlineKeyboardBuilder()
    b.button(text="Отправить", callback_data="review_send")
    b.button(text="Изменить текст", callback_data="review_edit_text")
    b.button(text="Поменять оценку", callback_data="review_change_rating")
    b.button(text="В меню", callback_data="review_to_menu")
    b.adjust(1)
    _kb_review_confirm = b.as_markup()

    logger.info("Все статические клавиатуры собраны и закешированы")


# kb_confirm — единственная динамическая клавиатура, строится на лету
def kb_confirm(game_id: str, product_key: str):
    b = InlineKeyboardBuilder()
    b.button(text="Все верно", callback_data=f"confirm_yes:{game_id}:{product_key}")
    b.button(text="Неверный ID", callback_data=f"confirm_noid:{product_key}")
    b.button(text="Я передумал", callback_data="confirm_cancel")
    b.adjust(1)
    return b.as_markup()


# ─── НОВОЕ: Запрос статуса платежа к VPS (через общий HTTP-сессия) ───
async def vps_check_payment(order_id: str) -> bool | None:
    try:
        session = await get_http_session()
        async with session.post(
            f"{VPS_API_URL}/check-payment",
            json={"secret": API_SECRET, "order_id": order_id},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"VPS /check-payment вернул HTTP {resp.status}: {text[:200]}")
                return None
            data = await resp.json()
            return bool(data.get("paid", False))
    except Exception as e:
        logger.error(f"Ошибка запроса статуса платежа {order_id} к VPS: {e}")
        return None


# ─── НОВОЕ: Отправка заявки на доставку UC (через общий HTTP-сессия) ───
async def vps_deliver(game_id: str, user_id: int, order_id: str, deliver_index: int, offer_id: str) -> bool:
    try:
        session = await get_http_session()
        async with session.post(
            f"{VPS_API_URL}/deliver",
            json={
                "secret": API_SECRET,
                "game_id": game_id,
                "user_id": user_id,
                "order_id": order_id,
                "deliver_index": deliver_index,
                "offer_id": offer_id,
            },
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"VPS /deliver #{deliver_index} ({offer_id}) вернул HTTP {resp.status}: {text[:200]}")
                return False
            data = await resp.json()
            return bool(data.get("success", False))
    except Exception as e:
        logger.error(f"Ошибка доставки UC (заявка #{deliver_index}, offer={offer_id}): {e}")
        return False


# ─── Отправка отзыва в группу ───
async def send_review_to_group(text: str):
    if not REVIEW_CHAT_ID:
        logger.warning("REVIEW_CHAT_ID не задан — отзыв не отправлен в группу")
        return
    try:
        await bot.send_message(REVIEW_CHAT_ID, text)
        logger.info("Отзыв отправлен в группу отзывов")
    except Exception as e:
        logger.error(f"Не удалось отправить отзыв в группу: {e}")


# ─── НОВОЕ: обработка одного оплаченного заказа (доставка + уведомление) ───
async def process_paid_order(order_id: str, info: dict):
    """Доставка и уведомление по одному подтверждённому заказу — работает конкурентно."""
    deliveries = info.get("deliveries", ["60_uc"])
    game_id = info.get("game_id", "")
    delivery_user_id = info.get("user_id", 0)

    # Параллельная доставка всех частей заказа
    tasks = [
        vps_deliver(game_id, delivery_user_id, order_id, i + 1, offer_id)
        for i, offer_id in enumerate(deliveries)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Доставка {i+1}/{len(deliveries)} для {order_id} — ошибка: {result}")
        elif result:
            logger.info(f"Доставка {i+1}/{len(deliveries)} для {order_id} — успешно")
        else:
            logger.error(f"Доставка {i+1}/{len(deliveries)} для {order_id} — НЕ удалась")

    # Уведомление пользователя
    notify_msg = await bot.send_message(info["chat_id"], "Оплата выполнена")
    await asyncio.sleep(2)
    try:
        await notify_msg.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение «Оплата выполнена»: {e}")

    await asyncio.sleep(1)
    try:
        await bot.delete_message(info["chat_id"], info["message_id"])
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение с ссылкой на оплату: {e}")

    await bot.send_message(
        info["chat_id"],
        REVIEW_PROMPT_TEXT,
        reply_markup=_kb_review,
    )


# ─── НОВОЕ: фоновая задача — конкурентная проверка и обработка платежей ───
async def check_payments_loop():
    logger.info("Запущен цикл проверки платежей (интервал 15 сек, таймаут 600 сек)")
    while True:
        await asyncio.sleep(15)
        now = time.time()
        to_remove = []
        to_check = []

        # 1) Разделаем на истёкшие и активные
        for order_id, info in list(pending_payments.items()):
            if now - info["created_at"] > PAYMENT_TIMEOUT:
                logger.info(f"Платёж {order_id} истёк по таймауту (10 мин)")
                await bot.send_message(
                    info["chat_id"],
                    "Похоже, платёж прервался. Ничего страшного — "
                    "просто создайте заказ ещё раз, и сможете оплатить.",
                    reply_markup=_kb_back_to_menu,
                )
                await asyncio.sleep(1)
                try:
                    await bot.delete_message(info["chat_id"], info["message_id"])
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение {info['message_id']}: {e}")
                to_remove.append(order_id)
            else:
                to_check.append((order_id, info))

        # 2) Конкурентная проверка статусов всех активных платежей
        if to_check:
            check_tasks = [vps_check_payment(oid) for oid, _ in to_check]
            results = await asyncio.gather(*check_tasks)

            confirmed = []
            for (order_id, info), paid in zip(to_check, results):
                if paid is None or not paid:
                    continue
                logger.info(f"Платёж {order_id} подтверждён (VPS: paid=true)")

                # Списываем баланс последовательно
                global kotshop_balance
                if kotshop_balance is not None:
                    price = info.get("amount_kopecks", 0) / 100
                    kotshop_balance -= price
                    if kotshop_balance < 0:
                        kotshop_balance = 0
                    save_balance()
                    logger.info(f"Баланс списан на {price}₽, остаток: {kotshop_balance}₽ (заказ {order_id})")

                confirmed.append((order_id, info))
                to_remove.append(order_id)

            # 3) Конкурентная обработка всех подтверждённых заказов
            if confirmed:
                process_tasks = [
                    process_paid_order(oid, info) for oid, info in confirmed
                ]
                await asyncio.gather(*process_tasks, return_exceptions=True)

        # 4) Очистка
        if to_remove:
            for order_id in to_remove:
                pending_payments.pop(order_id, None)
            save_pending_to_file()


# ─── Вспомогательная функция ───
async def answer_and_delete(callback, text, reply_markup=None):
    await callback.message.answer(text, reply_markup=reply_markup)
    await asyncio.sleep(1)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {callback.message.message_id}: {e}")


# ─── Хендлеры ───
@dp.message(Command("start"))
async def cmd_start(message):
    logger.info(f"/start от user_id={message.from_user.id}, username={message.from_user.username}")
    await message.answer(WELCOME_TEXT, reply_markup=_kb_start)


# ─── БАЛАНС: команда установки баланса (KotShopBalans=5000) ───
@dp.message(F.text.startswith("KotShopBalans="), StateFilter(None))
async def cmd_set_balance(message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        value = float(message.text.split("=", 1)[1].strip())
    except (ValueError, IndexError):
        await message.answer("❌ Неверный формат. Пример: `KotShopBalans=5000`")
        return

    if value < 0:
        await message.answer("❌ Баланс не может быть отрицательным.")
        return

    global kotshop_balance
    kotshop_balance = value
    save_balance()
    logger.info(f"Админ {message.from_user.id} установил баланс: {kotshop_balance}₽")
    await message.answer(
        f"✅ Баланс установлен: {kotshop_balance}₽\n"
        f"Пользователи могут покупать товары, пока общая сумма не достигнет этого значения."
    )


# ─── БАЛАНС: команда проверки баланса (KotShopSee) ───
@dp.message(F.text == "KotShopSee", StateFilter(None))
async def cmd_see_balance(message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    if kotshop_balance is None:
        await message.answer("📊 Баланс не задан — лимит отключён (покупки не ограничены).")
    else:
        await message.answer(f"📊 Текущий остаток баланса: {kotshop_balance}₽")


# ─── НОВОЕ: текстовые команды по ключевым словам ───

@dp.message(lambda m: m.text and m.text.lower().strip() in MENU_KW, StateFilter(None))
async def kw_menu(message, state: FSMContext):
    await state.clear()
    await message.answer(MENU_TEXT, reply_markup=_kb_menu)


@dp.message(lambda m: m.text and m.text.lower().strip() in BUY_KW, StateFilter(None))
async def kw_buy(message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Выберите нужную игру или напишите название игры для получения раздела покупки",
        reply_markup=_kb_buy,
    )


@dp.message(lambda m: m.text and m.text.lower().strip() in PUBG_KW, StateFilter(None))
async def kw_pubg(message, state: FSMContext):
    await state.clear()
    await message.answer("Выберите нужный раздел", reply_markup=_kb_pubg)


@dp.message(lambda m: m.text and m.text.lower().strip() in UC_KW, StateFilter(None))
async def kw_uc(message, state: FSMContext):
    await state.clear()
    await message.answer("Выберите количество UC", reply_markup=_kb_pubg_products)


@dp.message(lambda m: m.text and m.text.lower().strip() in SUPPORT_KW, StateFilter(None))
async def kw_support(message, state: FSMContext):
    await state.clear()
    await message.answer(SUPPORT_TEXT, reply_markup=_kb_support)


@dp.message(F.text == "тест", StateFilter(None))
async def cmd_test_purchase(message, state: FSMContext):
    logger.info(f"ТЕСТ: имитация успешной оплаты от user_id={message.from_user.id}")
    await state.clear()

    notify_msg = await message.answer("Оплата выполнена")
    await asyncio.sleep(2)
    try:
        await notify_msg.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить тестовое сообщение «Оплата выполнена»: {e}")

    await message.answer(REVIEW_PROMPT_TEXT, reply_markup=_kb_review)


@dp.message(F.text == "Отзыв", StateFilter(None))
async def cmd_review(message, state: FSMContext):
    await state.clear()
    await message.answer(REVIEW_PROMPT_TEXT, reply_markup=_kb_review)


@dp.callback_query(F.data == "menu")
async def cb_menu(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(callback, MENU_TEXT, _kb_menu)
    await callback.answer()


@dp.callback_query(F.data == "oferta")
async def cb_oferta(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(callback, POLICY_TEXT, _kb_policy)
    await callback.answer()


@dp.callback_query(F.data == "back_start")
async def cb_back_start(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(callback, WELCOME_TEXT, _kb_start)
    await callback.answer()


@dp.callback_query(F.data == "buy")
async def cb_buy(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(
        callback,
        "Выберите нужную игру или напишите название игры для получения раздела покупки",
        _kb_buy,
    )
    await callback.answer()


@dp.callback_query(F.data == "support")
async def cb_support(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(callback, SUPPORT_TEXT, _kb_support)
    await callback.answer()


@dp.callback_query(F.data == "tournament")
async def cb_tournament(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(callback, "🏆 Турнирный раздел в разработке", _kb_menu)
    await callback.answer()


@dp.callback_query(F.data == "back_menu")
async def cb_back_menu(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(callback, MENU_TEXT, _kb_menu)
    await callback.answer()


# ── PUBG Mobile ──
@dp.callback_query(F.data == "pubg")
async def cb_pubg(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(callback, "Выберите нужный раздел", _kb_pubg)
    await callback.answer()


@dp.callback_query(F.data == "back_buy")
async def cb_back_buy(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(
        callback,
        "Выберите нужную игру или напишите название игры для получения раздела покупки",
        _kb_buy,
    )
    await callback.answer()


@dp.callback_query(F.data == "pubg_buy_uc")
async def cb_pubg_buy_uc(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(callback, "Выберите количество UC", _kb_pubg_products)
    await callback.answer()


@dp.callback_query(F.data == "pubg_other")
async def cb_pubg_other(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(
        callback,
        "На данный момент этот раздел в разработке",
        _kb_pubg_other,
    )
    await callback.answer()


@dp.callback_query(F.data == "back_pubg")
async def cb_back_pubg(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(callback, "Выберите нужный раздел", _kb_pubg)
    await callback.answer()


# ── Универсальный обработчик выбора товара ──
@dp.callback_query(F.data.startswith("pubg_prod:"))
async def cb_pubg_product(callback, state: FSMContext):
    product_key = callback.data.split(":")[1]
    if product_key not in PRODUCTS:
        logger.warning(f"Неизвестный товар: {product_key}")
        await callback.answer("Товар не найден")
        return

    await state.set_state(OrderFlow.waiting_for_id)
    await state.set_data({"product": product_key})
    await callback.message.answer("Укажите ваш ID который должен начинаться на 5")
    await asyncio.sleep(1)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {callback.message.message_id}: {e}")
    await callback.answer()


@dp.message(OrderFlow.waiting_for_id)
async def process_game_id(message, state: FSMContext):
    game_id = message.text.strip()

    if not game_id.isdigit() or not game_id.startswith("5"):
        logger.warning(f"Неверный game_id от user_id={message.from_user.id}: '{game_id}'")
        await message.answer(
            "❌ ID должен состоять только из цифр и начинаться на 5. Попробуйте ещё раз."
        )
        return

    data = await state.get_data()
    product_key = data.get("product", "60uc")
    product = PRODUCTS.get(product_key, PRODUCTS["60uc"])

    await state.clear()
    logger.info(f"Получен game_id={game_id} от user_id={message.from_user.id}, product={product_key}")
    await message.answer(
        f"Вы выбрали товар {product['name']} стоимостью в {product['price']}₽\n"
        f"Ваш ID: {game_id}",
        reply_markup=kb_confirm(game_id, product_key)
    )


# ── Подтверждение заказа → создание ОДНОГО платежа ──
@dp.callback_query(F.data.startswith("confirm_yes"))
async def cb_confirm_yes(callback, state: FSMContext):
    await state.clear()
    parts = callback.data.split(":")
    game_id = parts[1]
    product_key = parts[2] if len(parts) > 2 else "60uc"
    product = PRODUCTS.get(product_key, PRODUCTS["60uc"])
    user_id = callback.from_user.id
    amount_kopecks = product["amount_kopecks"]
    total_price = product["price"]
    deliveries = product["deliveries"]

    # ─── БАЛАНС: проверка остатка перед созданием платежа ───
    if kotshop_balance is not None and total_price > kotshop_balance:
        logger.info(
            f"Отказ в покупке user_id={user_id}: товар {total_price}₽ > баланс {kotshop_balance}₽"
        )
        await callback.message.answer(BALANCE_INSUFFICIENT_TEXT)
        await asyncio.sleep(1)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()
        return

    logger.info(
        f"Создание платежа: user_id={user_id}, game_id={game_id}, "
        f"product={product_key}, amount={amount_kopecks} коп., deliveries={deliveries}"
    )

    order_id = f"order-{user_id}-{int(time.time())}"
    logger.info(f"Создание заказа: order_id={order_id}")

    try:
        session = await get_http_session()
        async with session.post(
            f"{VPS_API_URL}/create-payment",
            json={
                "secret": API_SECRET,
                "order_id": order_id,
                "amount_kopecks": amount_kopecks,
                "game_id": game_id,
                "user_id": user_id,
                "description": f"Покупка {product['name']} для PUBG Mobile. Игровой ID: {game_id}",
                "email": "noreply@kotshop241.ru",
            },
        ) as resp:
            logger.info(f"Ответ бэкенда: HTTP {resp.status}")
            try:
                data = await resp.json()
            except Exception as e:
                raw_text = await resp.text()
                logger.error(f"Не удалось распарсить JSON от бэкенда: {e}")
                logger.error(f"Сырой ответ: {raw_text[:500]}")
                await callback.message.answer("Сервер вернул некорректный ответ. Попробуйте позже.")
                await asyncio.sleep(1)
                try:
                    await callback.message.delete()
                except Exception:
                    pass
                await callback.answer()
                return

        logger.info(f"Тело ответа бэкенда: {data}")

        if not data.get("success"):
            err = data.get("error", "неизвестная ошибка")
            logger.error(f"Бэкенд отклонил платёж: {data}")
            await callback.message.answer(
                f"❌ Не удалось создать платёж: {err}\n\n"
                f"Попробуйте позже или обратитесь в поддержку: @kotshop241_support"
            )
            await asyncio.sleep(1)
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.answer()
            return

    except aiohttp.ClientConnectorError as e:
        logger.error(f"Не удалось подключиться к VPS-бэкенду: {e}")
        logger.error(f"Проверьте VPS_API_URL={VPS_API_URL} и открыт ли порт 8080 на VPS")
        await callback.message.answer(
            "❌ Не удалось подключиться к серверу оплаты.\n"
            "Проверьте, что VPS запущен и порт 8080 открыт.\n\n"
            "Поддержка: @kotshop241_support"
        )
        await asyncio.sleep(1)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()
        return

    except asyncio.TimeoutError:
        logger.error(f"Таймаут при запросе к VPS-бэкенду (15 сек). URL: {VPS_API_URL}")
        await callback.message.answer("❌ Сервер оплаты не ответил вовремя. Попробуйте позже.")
        await asyncio.sleep(1)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()
        return

    except Exception as e:
        logger.error(f"Непредвиденная ошибка при создании платежа: {e}")
        logger.error(traceback.format_exc())
        await callback.message.answer(
            "❌ Произошла ошибка. Попробуйте позже или обратитесь в поддержку: @kotshop241_support"
        )
        await asyncio.sleep(1)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()
        return

    b = InlineKeyboardBuilder()
    b.button(text=f"Оплатить {total_price}₽", url=data["payment_url"])
    b.adjust(1)

    payment_text = (
        f"Заказ #{order_id}\n"
        f"Товар: {product['name']}\n"
        f"Ваш ID: {game_id}\n"
        f"Сумма: {total_price}₽\n\n"
        f"Нажмите «Оплатить», чтобы завершить покупку."
    )

    payment_msg = await callback.message.answer(payment_text, reply_markup=b.as_markup())
    await asyncio.sleep(1)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {callback.message.message_id}: {e}")

    pending_payments[order_id] = {
        "user_id": user_id,
        "chat_id": callback.message.chat.id,
        "message_id": payment_msg.message_id,
        "game_id": game_id,
        "product_name": product["name"],
        "payment_id": data.get("payment_id", ""),
        "amount_kopecks": amount_kopecks,
        "created_at": time.time(),
        "payment_url": data["payment_url"],
        "deliveries": deliveries,
    }
    save_pending_to_file()
    logger.info(f"Заказ {order_id} добавлен в очередь мониторинга (deliveries={deliveries})")

    await callback.answer()


@dp.callback_query(F.data.startswith("confirm_noid"))
async def cb_confirm_noid(callback, state: FSMContext):
    parts = callback.data.split(":")
    product_key = parts[1] if len(parts) > 1 else "60uc"
    await state.set_state(OrderFlow.waiting_for_id)
    await state.set_data({"product": product_key})
    await callback.message.answer("Укажите ваш ID который должен начинаться на 5")
    await asyncio.sleep(1)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {callback.message.message_id}: {e}")
    await callback.answer()


@dp.callback_query(F.data == "confirm_cancel")
async def cb_confirm_cancel(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(callback, "Выберите количество UC", _kb_pubg_products)
    await callback.answer()


# ── Раздел отзывов ──
@dp.callback_query(F.data == "review_start")
async def cb_review_start(callback, state: FSMContext):
    await state.set_state(OrderFlow.waiting_for_rating)
    await callback.message.answer(REVIEW_RATING_TEXT)
    await asyncio.sleep(1)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {callback.message.message_id}: {e}")
    await callback.answer()


@dp.message(OrderFlow.waiting_for_rating)
async def process_rating(message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 10):
        await message.answer("❌ Пожалуйста, отправьте число от 1 до 10.")
        return

    rating = int(text)
    stars = "⭐" * rating
    await state.set_state(None)
    await state.set_data({"rating": rating})
    await message.answer(
        f"{stars}\n\nЗдесь будет ваш отзыв, напишите его",
        reply_markup=_kb_review_rating
    )


@dp.callback_query(F.data == "review_write")
async def cb_review_write(callback, state: FSMContext):
    await state.set_state(OrderFlow.waiting_for_review_text)
    await callback.message.answer(REVIEW_WRITE_TEXT)
    await asyncio.sleep(1)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {callback.message.message_id}: {e}")
    await callback.answer()


@dp.message(OrderFlow.waiting_for_review_text)
async def process_review_text(message, state: FSMContext):
    data = await state.get_data()
    rating = data.get("rating", 5)
    stars = "⭐" * rating
    review_text = message.text.strip()

    await state.set_state(None)
    await state.set_data({"rating": rating, "review_text": review_text})
    await message.answer(
        f"{stars}\n\n{review_text}",
        reply_markup=_kb_review_confirm
    )


@dp.callback_query(F.data == "review_send")
async def cb_review_send(callback, state: FSMContext):
    data = await state.get_data()
    rating = data.get("rating", 5)
    review_text = data.get("review_text", "")
    stars = "⭐" * rating

    full_review = f"{stars}\n\n{review_text}"
    await send_review_to_group(full_review)

    await state.clear()
    await callback.message.answer("Спасибо за ваш отзыв! 💙", reply_markup=_kb_menu)
    await asyncio.sleep(1)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {callback.message.message_id}: {e}")
    await callback.answer()


@dp.callback_query(F.data == "review_edit_text")
async def cb_review_edit_text(callback, state: FSMContext):
    data = await state.get_data()
    rating = data.get("rating", 5)
    await state.set_state(OrderFlow.waiting_for_review_text)
    await state.set_data({"rating": rating})
    await callback.message.answer(REVIEW_WRITE_TEXT)
    await asyncio.sleep(1)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {callback.message.message_id}: {e}")
    await callback.answer()


@dp.callback_query(F.data == "review_change_rating")
async def cb_review_change_rating(callback, state: FSMContext):
    data = await state.get_data()
    review_text = data.get("review_text", "")
    await state.set_state(OrderFlow.waiting_for_rating)
    await state.set_data({"review_text": review_text})
    await callback.message.answer(REVIEW_RATING_TEXT)
    await asyncio.sleep(1)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {callback.message.message_id}: {e}")
    await callback.answer()


@dp.callback_query(F.data == "review_to_menu")
async def cb_review_to_menu(callback, state: FSMContext):
    await state.clear()
    await callback.message.answer(MENU_TEXT, reply_markup=_kb_menu)
    await asyncio.sleep(1)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {callback.message.message_id}: {e}")
    await callback.answer()


@dp.callback_query(F.data == "review_send_stars_only")
async def cb_review_send_stars_only(callback, state: FSMContext):
    data = await state.get_data()
    rating = data.get("rating", 5)
    stars = "⭐" * rating

    await send_review_to_group(stars)
    await state.clear()
    await callback.message.answer("Спасибо за вашу оценку! 💙", reply_markup=_kb_menu)
    await asyncio.sleep(1)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {callback.message.message_id}: {e}")
    await callback.answer()


@dp.callback_query(F.data == "review_cancel")
async def cb_review_cancel(callback, state: FSMContext):
    await state.clear()
    await answer_and_delete(callback, MENU_TEXT, _kb_menu)
    await callback.answer()


# ─── Глобальный обработчик ошибок ───
@dp.error()
async def on_error(event, exception, *args, **kwargs):
    logger.error(f"Необработанная ошибка в хендлере: {exception}")
    logger.error(traceback.format_exc())
    return True


# ─── Main ───
async def main():
    logger.info("BotHost бот запущен")
    logger.info(f"VPS_API_URL = {VPS_API_URL}")
    logger.info(f"REVIEW_CHAT_ID = {REVIEW_CHAT_ID if REVIEW_CHAT_ID else '(не задан)'}")

    # ─── ИЗМЕНЕНО: инициализация клавиатур до старта поллинга ───
    init_keyboards()

    load_pending_from_file()
    load_balance()

    asyncio.create_task(check_payments_loop())
    await dp.start_polling(bot)

    # ─── НОВОЕ: закрываем HTTP-сессию при остановке ───
    if http_session and not http_session.closed:
        await http_session.close()


if __name__ == "__main__":
    asyncio.run(main())
