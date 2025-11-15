import json
import os
import logging
from typing import Dict, Any
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import time
import asyncio
from datetime import datetime, timedelta
import aiohttp
import httpx

# Глобальные переменные
CANCEL_PROCESS = {}
PHOTO_PROCESS = {}
USER_DAILY_LIMITS = {}
USER_VERIFIED = {}
USER_TOKENS = {}
# Глобальные переменные для контроля процессов
ACTIVE_PROCESSES = {}  # Храним активные задачи
CANCEL_FLAGS = {}      # Флаги отмены для каждого пользователя

# Добавьте в начало файла после других глобальных переменных
VERIFIED_USERS_FILE = 'verified_users.json'

# ========== ОПТИМИЗИРОВАННЫЙ МОДУЛЬ НАКРУТКИ ФОТОГРАФИЙ ==========
MAX_CONCURRENT_UPLOADS = 2
UPLOAD_TIMEOUT = 30
MAX_RETRIES = 2
RETRY_DELAY = 1
# 🔧 ДОБАВИТЬ ЭТИ ПЕРЕМЕННЫЕ
PROCESS_LOCK = asyncio.Lock()
ACTIVE_USER_PROCESSES = {}  # Отслеживание активных процессов по пользователям
# 🔧 ЗАМЕНИТЬ глобальные переменные
USER_PROCESS_STATES = {}  # Вместо MESSAGE_PROCESSING и ACTIVE_USER_PROCESSES

# 🔧 ДОБАВЬТЕ ЭТО В НАЧАЛО ФАЙЛА (после других глобальных переменных)
PROCESS_LOCK = asyncio.Lock()  # Глобальный мьютекс для процессов
MESSAGE_TIMESTAMPS = {}  # Таймстампы сообщений для защиты от дублирования
# 🔧 ДОБАВЬТЕ В ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
MESSAGE_PROCESSING = {}  # Флаг обработки сообщений для каждого пользователя

# ========== МОДУЛЬ ПОДПИСКИ ==========

# Глобальные переменные для подписок
USER_SUBSCRIPTIONS = {}
ADMIN_USERS = set()

# Настройки подписки
SUBSCRIPTION_PRICE = "99 руб/месяц"  # Для отображения
SUBSCRIPTION_DAYS = 30  # Длительность подписки

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.ERROR
)
logger = logging.getLogger(__name__)

# Файл для хранения токенов
TOKENS_FILE = 'tokens.json'
def load_tokens() -> Dict[str, str]:
    """Загрузка токенов из файла"""
    try:
        with open(TOKENS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}

def save_tokens(tokens: Dict[str, str]):
    """Сохранение токенов в файл"""
    try:
        with open(TOKENS_FILE, 'w', encoding='utf-8') as f:
            json.dump(tokens, f, ensure_ascii=False, indent=2)
        logger.info("Токены успешно сохранены в файл")
    except Exception as e:
        logger.error(f"Ошибка при сохранении токенов: {e}")

def get_back_button():
    """Кнопка 'Назад в меню'"""
    return InlineKeyboardMarkup([[InlineKeyboardButton("Назад в меню", callback_data="menu")]])

def get_photo_cancel_button():
    """Кнопка отмены процесса загрузки фото"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 НЕМЕДЛЕННАЯ ОСТАНОВКА", callback_data="universal_cancel")]
    ])

def get_cancel_button():
    """Кнопка отмены процесса накрутки сообщений"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 НЕМЕДЛЕННАЯ ОСТАНОВКА", callback_data="universal_cancel")]
    ])

def get_captcha_button():
    """Кнопка капчи"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Я не робот", callback_data="captcha_verify")]
    ])

def load_subscriptions():
    """Загрузка подписок из файла"""
    global USER_SUBSCRIPTIONS, ADMIN_USERS
    try:
        with open('subscriptions.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            USER_SUBSCRIPTIONS = data.get('subscriptions', {})
            ADMIN_USERS = set(data.get('admins', []))
    except FileNotFoundError:
        save_subscriptions()

def save_subscriptions():
    """Сохранение подписок в файл"""
    try:
        with open('subscriptions.json', 'w', encoding='utf-8') as f:
            json.dump({
                'subscriptions': USER_SUBSCRIPTIONS,
                'admins': list(ADMIN_USERS)
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения подписок: {e}")

def is_user_subscribed(user_id: str) -> bool:
    """Проверка подписки пользователя"""
    # Админы всегда имеют доступ
    if user_id in ADMIN_USERS:
        return True
    
    if user_id in USER_SUBSCRIPTIONS:
        subscription_end = datetime.fromisoformat(USER_SUBSCRIPTIONS[user_id])
        return datetime.now() < subscription_end
    return False

def get_subscription_status(user_id: str) -> str:
    """Получить статус подписки"""
    if user_id in ADMIN_USERS:
        return "👑 Администратор"
    
    if is_user_subscribed(user_id):
        return "✅ Активна"
    return "❌ Отсутствует"

def add_subscription(user_id: str, days: int = SUBSCRIPTION_DAYS):
    """Добавить подписку пользователю"""
    end_date = datetime.now() + timedelta(days=days)
    USER_SUBSCRIPTIONS[user_id] = end_date.isoformat()
    save_subscriptions()

def remove_subscription(user_id: str):
    """Удалить подписку пользователя"""
    if user_id in USER_SUBSCRIPTIONS:
        del USER_SUBSCRIPTIONS[user_id]
        save_subscriptions()

def load_verified_users() -> Dict[str, bool]:
    """Загрузка верифицированных пользователей из файла"""
    try:
        with open(VERIFIED_USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}

def save_verified_users(verified_users: Dict[str, bool]):
    """Сохранение верифицированных пользователей в файл"""
    try:
        with open(VERIFIED_USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(verified_users, f, ensure_ascii=False, indent=2)
        logger.info("Верифицированные пользователи сохранены")
    except Exception as e:
        logger.error(f"Ошибка при сохранении верифицированных пользователей: {e}")

# Загружаем верифицированных пользователей при старте
USER_VERIFIED = load_verified_users()

# 🔧 ДОБАВИТЬ ЭТУ ФУНКЦИЮ
async def reset_user_state(user_id: str, context: ContextTypes.DEFAULT_TYPE = None):
    """Полный сброс состояния пользователя"""
    # Сбрасываем все флаги
    await set_user_processing(user_id, False)
    PHOTO_PROCESS.pop(user_id, None)
    CANCEL_FLAGS.pop(user_id, None)
    
    if context:
        context.user_data.clear()
    
    logger.info(f"🔧 Полный сброс состояния для {user_id}")

async def is_user_processing(user_id: str) -> bool:
    """Проверяет, обрабатывается ли сообщение пользователя"""
    return USER_PROCESS_STATES.get(user_id, False)

async def set_user_processing(user_id: str, state: bool):
    """Устанавливает состояние обработки"""
    USER_PROCESS_STATES[user_id] = state
    # 🔧 АВТООЧИСТКА: автоматически сбрасываем флаг через 30 секунд
    if state:
        asyncio.create_task(auto_reset_processing(user_id))

async def auto_reset_processing(user_id: str):
    """Автоматически сбрасывает флаг обработки через 30 секунд"""
    await asyncio.sleep(30)
    if USER_PROCESS_STATES.get(user_id):
        USER_PROCESS_STATES[user_id] = False
        logger.info(f"🔧 Автосброс флага обработки для {user_id}")

async def is_user_busy(user_id: str) -> bool:
    """Проверяет, занят ли пользователь другим процессом"""
    async with PROCESS_LOCK:
        return user_id in ACTIVE_USER_PROCESSES

async def set_user_busy(user_id: str, process_type: str):
    """Устанавливает флаг занятости пользователя"""
    async with PROCESS_LOCK:
        ACTIVE_USER_PROCESSES[user_id] = process_type

async def set_user_free(user_id: str):
    """Освобождает пользователя"""
    async with PROCESS_LOCK:
        ACTIVE_USER_PROCESSES.pop(user_id, None)

async def show_subscription_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню подписки"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    # Безопасный ответ на запрос
    try:
        await query.answer()
    except:
        pass
    
    subscription_status = get_subscription_status(user_id)
    
    # Красивое оформление меню подписки
    if is_user_subscribed(user_id) or user_id in ADMIN_USERS:
        # Для подписанных пользователей и админов
        message_text = (
            "🌟 <b>ПРЕМИУМ ПОДПИСКА</b>\n\n"
            f"📊 <b>Ваш статус:</b> {subscription_status}\n\n"
            "💫 <b>Ваши преимущества:</b>\n"
            "• 🔵 Накрутка сообщений VK\n"
            "• 📸 Накрутка фотографий VK\n"
            "• 🚀 Приоритетная обработка\n"
            "• ⚡ Максимальная скорость\n"
            "• 🔧 Все функции бота\n\n"
            
            "🎁 <b>Дополнительно:</b>\n"
            "• 📊 Расширенная статистика\n"
            "• 🛡️ Приоритетная поддержка\n"
            "• 🔄 Увеличенные лимиты\n\n"
            
            "💎 <i>Вы наслаждаетесь всеми преимуществами премиум-доступа!</i>"
        )
    else:
        # Для пользователей без подписки
        message_text = (
            "🔒 <b>ПРЕМИУМ ПОДПИСКА</b>\n\n"
            "💫 <b>Откройте все возможности бота!</b>\n\n"
            
            "🚀 <b>Что вы получите:</b>\n"
            "• 🔵 Накрутка непрочитанных сообщений VK\n"
            "• 📸 Массовая загрузка фотографий в альбомы\n"
            "• ⚡ Максимальная скорость работы\n"
            "• 🛡️ Приоритетная поддержка\n"
            "• 📊 Расширенная статистика\n\n"
            
            "💰 <b>Стоимость подписки:</b>\n"
            f"• {SUBSCRIPTION_PRICE}\n\n"
            
            "🎁 <b>Преимущества:</b>\n"
            "• 🔄 Автопродление\n"
            "• 💰 Возврат средств в течение 24 часов\n"
            "• 📱 Доступ с любого устройства\n\n"
            
            "⚡ <b>Ограничения бесплатной версии:</b>\n"
            "• ❌ Функции VK недоступны\n"
            "• ⏳ Очередь обработки\n"
            "• 📉 Базовые возможности\n\n"
            
            "🔑 <i>Приобретите подписку для разблокировки всех функций!</i>"
        )
    
    # Создаем клавиатуру
    keyboard = []
    
    if not is_user_subscribed(user_id) and user_id not in ADMIN_USERS:
        keyboard.append([InlineKeyboardButton("💳 Приобрести подписку", callback_data="buy_subscription")])
    
    keyboard.append([InlineKeyboardButton("📊 Проверить статус", callback_data="check_subscription")])
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            message_text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить сообщение подписки: {e}")

async def show_buy_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать информацию о покупке подписки"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    try:
        await query.answer()
    except:
        pass
    
    message_text = (
        "💳 <b>ПРИОБРЕТЕНИЕ ПОДПИСКИ</b>\n\n"
        
        "💰 <b>Доступные способы оплаты:</b>\n"
        "• 💠 Банковская карта (Visa/MasterCard/Мир)\n"
        "• 📱 QIWI кошелек\n"
        "• 🔵 ЮMoney\n"
        "• 📲 Сбербанк Онлайн\n"
        "• 🟢 Tinkoff\n\n"
        
        "⚡ <b>Процесс оплаты:</b>\n"
        "1. Выберите способ оплаты\n"
        "2. Оплатите счет\n"
        "3. Подписка активируется автоматически\n"
        "4. Наслаждайтесь премиум-функциями!\n\n"
        
        f"💎 <b>Стоимость:</b> {SUBSCRIPTION_PRICE}\n\n"
        
        "🛡️ <b>Гарантии:</b>\n"
        "• 🔄 Возврат средств в течение 24 часов\n"
        "• 📞 Техническая поддержка\n"
        "• 🔒 Безопасные платежи\n\n"
        
        "📞 <b>Для оплаты свяжитесь с администратором:</b>\n"
        "@username_администратора\n\n"
        
        "<i>Функция автоматической оплаты скоро будет доступна!</i>"
    )
    
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Назад к подписке", callback_data="subscription")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
    ])
    
    try:
        await query.edit_message_text(
            message_text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить сообщение покупки: {e}")

async def check_subscription_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверить статус подписки"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    try:
        await query.answer()
    except:
        pass
    
    subscription_status = get_subscription_status(user_id)
    
    if is_user_subscribed(user_id):
        if user_id in USER_SUBSCRIPTIONS:
            end_date = datetime.fromisoformat(USER_SUBSCRIPTIONS[user_id])
            days_left = (end_date - datetime.now()).days
            status_text = f"⏳ Осталось дней: {days_left}"
        else:
            status_text = "⏳ Бессрочная"
        
        message_text = (
            "✅ <b>СТАТУС ПОДПИСКИ</b>\n\n"
            f"📊 <b>Статус:</b> {subscription_status}\n"
            f"{status_text}\n\n"
            "💫 <b>Ваши преимущества активны!</b>\n"
            "Вы можете использовать все функции бота."
        )
    else:
        message_text = (
            "❌ <b>СТАТУС ПОДПИСКИ</b>\n\n"
            f"📊 <b>Статус:</b> {subscription_status}\n\n"
            "🔒 <b>Функции ограничены:</b>\n"
            "• ❌ Накрутка сообщений VK\n"
            "• ❌ Накрутка фотографий VK\n"
            "• ⚡ Базовая скорость\n\n"
            "💎 <i>Приобретите подписку для разблокировки!</i>"
        )
    
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Приобрести подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton("🔙 Назад", callback_data="subscription")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
    ])
    
    try:
        await query.edit_message_text(
            message_text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить сообщение статуса: {e}")

async def handle_first_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик первого сообщения с капчей"""
    user_id = str(update.message.from_user.id)
    
    # Если пользователь уже верифицирован - пропускаем
    if USER_VERIFIED.get(user_id):
        await handle_regular_message(update, context)
        return
    
    # Если это первое сообщение - показываем капчу
    await update.message.reply_text(
        "🛡️ <b>Защита от ботов</b>\n\n"
        "Для продолжения работы подтвердите, что вы не робот:\n\n"
        "⚠️ <i>Это необходимо для безопасности системы</i>",
        parse_mode='HTML',
        reply_markup=get_captcha_button()
    )

async def cleanup_user_process(user_id: str, context: ContextTypes.DEFAULT_TYPE = None):
    """Полная очистка всех флагов и данных пользователя"""
    logger.info(f"🔍 Начало полной очистки для {user_id}")

    async with PROCESS_LOCK:  # Блокируем доступ для безопасной очистки
        # Очищаем глобальные флаги
        PHOTO_PROCESS.pop(user_id, None)
        CANCEL_FLAGS.pop(user_id, None)
        CANCEL_PROCESS.pop(user_id, None)
        MESSAGE_PROCESSING.pop(user_id, None)

        if context:
            context.user_data.pop('waiting_for_photo_info', None)
            context.user_data.pop('waiting_for_photo_details', None)
            context.user_data.pop('pending_photo', None)
            context.user_data.pop('current_photo_count', None)

    logger.info(f"🔍 Завершена очистка для {user_id}")

async def process_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str, album_id: str, photo_count: int, photo, message, photo_caption: str = None):
    """ГАРАНТИРОВАННЫЙ процесс загрузки фотографий"""
    user_id = str(update.message.from_user.id)
    
    # 🔧 УСТАНАВЛИВАЕМ ФЛАГ ПРОЦЕССА
    PHOTO_PROCESS[user_id] = True
    
    try:
        # Получаем файл фото ОДИН РАЗ
        photo_file = await photo.get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        
        success_count = 0
        failed_count = 0

        await safe_edit_message(
            message,
            f"🚀 <b>НАЧИНАЮ ГАРАНТИРОВАННУЮ ЗАГРУЗКУ</b>\n\n"
            f"📸 Цель: {photo_count} фото\n"
            f"🏷️ Название: {photo_caption or 'БЕЗ НАЗВАНИЯ'}\n"
            f"🆔 ID процесса: {user_id[-6:]}\n\n"
            f"<i>Подготовка к 100% загрузке...</i>",
            reply_markup=get_photo_cancel_button()
        )

        # 🔧 ОСНОВНОЙ ЦИКЛ С ГАРАНТИЕЙ
        for i in range(photo_count):
            # Проверяем отмену
            if not PHOTO_PROCESS.get(user_id) or CANCEL_FLAGS.get(user_id):
                logger.info(f"Процесс остановлен на фото {i}")
                break
            
            # 🔧 ЗАГРУЗКА С ГАРАНТИЕЙ
            result = await upload_single_photo_guaranteed(token, album_id, photo_bytes, photo_caption, i, user_id)
            
            if result:
                success_count += 1
                status = "✅ УСПЕХ"
            else:
                failed_count += 1
                status = "❌ ПОВТОР"

            # 🔧 ОБНОВЛЯЕМ СТАТУС КАЖДОЕ ФОТО
            progress = min(100, int(((i + 1) / photo_count) * 100))
            progress_bar = "█" * (progress // 4) + "░" * (25 - progress // 4)
            
            status_text = (
                f"📸 <b>ГАРАНТИРОВАННАЯ ЗАГРУЗКА</b>\n\n"
                f"📊 Прогресс: {i + 1}/{photo_count}\n"
                f"📈 Статус: {progress_bar} {progress}%\n"
                f"✅ Успешно: {success_count} фото\n"
                f"🔄 Обработано: {i + 1} фото\n"
                f"🎯 Результат: {status}\n"
                f"🆔 ID: {user_id[-6:]}\n\n"
            )
            
            if not PHOTO_PROCESS.get(user_id):
                status_text += "🛑 <b>Процесс остановлен</b>"
            else:
                status_text += "⚡ <b>Продолжаю гарантированную загрузку...</b>"
            
            await safe_edit_message(
                message,
                status_text,
                reply_markup=get_photo_cancel_button() if PHOTO_PROCESS.get(user_id) else get_back_button()
            )
            
            # 🔧 ОПТИМАЛЬНАЯ ЗАДЕРЖКА МЕЖДУ ФОТО
            await asyncio.sleep(1)

        # 🔧 ФИНАЛЬНЫЙ СБРОС ФЛАГОВ
        PHOTO_PROCESS[user_id] = False
        CANCEL_FLAGS.pop(user_id, None)
        
        # Очистка данных пользователя
        context.user_data.pop('waiting_for_photo_info', None)
        context.user_data.pop('waiting_for_photo_details', None)
        context.user_data.pop('pending_photo', None)
        context.user_data.pop('current_photo_count', None)
        
        await show_final_success_message(message, success_count, failed_count, photo_count, album_id, photo_caption, token)
        
    except Exception as e:
        logger.error(f"Критическая ошибка в process_photo_upload: {e}")
        # 🔧 ГАРАНТИРОВАННЫЙ СБРОС ПРИ ОШИБКЕ
        PHOTO_PROCESS[user_id] = False
        CANCEL_FLAGS.pop(user_id, None)
        
        await safe_edit_message(
            message,
            "❌ <b>КРИТИЧЕСКАЯ ОШИБКА</b>\n\n"
            "Произошла непредвиденная ошибка\n\n"
            "🔧 Все флаги сброшены",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
            ])
        )

async def verify_album_before_upload(token: str, album_id: str) -> bool:
    """Тщательная проверка альбома перед загрузкой"""
    try:
        # Проверяем существование альбома
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,
                'need_system': 0
            },
            timeout=10
        )
        data = response.json()
        
        if 'response' not in data or data['response']['count'] == 0:
            return False
        
        # Проверяем права на запись
        upload_response = requests.post(
            'https://api.vk.com/method/photos.getUploadServer',
            params={
                'access_token': token,
                'v': '5.199',
                'album_id': album_id
            },
            timeout=10
        )
        upload_data = upload_response.json()
        
        return 'response' in upload_data and 'upload_url' in upload_data['response']
        
    except Exception as e:
        logger.error(f"Ошибка проверки альбома: {e}")
        return False

async def unadm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выход из режима администратора"""
    user_id = str(update.message.from_user.id)
    
    if user_id in ADMIN_USERS:
        ADMIN_USERS.remove(user_id)
        save_subscriptions()
        
        await update.message.reply_text(
            "🔓 <b>ВЫ ВЫШЛИ ИЗ РЕЖИМА АДМИНИСТРАТОРА</b>\n\n"
            "✅ Теперь у вас обычные права пользователя\n\n"
            "💡 Для входа снова используйте:\n"
            "<code>/adm ваш_пароль</code>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
            ])
        )
    else:
        await update.message.reply_text(
            "❌ Вы не являетесь администратором!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
            ])
        )

async def get_upload_server_simple(token: str, album_id: str) -> str:
    """Простое получение upload server"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://api.vk.com/method/photos.getUploadServer',
                params={
                    'access_token': token,
                    'v': '5.199',
                    'album_id': album_id
                },
                timeout=10
            ) as response:
                data = await response.json()
                return data.get('response', {}).get('upload_url') if 'response' in data else None
    except Exception as e:
        logger.error(f"Ошибка получения upload server: {e}")
        return None

async def show_final_success_message(message, success_count: int, failed_count: int, total_count: int, album_id: str, photo_caption: str, token: str):
    """Финальное сообщение с ПРАВИЛЬНОЙ ссылкой на альбом"""
    # 🔧 СБРАСЫВАЕМ ФЛАГИ ПЕРЕД ОТПРАВКОЙ СООБЩЕНИЯ
    user_id = str(message.chat.id)
    PHOTO_PROCESS.pop(user_id, None)
    CANCEL_FLAGS.pop(user_id, None)
    
    # 🔧 КРИТИЧЕСКИЙ ФИКС: Получаем ПРАВИЛЬНУЮ информацию об альбоме
    album_info = await get_album_info_by_id(token, album_id)
    
    if album_info and album_info.get('status') == 'success':
        album_title = album_info.get('title', 'ваш альбом')
        owner_id = album_info.get('owner_id')
        # 🔧 ПРАВИЛЬНАЯ ссылка на альбом
        album_url = f"https://vk.com/album{owner_id}_{album_id}"
        
        # 🔧 ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: убеждаемся что альбом реально существует
        album_exists = await verify_album_via_browser(album_url)
        if not album_exists:
            album_url = f"https://vk.com/albums{owner_id}?z=album{owner_id}_{album_id}"
    else:
        # Резервный вариант если не удалось получить информацию
        album_title = "ваш альбом"
        # Пробуем получить owner_id из токена
        user_info = await get_vk_user_info(token)
        owner_id = user_info.get('id') if user_info else '0'
        album_url = f"https://vk.com/album{owner_id}_{album_id}"
    
    # 🔧 ИСПРАВЛЕННЫЙ ТЕКСТ СООБЩЕНИЯ
    message_text = (
        f"🎉 <b>НАКРУТКА ЗАВЕРШЕНА!</b>\n\n"
        f"📊 <b>Результаты:</b>\n"
        f"├ 🎯 Планировалось: {total_count} фото\n"
        f"├ ✅ Успешно загружено: {success_count} фото\n"
        f"├ ❌ Ошибок при загрузке: {failed_count} фото\n"
        f"├ 📈 Эффективность: {(success_count/total_count)*100:.1f}%\n"
        f"├ 🏷️ Использовано название: {photo_caption or 'БЕЗ НАЗВАНИЯ'}\n"
        f"└ 📁 Альбом: {album_title}\n\n"
        f"🔗 <b>Ссылка на альбом:</b>\n"
        f"<code>{album_url}</code>\n\n"
    )
    
    # Добавляем предупреждение если много ошибок
    if failed_count > total_count * 0.3:
        message_text += "⚠️ <b>Много ошибок!</b> Проверьте настройки приватности альбома в VK.\n\n"
    
    message_text += "💫 Все загруженные фото теперь в вашем альбоме VK!"
    
    # 🔧 ИСПРАВЛЕННАЯ КЛАВИАТУРА
    keyboard = [
        [InlineKeyboardButton("📁 Открыть альбом VK", url=album_url)],
        [InlineKeyboardButton("🔄 Накрутить еще", callback_data="start_photo_upload")],
    ]
    
    # 🔧 ДОБАВЛЯЕМ КНОПКИ ПРИ ОШИБКАХ
    if failed_count > total_count * 0.3:
        keyboard.append([InlineKeyboardButton("🔧 Проверить настройки", callback_data="photo_upload")])
    
    keyboard.append([InlineKeyboardButton("📊 Статистика", callback_data="photo_stats")])
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await safe_edit_message(message, message_text, reply_markup)
    
    # 🔧 ФИКС: Добавляем задержку для гарантии очистки
    await asyncio.sleep(1)
    logger.info(f"✅ Накрутка завершена для {user_id}: {success_count}/{total_count} фото")

async def get_album_info_by_id(token: str, album_id: str) -> dict:
    """Получает точную информацию об альбоме по ID"""
    try:
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,
                'need_system': 0
            },
            timeout=10
        )
        data = response.json()
        
        if 'response' in data and data['response']['count'] > 0:
            album = data['response']['items'][0]
            return {
                'status': 'success',
                'title': album.get('title', 'Без названия'),
                'owner_id': album.get('owner_id'),
                'id': album.get('id'),
                'size': album.get('size', 0),
                'description': album.get('description', '')
            }
        return {'status': 'error'}
    except Exception as e:
        logger.error(f"Ошибка получения информации об альбоме: {e}")
        return {'status': 'error'}

async def verify_album_via_browser(album_url: str) -> bool:
    """Проверяет что альбом доступен через браузер (косвенная проверка)"""
    try:
        import requests
        response = requests.get(album_url, timeout=5)
        # Если страница не возвращает 404, считаем что альбом существует
        return response.status_code != 404
    except:
        return False

# 🔧 ФИКС: УЛУЧШЕННАЯ ОТМЕНА ПРОЦЕССА
async def universal_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Мгновенная отмена всех процессов"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    try:
        await query.answer("🛑 Останавливаю накрутку...")
    except:
        pass
    
    logger.info(f"Пользователь {user_id} отменил процесс накрутки")

    # 🔧 СБРАСЫВАЕМ ФЛАГИ ПРОЦЕССА
    PHOTO_PROCESS[user_id] = False
    CANCEL_FLAGS[user_id] = True
    # Также ставим общий флаг отмены процессов (для функций накрутки сообщений)
    CANCEL_PROCESS[user_id] = True

    # Очищаем данные пользователя
    context.user_data.pop('waiting_for_photo_info', None)
    context.user_data.pop('waiting_for_photo_details', None)
    context.user_data.pop('pending_photo', None)

    await safe_edit_message(
        query.message,
        "🛑 <b>НАКРУТКА ОСТАНОВЛЕНА</b>\n\n"
        "❌ Все процессы прерваны по вашему запросу.\n\n"
        "💫 <i>Все флаги сброшены, можно начинать новую накрутку.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📸 Новая накрутка", callback_data="start_photo_upload")],
            [InlineKeyboardButton("📊 Статистика", callback_data="photo_stats")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
        ])
    )
    
    # 🔧 ДОПОЛНИТЕЛЬНЫЙ СБРОС ЧЕРЕЗ 2 СЕКУНДЫ
    await asyncio.sleep(2)
    PHOTO_PROCESS.pop(user_id, None)
    CANCEL_FLAGS.pop(user_id, None)
    CANCEL_PROCESS.pop(user_id, None)


async def get_upload_server_with_retry(token: str, album_id: str, retries: int = 2) -> str:
    """Получение upload server с повторными попытками"""
    for attempt in range(retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://api.vk.com/method/photos.getUploadServer',
                    params={
                        'access_token': token,
                        'v': '5.199',
                        'album_id': album_id
                    },
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    data = await response.json()
                    
                    if 'response' in data and data['response'].get('upload_url'):
                        return data['response']['upload_url']
                    elif 'error' in data:
                        logger.warning(f"Ошибка VK API (попытка {attempt + 1}): {data['error']}")
                        
        except Exception as e:
            logger.warning(f"Ошибка сети (попытка {attempt + 1}): {e}")
            
        if attempt < retries:
            await asyncio.sleep(2)
    
    return None

async def upload_single_photo_with_retry(semaphore, token: str, album_id: str, photo_bytes: bytes, 
                                       photo_caption: str, index: int, upload_url: str, user_id: str, 
                                       is_retry: bool = False) -> bool:
    """Загрузка одного фото с повторными попытками и улучшенной обработкой ошибок"""
    if not PHOTO_PROCESS.get(user_id):
        return False

    async with semaphore:
        for attempt in range(MAX_RETRIES + 1):
            if not PHOTO_PROCESS.get(user_id):
                return False
                
            try:
                caption = photo_caption if photo_caption else ""
                
                async with aiohttp.ClientSession() as session:
                    # 🔧 УЛУЧШЕННАЯ ЗАГРУЗКА С ПРОВЕРКАМИ
                    form_data = aiohttp.FormData()
                    form_data.add_field('file', photo_bytes, filename=f'photo_{index}_{attempt}.jpg', content_type='image/jpeg')
                    
                    # ЗАГРУЗКА НА СЕРВЕР VK
                    async with session.post(
                        upload_url,
                        data=form_data,
                        timeout=aiohttp.ClientTimeout(total=UPLOAD_TIMEOUT)
                    ) as upload_response:
                        
                        if upload_response.status != 200:
                            logger.debug(f"Ошибка HTTP {upload_response.status} для фото {index}")
                            continue
                            
                        upload_result = await upload_response.json()

                    # 🔧 ДЕТАЛЬНАЯ ПРОВЕРКА ОТВЕТА UPLOAD
                    if 'error' in upload_result:
                        error_msg = upload_result['error'].get('error_msg', 'Unknown error')
                        logger.debug(f"Ошибка upload фото {index}: {error_msg}")
                        continue

                    required_fields = ['server', 'photos_list', 'hash']
                    if not all(field in upload_result for field in required_fields):
                        logger.debug(f"Неполные данные upload для фото {index}")
                        continue

                    # 🔧 СОХРАНЕНИЕ ФОТО В АЛЬБОМ
                    save_params = {
                        'access_token': token,
                        'v': '5.199',
                        'album_id': album_id,
                        'server': str(upload_result['server']),
                        'photos_list': str(upload_result['photos_list']),
                        'hash': str(upload_result['hash']),
                        'caption': caption
                    }

                    async with session.post(
                        'https://api.vk.com/method/photos.save',
                        params=save_params,
                        timeout=aiohttp.ClientTimeout(total=15)
                    ) as save_response:
                        
                        if save_response.status != 200:
                            logger.debug(f"Ошибка HTTP save {save_response.status} для фото {index}")
                            continue
                            
                        save_data = await save_response.json()

                    # 🔧 ПРОВЕРКА УСПЕШНОГО СОХРАНЕНИЯ
                    if 'error' in save_data:
                        error_msg = save_data['error'].get('error_msg', 'Unknown error')
                        logger.debug(f"Ошибка save фото {index}: {error_msg}")
                        
                        # 🔧 ОСОБЫЕ СЛУЧАИ ОШИБОК
                        error_code = save_data['error'].get('error_code')
                        if error_code in [200, 201]:  # Доступ запрещен
                            logger.warning(f"Доступ запрещен для альбома {album_id}")
                            return False
                        elif error_code == 121:  # Неверный хэш
                            logger.debug(f"Неверный хэш для фото {index}, пробуем снова")
                            continue
                        else:
                            continue
                    
                    # 🔧 ПРОВЕРЯЕМ ЧТО ФОТО ДЕЙСТВИТЕЛЬНО СОХРАНИЛОСЬ
                    if 'response' in save_data and len(save_data['response']) > 0:
                        logger.debug(f"✅ Фото {index} успешно загружено")
                        return True
                    else:
                        logger.debug(f"Пустой ответ save для фото {index}")
                        continue

            except asyncio.TimeoutError:
                logger.debug(f"Таймаут для фото {index}, попытка {attempt + 1}")
            except aiohttp.ClientError as e:
                logger.debug(f"Ошибка сети для фото {index}: {e}")
            except Exception as e:
                logger.debug(f"Неожиданная ошибка для фото {index}: {e}")

            # 🔧 ЗАДЕРЖКА ПЕРЕД ПОВТОРНОЙ ПОПЫТКОЙ
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))  # Увеличивающаяся задержка

        return False
    
async def upload_single_photo_simple(token: str, album_id: str, photo_bytes: bytes, photo_caption: str, index: int, upload_url: str, user_id: str) -> bool:
    """УЛУЧШЕННАЯ загрузка одного фото с минимизацией ошибок"""
    # 🔧 ФИКС: ТРОЙНАЯ ПРОВЕРКА ОТМЕНЫ
    if not PHOTO_PROCESS.get(user_id) or CANCEL_FLAGS.get(user_id):
        return False

    try:
        caption = photo_caption if photo_caption else ""
        
        async with aiohttp.ClientSession() as session:
            # 🔧 УВЕЛИЧИВАЕМ ТАЙМАУТЫ ДЛЯ СТАБИЛЬНОСТИ
            form_data = aiohttp.FormData()
            form_data.add_field('file', photo_bytes, filename=f'photo_{index}.jpg', content_type='image/jpeg')
            
            # 🔧 ПЕРВАЯ ПОПЫТКА ЗАГРУЗКИ
            try:
                async with session.post(
                    upload_url,
                    data=form_data,
                    timeout=aiohttp.ClientTimeout(total=60)  # Увеличиваем таймаут
                ) as upload_response:
                    if upload_response.status != 200:
                        logger.debug(f"Ошибка HTTP {upload_response.status} для фото {index}, пробуем еще раз...")
                        return False
                    upload_result = await upload_response.json()
            except asyncio.TimeoutError:
                logger.debug(f"Таймаут загрузки фото {index}, пробуем еще раз...")
                return False

            # 🔧 ДЕТАЛЬНАЯ ПРОВЕРКА ОТВЕТА UPLOAD
            if 'error' in upload_result:
                error_msg = upload_result['error'].get('error_msg', 'Unknown error')
                logger.debug(f"Ошибка upload фото {index}: {error_msg}")
                return False

            # 🔧 ПРОВЕРКА ВСЕХ ОБЯЗАТЕЛЬНЫХ ПОЛЕЙ
            required_fields = ['server', 'photos_list', 'hash']
            if not all(field in upload_result for field in required_fields):
                logger.debug(f"Неполные данные upload для фото {index}")
                return False
            
            # 🔧 ПРЕОБРАЗОВАНИЕ ДАННЫХ ДЛЯ VK API
            save_params = {
                'access_token': token,
                'v': '5.199',
                'album_id': album_id,
                'server': str(upload_result['server']),
                'photos_list': str(upload_result['photos_list']),
                'hash': str(upload_result['hash']),
                'caption': caption
            }

            # 🔧 СОХРАНЕНИЕ ФОТО С ПОВТОРНЫМИ ПОПЫТКАМИ
            for attempt in range(3):  # 3 попытки сохранения
                try:
                    async with session.post(
                        'https://api.vk.com/method/photos.save',
                        params=save_params,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as save_response:
                        if save_response.status != 200:
                            logger.debug(f"Ошибка HTTP save {save_response.status} для фото {index}, попытка {attempt + 1}")
                            await asyncio.sleep(1)  # Задержка перед повторной попыткой
                            continue
                        
                        save_data = await save_response.json()

                    # 🔧 ПРОВЕРКА УСПЕШНОГО СОХРАНЕНИЯ
                    if 'error' in save_data:
                        error_msg = save_data['error'].get('error_msg', 'Unknown error')
                        error_code = save_data['error'].get('error_code')
                        
                        # 🔧 ОБРАБОТКА ЧАСТЫХ ОШИБОК
                        if error_code in [6, 9]:  # Слишком много запросов, попробуйте позже
                            logger.debug(f"Ошибка VK {error_code} для фото {index}, ждем 2 секунды...")
                            await asyncio.sleep(2)
                            continue
                        elif error_code in [121, 122]:  # Неверный хэш или album_id
                            logger.debug(f"Критическая ошибка VK {error_code} для фото {index}")
                            return False
                        else:
                            logger.debug(f"Ошибка save фото {index}: {error_code} - {error_msg}")
                            await asyncio.sleep(1)
                            continue
                    
                    # 🔧 ПРОВЕРКА ЧТО ФОТО ДЕЙСТВИТЕЛЬНО СОХРАНИЛОСЬ
                    if 'response' in save_data and isinstance(save_data['response'], list) and len(save_data['response']) > 0:
                        logger.debug(f"✅ Фото {index} успешно загружено (попытка {attempt + 1})")
                        return True
                    else:
                        logger.debug(f"Пустой ответ save для фото {index}, попытка {attempt + 1}")
                        await asyncio.sleep(1)
                        continue

                except asyncio.TimeoutError:
                    logger.debug(f"Таймаут save фото {index}, попытка {attempt + 1}")
                    await asyncio.sleep(1)
                    continue
                except aiohttp.ClientError as e:
                    logger.debug(f"Ошибка сети save для фото {index}: {e}, попытка {attempt + 1}")
                    await asyncio.sleep(1)
                    continue

            return False

    except Exception as e:
        logger.debug(f"Неожиданная ошибка для фото {index}: {e}")
        return False

async def show_simple_success_message(message, success_count: int, failed_count: int, total_count: int, 
                                    album_id: str, photo_caption: str, token: str):
    """Простое сообщение об успехе"""
    
    # Создаем ссылку на альбом
    if '_' in album_id:
        owner_id, album_num = album_id.split('_')
        album_url = f"https://vk.com/album{owner_id}_{album_num}"
    else:
        album_url = f"https://vk.com/album-{album_id}"
    
    # Пытаемся получить название альбома
    album_title = "ваш альбом"
    try:
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,
                'need_system': 0
            },
            timeout=5
        )
        data = response.json()
        if 'response' in data and data['response']['count'] > 0:
            album_title = data['response']['items'][0].get('title', 'ваш альбом')
    except:
        pass
    
    # Определяем результат
    if failed_count == 0:
        title = "🎉 ВСЕ ФОТО ЗАГРУЖЕНЫ!"
        status = "Отличный результат!"
    elif failed_count <= total_count * 0.1:  # Меньше 10% ошибок
        title = "✅ ПОЧТИ ВСЕ ФОТО ЗАГРУЖЕНЫ"
        status = f"Не загрузилось всего {failed_count} фото"
    else:
        title = "⚠️ ЧАСТЬ ФОТО НЕ ЗАГРУЗИЛАСЬ"
        status = f"Проблемы с {failed_count} фото"
    
    await message.edit_text(
        f"{title}\n\n"
        f"{status}\n\n"
        f"📊 <b>Результат:</b>\n"
        f"• 📸 Всего: {total_count} фото\n"
        f"• ✅ Успешно: {success_count} фото\n"
        f"• ❌ Ошибок: {failed_count} фото\n"
        f"• 🏷️ Название: {photo_caption or 'БЕЗ НАЗВАНИЯ'}\n"
        f"• 📁 Альбом: {album_title}\n\n"
        f"🔗 <b>Ссылка на альбом:</b>\n"
        f"<code>{album_url}</code>\n\n"
        f"💫 Все загруженные фото теперь в вашем альбоме VK!",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 Открыть альбом VK", url=album_url)],
            [InlineKeyboardButton("🔄 Накрутить еще", callback_data="start_photo_upload")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
        ])
    )

async def get_upload_server_fast(token: str, album_id: str) -> str:
    """Быстрое получение upload server с обработкой ошибок"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://api.vk.com/method/photos.getUploadServer',
                params={
                    'access_token': token,
                    'v': '5.199',
                    'album_id': album_id
                },
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                data = await response.json()
                return data.get('response', {}).get('upload_url') if 'response' in data else None
    except Exception as e:
        logger.error(f"Ошибка получения upload server: {e}")
        return None

async def upload_single_photo_fast(semaphore, token: str, album_id: str, photo_bytes: bytes, 
                                 photo_caption: str, index: int, upload_url: str, user_id: str) -> bool:
    """ОПТИМИЗИРОВАННАЯ загрузка одного фото"""
    if not PHOTO_PROCESS.get(user_id) or CANCEL_FLAGS.get(user_id):
        return False

    async with semaphore:
        try:
            # 🔧 ИСПОЛЬЗУЕМ ОДИНАКОВУЮ ПОДПИСЬ ДЛЯ ВСЕХ ФОТО
            caption = photo_caption if photo_caption else ""
            
            async with aiohttp.ClientSession() as session:
                # 🔧 ОПТИМИЗИРОВАННАЯ ЗАГРУЗКА ФОТО
                form_data = aiohttp.FormData()
                form_data.add_field('file', photo_bytes, filename=f'photo_{index}.jpg', content_type='image/jpeg')
                
                async with session.post(
                    upload_url,
                    data=form_data,
                    timeout=aiohttp.ClientTimeout(total=UPLOAD_TIMEOUT)
                ) as upload_response:
                    if upload_response.status != 200:
                        return False
                    upload_result = await upload_response.json()

                if 'error' in upload_result:
                    return False

                # 🔧 ПРОВЕРЯЕМ ОБЯЗАТЕЛЬНЫЕ ПОЛЯ
                required_fields = ['server', 'photos_list', 'hash']
                if not all(field in upload_result for field in required_fields):
                    return False
                
                # 🔧 ОПТИМИЗИРОВАННОЕ СОХРАНЕНИЕ
                save_params = {
                    'access_token': token,
                    'v': '5.199',
                    'album_id': album_id,
                    'server': str(upload_result['server']),
                    'photos_list': str(upload_result['photos_list']),
                    'hash': str(upload_result['hash']),
                    'caption': caption
                }

                async with session.post(
                    'https://api.vk.com/method/photos.save',
                    params=save_params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as save_response:
                    if save_response.status != 200:
                        return False
                    save_data = await save_response.json()

                return 'error' not in save_data

        except Exception as e:
            logger.debug(f"Ошибка загрузки фото {index}: {e}")
            return False
        
async def show_optimized_success_message(message, success_count: int, failed_count: int, total_count: int, 
                                       album_id: str, photo_caption: str, token: str):
    """УЛУЧШЕННОЕ финальное сообщение с анализом качества"""
    
    # Создаем ссылку на альбом
    if '_' in album_id:
        owner_id, album_num = album_id.split('_')
        album_url = f"https://vk.com/album{owner_id}_{album_num}"
    else:
        album_url = f"https://vk.com/album-{album_id}"
    
    # Получаем информацию об альбоме
    album_title = "ваш альбом"
    try:
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,
                'need_system': 0
            },
            timeout=5
        )
        data = response.json()
        if 'response' in data and data['response']['count'] > 0:
            album_title = data['response']['items'][0].get('title', 'ваш альбом')
            album_url = f"https://vk.com/album{data['response']['items'][0].get('owner_id')}_{album_id}"
    except:
        pass
    
    # 🔧 АНАЛИЗ КАЧЕСТВА ЗАГРУЗКИ
    if failed_count == 0:
        title = "🎉 ИДЕАЛЬНЫЙ РЕЗУЛЬТАТ!"
        status_emoji = "✨"
        status_text = "Все фото загружены без ошибок!"
        quality_rating = "💎 ПРЕМИУМ КАЧЕСТВО"
    elif failed_count <= total_count * 0.02:  # Меньше 2% ошибок
        title = "✅ ОТЛИЧНЫЙ РЕЗУЛЬТАТ!"
        status_emoji = "🌟"
        status_text = f"Почти идеально! Всего {failed_count} ошибок"
        quality_rating = "⭐ ВЫСОКОЕ КАЧЕСТВО"
    elif failed_count <= total_count * 0.05:  # Меньше 5% ошибок
        title = "⚠️ ХОРОШИЙ РЕЗУЛЬТАТ"
        status_emoji = "💫"
        status_text = f"Хороший результат, {failed_count} ошибок"
        quality_rating = "📊 СТАНДАРТНОЕ КАЧЕСТВО"
    else:
        title = "❌ МНОГО ОШИБОК"
        status_emoji = "🔧"
        status_text = f"Рекомендуем проверить настройки"
        quality_rating = "⚠️ ТРЕБУЕТСЯ ПРОВЕРКА"
    
    message_text = (
        f"{status_emoji} <b>{title}</b>\n\n"
        f"{status_text}\n\n"
        f"📊 <b>Детальная статистика:</b>\n"
        f"├ 🎯 Планировалось: {total_count} фото\n"
        f"├ ✅ Успешно загружено: {success_count} фото\n"
        f"├ ❌ Ошибок при загрузке: {failed_count} фото\n"
        f"├ 📈 Эффективность: {(success_count/total_count)*100:.1f}%\n"
        f"├ 🏆 Качество: {quality_rating}\n"
        f"├ 🏷️ Использовано название: {photo_caption or 'БЕЗ НАЗВАНИЯ'}\n"
        f"└ 📁 Альбом: {album_title}\n\n"
        f"🔗 <b>Ссылка на альбом:</b>\n"
        f"<code>{album_url}</code>\n\n"
        f"💫 <i>Все загруженные фото теперь в вашем альбоме VK!</i>"
    )
    
    keyboard = [
        [InlineKeyboardButton("📁 Открыть альбом VK", url=album_url)],
        [InlineKeyboardButton("🔄 Накрутить еще", callback_data="start_photo_upload")],
    ]
    
    # Добавляем кнопку диагностики при ошибках
    if failed_count > 0:
        keyboard.append([InlineKeyboardButton("🔧 Диагностика ошибок", callback_data="photo_diagnostics")])
    
    keyboard.append([InlineKeyboardButton("📊 Статистика", callback_data="photo_stats")])
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await safe_edit_message(message, message_text, reply_markup)

async def show_success_message(update: Update, context: ContextTypes.DEFAULT_TYPE, message, success_count: int, failed_count: int, album_id: str, photo_caption: str = None):
    """Красивое сообщение об успешном завершении"""
    
    # Получаем photo_count из context.user_data
    photo_count = context.user_data.get('current_photo_count', success_count + failed_count)
    
    # Получаем информацию для ссылки на альбом
    user_id = str(update.message.from_user.id)
    tokens = load_tokens()
    token = tokens.get(user_id)
    
    album_url = f"https://vk.com/album{album_id.split('_')[0]}_{album_id}" if '_' in album_id else f"https://vk.com/album-{album_id}"
    
    if token:
        try:
            # Получаем информацию об альбоме для красивого отображения
            response = requests.get(
                'https://api.vk.com/method/photos.getAlbums',
                params={
                    'access_token': token,
                    'v': '5.199',
                    'album_ids': album_id,
                    'need_system': 0
                },
                timeout=5
            )
            data = response.json()
            if 'response' in data and data['response']['count'] > 0:
                album_title = data['response']['items'][0].get('title', 'Ваш альбом')
                album_url = f"https://vk.com/album{data['response']['items'][0].get('owner_id')}_{album_id}"
        except:
            pass
    
    # Создаем красивое сообщение
    if success_count == photo_count:
        title = "🎉 БЛЕСТЯЩИЙ УСПЕХ!"
        emoji = "✨"
    elif success_count > photo_count * 0.7:
        title = "✅ ОТЛИЧНЫЙ РЕЗУЛЬТАТ!"
        emoji = "🌟"
    else:
        title = "⚠️ ЗАВЕРШЕНО С ОШИБКАМИ"
        emoji = "💫"
    
    await message.edit_text(
        f"{emoji} <b>{title}</b>\n\n"
        f"📊 <b>Итоговая статистика:</b>\n"
        f"├ ✅ Успешно загружено: <b>{success_count}</b> фото\n"
        f"├ ❌ Ошибок при загрузке: <b>{failed_count}</b>\n"
        f"├ 🏷️ Использовано название: <b>{photo_caption or 'Стандартное'}</b>\n"
        f"└ 📁 Альбом обновлен\n\n"
        f"🔗 <b>Ссылка на альбом:</b>\n"
        f"<code>{album_url}</code>\n\n"
        f"💫 <i>Все фото успешно добавлены в указанный альбом VK!</i>",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 Открыть альбом VK", url=album_url)],
            [InlineKeyboardButton("🔄 Накрутить еще", callback_data="start_photo_upload")],
            [InlineKeyboardButton("📸 Другие функции", callback_data="photo_upload")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
        ])
    )
    
    # Очищаем временные данные
    context.user_data.pop('current_photo_count', None)

async def captcha_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик подтверждения капчи с сохранением"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    await query.answer("✅ Верификация пройдена!")
    
    # Отмечаем пользователя как верифицированного
    USER_VERIFIED[user_id] = True
    save_verified_users(USER_VERIFIED)  # Сохраняем в файл
    
    # Сразу показываем меню после подтверждения капчи
    await show_menu(update, context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start с проверкой верификации"""
    user_id = str(update.message.from_user.id)
    
    # Если пользователь уже верифицирован - показываем меню
    if USER_VERIFIED.get(user_id):
        await show_menu(update, context)
        return
    
    # Если первый раз - показываем капчу
    await update.message.reply_text(
        "🛡️ <b>Защита от ботов</b>\n\n"
        "Для продолжения работы подтвердите, что вы не робот:\n\n"
        "⚠️ <i>Это необходимо для безопасности системы</i>",
        parse_mode='HTML',
        reply_markup=get_captcha_button()
    )

async def handle_regular_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обычный обработчик сообщений после верификации"""
    user_id = str(update.message.from_user.id)
    
    # Проверяем верификацию (из памяти или файла)
    if not USER_VERIFIED.get(user_id):
        # Перепроверяем файл на случай перезапуска бота
        fresh_verified = load_verified_users()
        if user_id in fresh_verified:
            USER_VERIFIED[user_id] = True
        else:
            await handle_first_message(update, context)
            return
    
    # Остальная логика бота...
    if (context.user_data.get('waiting_for_token') or 
        context.user_data.get('updating_token')):
        await handle_token_message(update, context)
    elif context.user_data.get('waiting_for_photo_info'):
        await handle_photo_upload(update, context)
    else:
        await update.message.reply_text(
            "Используйте меню для навигации по функциям бота:",
            reply_markup=get_back_button()
        )

# ========== ОСНОВНЫЕ ФУНКЦИИ БОТА ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = str(update.message.from_user.id)
    
    # Если пользователь уже верифицирован - показываем меню
    if USER_VERIFIED.get(user_id):
        await show_menu(update, context)
        return
    
    # Если первый раз - показываем капчу
    await update.message.reply_text(
        "🛡️ <b>Защита от ботов</b>\n\n"
        "Для продолжения работы подтвердите, что вы не робот:\n\n"
        "⚠️ <i>Это необходимо для безопасности системы</i>",
        parse_mode='HTML',
        reply_markup=get_captcha_button()
    )

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать главное меню - ОБНОВЛЕННАЯ ВЕРСИЯ С ПОДПИСКОЙ"""
    user_id = str(update.effective_user.id)
    tokens = load_tokens()
    
    has_token = user_id in tokens
    is_subscribed = is_user_subscribed(user_id)
    is_admin = user_id in ADMIN_USERS
    
    # Статусная строка
    status_lines = []
    
    if is_admin:
        status_lines.append("👑 Статус: Администратор")
    else:
        status_lines.append(f"💎 Подписка: {'✅ Активна' if is_subscribed else '❌ Отсутствует'}")
    
    status_lines.append(f"🔗 VK: {'✅ Подключен' if has_token else '❌ Не подключен'}")
    
    status_text = "\n".join(status_lines)
    
    # Создаем клавиатуру
    keyboard = []
    
    if has_token and (is_subscribed or is_admin):
        keyboard.append([InlineKeyboardButton("🔧 Функции для VK", callback_data="vk_functions")])
    
    keyboard.append([InlineKeyboardButton("🔗 Подключение", callback_data="connect")])
    keyboard.append([InlineKeyboardButton("👤 Профиль", callback_data="profile")])
    keyboard.append([InlineKeyboardButton("💎 Подписка", callback_data="subscription")])
    
    if is_admin:
        keyboard.append([InlineKeyboardButton("👑 Админ", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        query = update.callback_query
        try:
            await query.answer()
            await query.edit_message_text(
                f"🏠 <b>Главное меню бота VK</b>\n\n"
                f"{status_text}\n\n"
                f"Выберите нужный раздел:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.warning(f"Не удалось обновить главное меню: {e}")
    else:
        await update.message.reply_text(
            f"🏠 <b>Главное меню бота VK</b>\n\n"
            f"{status_text}\n\n"
            f"Выберите нужный раздел:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать профиль пользователя - ОБНОВЛЕННАЯ ВЕРСИЯ С ПОДПИСКОЙ"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    tokens = load_tokens()
    
    # Безопасный ответ
    try:
        await query.answer()
    except:
        pass
    
    # Получаем статус подписки
    subscription_status = get_subscription_status(user_id)
    
    if user_id in ADMIN_USERS:
        # Профиль для администратора
        profile_info = (
            f"👑 <b>ПРОФИЛЬ АДМИНИСТРАТОРА</b>\n\n"
            f"<b>📱 Telegram</b>\n"
            f"├ ID: <code>{query.from_user.id}</code>\n"
            f"├ Имя: {query.from_user.first_name}\n"
            f"├ Фамилия: {query.from_user.last_name or '—'}\n"
            f"└ Юзернейм: @{query.from_user.username or '—'}\n\n"
            f"<b>🔗 VK</b>\n"
        )
        
        if user_id in tokens:
            token = tokens[user_id]
            vk_user_info = await get_vk_user_info(token)
            if vk_user_info:
                vk_user_name = f"{vk_user_info.get('first_name', 'Неизвестно')} {vk_user_info.get('last_name', '')}"
                vk_user_id = vk_user_info.get('id', 'Неизвестно')
                profile_info += (
                    f"├ Аккаунт: <b>{vk_user_name}</b>\n"
                    f"├ ID: {vk_user_id}\n"
                    f"├ Токен: ...{token[-8:]}\n"
                    f"└ Статус: ✅ <b>Активен</b>"
                )
            else:
                profile_info += (
                    f"├ Токен: ...{token[-8:]}\n"
                    f"└ Статус: ⚠️ <b>Требует проверки</b>"
                )
        else:
            profile_info += "└ Статус: ❌ <b>Не подключен</b>"
            
    else:
        # Профиль для обычного пользователя
        profile_info = (
            f"👤 <b>ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ</b>\n\n"
            f"<b>📱 Telegram</b>\n"
            f"├ ID: <code>{query.from_user.id}</code>\n"
            f"├ Имя: {query.from_user.first_name}\n"
            f"├ Фамилия: {query.from_user.last_name or '—'}\n"
            f"└ Юзернейм: @{query.from_user.username or '—'}\n\n"
            f"<b>💎 Подписка</b>\n"
            f"└ Статус: {subscription_status}\n\n"
            f"<b>🔗 VK</b>\n"
        )
        
        if user_id in tokens:
            token = tokens[user_id]
            vk_user_info = await get_vk_user_info(token)
            if vk_user_info:
                vk_user_name = f"{vk_user_info.get('first_name', 'Неизвестно')} {vk_user_info.get('last_name', '')}"
                vk_user_id = vk_user_info.get('id', 'Неизвестно')
                profile_info += (
                    f"├ Аккаунт: <b>{vk_user_name}</b>\n"
                    f"├ ID: {vk_user_id}\n"
                    f"├ Токен: ...{token[-8:]}\n"
                    f"└ Статус: ✅ <b>Активен</b>"
                )
            else:
                profile_info += (
                    f"├ Токен: ...{token[-8:]}\n"
                    f"└ Статус: ⚠️ <b>Требует проверки</b>"
                )
        else:
            profile_info += "└ Статус: ❌ <b>Не подключен</b>"
    
    # Клавиатура профиля
    keyboard = []
    
    if user_id in ADMIN_USERS:
        keyboard.extend([
            [InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")],
            [InlineKeyboardButton("🔄 Обновить информацию", callback_data="profile")],
            [InlineKeyboardButton("⚙️ Управление подключением", callback_data="connect")],
            [InlineKeyboardButton("🔧 Функции VK", callback_data="vk_functions")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
        ])
    else:
        if user_id in tokens:
            keyboard.extend([
                [InlineKeyboardButton("🔄 Обновить информацию", callback_data="profile")],
                [InlineKeyboardButton("⚙️ Управление подключением", callback_data="connect")],
            ])
        else:
            keyboard.append([InlineKeyboardButton("🔗 Подключить VK", callback_data="connect")])
        
        # Добавляем кнопку подписки для обычных пользователей
        keyboard.append([InlineKeyboardButton("💎 Управление подпиской", callback_data="subscription")])
        
        if user_id in tokens:
            keyboard.append([InlineKeyboardButton("🔧 Функции VK", callback_data="vk_functions")])
        
        keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            profile_info,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить сообщение профиля: {e}")

async def show_vk_functions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню функций VK - ОБНОВЛЕННАЯ ВЕРСИЯ С ПРОВЕРКОЙ ПОДПИСКИ"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    try:
        await query.answer()
    except:
        pass
    
    # Проверяем подписку
    if not is_user_subscribed(user_id):
        await show_subscription_required(update, context)
        return
    
    tokens = load_tokens()
    
    # Проверяем наличие токена
    if user_id not in tokens:
        await query.edit_message_text(
            "❌ Для использования функций VK необходимо подключить токен!\n\n"
            "🔗 Используйте раздел 'Подключение' для добавления токена VK.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Подключение", callback_data="connect")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
            ])
        )
        return
    
    # Проверяем валидность токена
    token = tokens[user_id]
    user_info = await get_vk_user_info(token)
    
    if not user_info:
        await query.edit_message_text(
            "❌ Недействительный токен VK!\n\n"
            "Возможные причины:\n"
            "• Токен устарел\n"
            "• Токен не имеет нужных прав\n"
            "• Профиль VK заблокирован\n\n"
            "🔧 Используйте 'Подключение' для обновления токена.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить токен", callback_data="update_token")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
            ])
        )
        return
    
    # Показываем функции VK
    vk_user_name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}"
    
    keyboard = [
        [InlineKeyboardButton("🔵 Накрутка сообщений", callback_data="unread_messages")],
        [InlineKeyboardButton("📸 Накрутка фотографий", callback_data="photo_upload")],
        [InlineKeyboardButton("💎 Проверить подписку", callback_data="check_subscription")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            f"🔧 <b>Функции для VK</b>\n\n"
            f"👤 Подключенный профиль: {vk_user_name}\n"
            f"🆔 ID: {user_info.get('id', 'Неизвестно')}\n"
            f"💎 Статус: Премиум-доступ ✅\n\n"
            f"Выберите нужную функцию:",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить сообщение функций VK: {e}")

async def show_subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать сообщение о необходимости подписки"""
    query = update.callback_query
    
    try:
        await query.answer("❌ Требуется подписка!")
    except:
        pass
    
    message_text = (
        "🔒 <b>ТРЕБУЕТСЯ ПОДПИСКА</b>\n\n"
        
        "💎 <b>Для доступа к функциям VK необходима премиум-подписка!</b>\n\n"
        
        "🚀 <b>Что вы получите с подпиской:</b>\n"
        "• 🔵 Накрутка непрочитанных сообщений VK\n"
        "• 📸 Массовая загрузка фотографий в альбомы\n"
        "• ⚡ Максимальная скорость работы\n"
        "• 🛡️ Приоритетная поддержка\n"
        "• 📊 Расширенная статистика\n\n"
        
        "💰 <b>Стоимость подписки:</b>\n"
        f"• {SUBSCRIPTION_PRICE}\n\n"
        
        "🎁 <b>Преимущества:</b>\n"
        "• 🔄 Автопродление\n"
        "• 💰 Возврат средств в течение 24 часов\n"
        "• 📱 Доступ с любого устройства\n\n"
        
        "⚡ <b>Ограничения бесплатной версии:</b>\n"
        "• ❌ Функции VK недоступны\n"
        "• ⏳ Очередь обработки\n"
        "• 📉 Базовые возможности\n\n"
        
        "🔑 <i>Приобретите подписку для разблокировки всех функций!</i>"
    )
    
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Приобрести подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton("📊 Проверить статус", callback_data="check_subscription")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
    ])
    
    try:
        await query.edit_message_text(
            message_text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить сообщение о подписке: {e}")

# ========== АДМИН ПАНЕЛЬ ==========

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Панель администратора"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    try:
        await query.answer()
    except:
        pass
    
    if user_id not in ADMIN_USERS:
        await query.edit_message_text("❌ Доступ запрещен!")
        return
    
    # Статистика
    total_users = len(USER_SUBSCRIPTIONS) + len(ADMIN_USERS)
    active_subscriptions = sum(1 for uid in USER_SUBSCRIPTIONS if is_user_subscribed(uid))
    
    message_text = (
        "👑 <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n\n"
        
        "📊 <b>Статистика:</b>\n"
        f"• 👥 Всего пользователей: {total_users}\n"
        f"• 💎 Активных подписок: {active_subscriptions}\n"
        f"• 👑 Администраторов: {len(ADMIN_USERS)}\n\n"
        
        "⚙️ <b>Управление:</b>\n"
        "• Добавить/удалить подписки\n"
        "• Просмотр статистики\n"
        "• Управление пользователями\n\n"
        
        "🔧 <b>Функции:</b>\n"
        "• Все функции VK доступны\n"
        "• Приоритетная обработка\n"
        "• Расширенный доступ"
    )
    
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика подписок", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 Управление пользователями", callback_data="admin_users")],
        [InlineKeyboardButton("💎 Управление подписками", callback_data="admin_subscriptions")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
    ])
    
    try:
        await query.edit_message_text(
            message_text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить админ панель: {e}")

# ========== КОМАНДА АДМИНА ==========

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для входа в админ панель"""
    user_id = str(update.message.from_user.id)
    
    if len(context.args) == 0:
        await update.message.reply_text("❌ Использование: /adm <пароль>")
        return
    
    password = context.args[0]
    
    # Проверка пароля (замените на свой пароль)
    if password == "hook17":  # Измените этот пароль!
        ADMIN_USERS.add(user_id)
        save_subscriptions()
        
        await update.message.reply_text(
            "✅ <b>Вы вошли как администратор!</b>\n\n"
            "Теперь вам доступны:\n"
            "• Все функции бота без ограничений\n"
            "• Панель администратора\n"
            "• Расширенный доступ",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
            ])
        )
    else:
        await update.message.reply_text("❌ Неверный пароль!")

# ========== ФУНКЦИИ ДЛЯ НАКРУТКИ СООБЩЕНИЙ ==========

async def show_unread_messages_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать главное меню накрутки сообщений - С ПРОВЕРКОЙ ТОКЕНА"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    tokens = load_tokens()
    
    # Проверяем токен
    if user_id not in tokens:
        await query.edit_message_text(
            "❌ Токен не найден!",
            reply_markup=get_back_button()
        )
        return
    
    token = tokens[user_id]
    user_info = await get_vk_user_info(token)
    
    if not user_info:
        await query.edit_message_text(
            "❌ Недействительный токен!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить токен", callback_data="update_token")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
            ])
        )
        return
    
    # Остальной код функции остается прежним...
    keyboard = [
        [InlineKeyboardButton("🚀 Накрутить сообщения", callback_data="start_unread")],
        [InlineKeyboardButton("🔄 Обновить диалоги", callback_data="refresh_dialogs_main")],
        [InlineKeyboardButton("📊 Статистика диалогов", callback_data="dialogs_stats_main")],
        [InlineKeyboardButton("Назад к функциям", callback_data="vk_functions")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🔵 Накрутка непрочитанных сообщений VK\n\n"
        "📋 Как это работает:\n"
        "• Бот находит прочитанные диалоги (без синих точек)\n"
        "• Ставит на них синие кружки (непрочитанные)\n"
        "• Работает только с сообщениями, которые вы уже прочитали\n\n"
        "⚡ Функции кнопок:\n"
        "• 🚀 Накрутить сообщения - запуск накрутки\n"
        "• 🔄 Обновить диалоги - обновить список диалогов\n"
        "• 📊 Статистика - посмотреть текущую статистику\n\n"
        "⚠️ Безопасность: Лимит до 10,000 диалогов",
        reply_markup=reply_markup
    )

async def get_all_conversations(token: str) -> tuple:
    """Возвращает (conversations_list, total_count) для всех диалогов пользователя.

    Пагинация: использует `messages.getConversations` с `offset` и `count=200`.
    """
    conversations = []
    count = 200
    offset = 0

    try:
        async with aiohttp.ClientSession() as session:
            while True:
                params = {
                    'access_token': token,
                    'v': '5.199',
                    'extended': 1,
                    'count': count,
                    'offset': offset
                }
                async with session.get('https://api.vk.com/method/messages.getConversations', params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    data = await resp.json()

                if 'error' in data:
                    logger.error(f"Ошибка VK API while paging conversations: {data['error']}")
                    return None, 0

                resp_data = data.get('response', {})
                items = resp_data.get('items', [])
                total = resp_data.get('count', 0)

                conversations.extend(items)

                offset += len(items)
                if offset >= total or not items:
                    break

                # Небольшая пауза для устойчивости
                await asyncio.sleep(0.1)

        return conversations, total

    except Exception as e:
        logger.error(f"Ошибка при получении всех диалогов: {e}")
        return None, 0


async def get_conversations_stats_simple(token: str) -> tuple:
    """Точный подсчёт диалогов: возвращает (total_count, read_count, unread_count, analyzed_count).

    - `read_count`: количество диалогов с unread_count == 0
    - `unread_count`: количество диалогов с unread_count > 0
    - `analyzed_count`: реальное число загруженных диалогов (может отличаться от total при ошибках)
    """
    conversations, total = await get_all_conversations(token)
    if conversations is None:
        return 0, 0, 0, 0

    read_count = 0
    unread_count = 0

    for conv in conversations:
        try:
            conversation_info = conv.get('conversation', {})
            unread = conversation_info.get('unread_count', 0)
            if unread > 0:
                unread_count += 1
            else:
                read_count += 1
        except Exception:
            continue

    return total, read_count, unread_count, len(conversations)

async def refresh_dialogs_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновить диалоги - ПРОСТАЯ И НАДЕЖНАЯ ВЕРСИЯ"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    tokens = load_tokens()
    token = tokens.get(user_id)
    
    if not token:
        await query.edit_message_text(
            "❌ Токен не найден!",
            reply_markup=get_back_button()
        )
        return
    
    message = await query.edit_message_text(
        "🔄 Сканирую диалоги...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="unread_messages")]])
    )
    
    try:
        # Используем простой и надежный метод
        total_count, read_count, unread_count, analyzed_count = await get_conversations_stats_simple(token)
        
        # Получаем информацию о пользователе
        user_info = await get_vk_user_info(token)
        user_name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}" if user_info else "Неизвестно"
        
        # Создаем клавиатуру
        keyboard = []
        
        if read_count > 0:
            keyboard.append([InlineKeyboardButton("🚀 Накрутить сообщения", callback_data="start_unread")])
            status_text = f"🎯 Найдено {read_count} прочитанных диалогов!"
        else:
            status_text = "🎉 Все диалоги уже с синими точками!"
        
        keyboard.extend([
            [InlineKeyboardButton("🔄 Обновить еще раз", callback_data="refresh_dialogs_main")],
            [InlineKeyboardButton("Назад", callback_data="unread_messages")]
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message.edit_text(
            f"✅ Сканирование завершено!\n\n"
            f"📊 Статистика для {user_name}:\n"
            f"• Всего диалогов: {total_count}\n"
            f"• 🔵 С синими точками: {unread_count}\n"
            f"• ✅ Без синих точек: {read_count}\n"
            f"• 📊 Проанализировано: {analyzed_count}\n\n"
            f"{status_text}",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Ошибка при сканировании диалогов: {e}")
        await message.edit_text(
            f"❌ Ошибка при сканировании!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="unread_messages")]])
        )

async def start_unread_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запуск процесса накрутки непрочитанных сообщений"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    tokens = load_tokens()
    token = tokens.get(user_id)
    
    if not token:
        await query.edit_message_text(
            "❌ Токен не найден!",
            reply_markup=get_back_button()
        )
        return
    
    # Сначала проверяем актуальную статистику
    total_count, read_count, unread_count, analyzed_count = await get_conversations_stats_simple(token)
    
    if read_count == 0:
        await query.edit_message_text(
            "❌ Нет диалогов для накрутки!\n\n"
            "Все диалоги уже с синими точками. Зайдите в любой диалог чтобы прочитать сообщения, затем обновите статистику.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить диалоги", callback_data="refresh_dialogs_main")],
                [InlineKeyboardButton("Назад", callback_data="unread_messages")]
            ])
        )
        return
    
    # Запускаем основную функцию накрутки
    await mark_vk_conversations_unread(update, context, token)

async def mark_vk_conversations_unread(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str) -> bool:
    """Накрутка непрочитанных сообщений"""
    query = update.callback_query
    user_id = str(query.from_user.id)

    CANCEL_PROCESS[user_id] = False

    message = await query.edit_message_text(
        "⏳ Сканирую диалоги...",
        reply_markup=get_cancel_button()
    )

    start_time = time.time()

    # Получаем все диалоги через пагинацию
    conversations, total = await get_all_conversations(token)
    if conversations is None:
        await message.edit_text(
            "❌ Ошибка при получении списка диалогов.",
            reply_markup=get_back_button()
        )
        CANCEL_PROCESS.pop(user_id, None)
        return False

    # Собираем все peer_id диалогов без синих точек (unread_count == 0)
    dialogs_to_process = []
    for conv in conversations:
        if CANCEL_PROCESS.get(user_id, False):
            await message.edit_text(
                "❌ Накрутка отменена пользователем!",
                reply_markup=get_back_button()
            )
            CANCEL_PROCESS.pop(user_id, None)
            return False

        try:
            conversation_info = conv.get('conversation', {})
            unread_count_in_conv = conversation_info.get('unread_count', 0)
            if unread_count_in_conv == 0:
                peer_id = conversation_info.get('peer', {}).get('id')
                if peer_id:
                    dialogs_to_process.append(peer_id)
        except Exception:
            continue

    if not dialogs_to_process:
        await message.edit_text(
            f"❌ Нет прочитанных диалогов для накрутки!\n\n📊 Все диалоги уже синие.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить диалоги", callback_data="refresh_dialogs_main")],
                [InlineKeyboardButton("Назад", callback_data="unread_messages")]
            ])
        )
        CANCEL_PROCESS.pop(user_id, None)
        return False

    await message.edit_text(
        f"⏳ Начинаю накрутку...\n📊 Найдено прочитанных диалогов для обработки: {len(dialogs_to_process)}",
        reply_markup=get_cancel_button()
    )

    success_count = 0
    fail_count = 0

    # Используем aiohttp чтобы не блокировать event loop и позволить мгновенную отмену
    session_timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=session_timeout) as session:
        for i, peer_id in enumerate(dialogs_to_process):
            if CANCEL_PROCESS.get(user_id, False):
                await message.edit_text(
                    "❌ Накрутка отменена пользователем!",
                    reply_markup=get_back_button()
                )
                # Не удаляем флаг здесь — оставим это на завершение рабочего процесса
                return False

            # Попробуем выполнить markAsUnreadConversation с ретраями при rate-limit
            attempt_ok = False
            for attempt in range(3):
                try:
                    resp = await session.post(
                        'https://api.vk.com/method/messages.markAsUnreadConversation',
                        params={
                            'access_token': token,
                            'v': '5.199',
                            'peer_id': peer_id
                        }
                    )
                    data = await resp.json()

                    if 'error' not in data:
                        success_count += 1
                        attempt_ok = True
                        break
                    else:
                        err = data.get('error', {})
                        code = err.get('error_code')
                        # VK rate limit
                        if code == 6:
                            await asyncio.sleep(1.0 + attempt * 0.5)
                            continue
                        else:
                            # Other errors: log and stop retrying for this peer
                            break

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.debug(f"Ошибка при markAsUnreadConversation: {e}")
                    await asyncio.sleep(0.5)
                    continue

        if not attempt_ok:
            fail_count += 1

        # Обновляем прогресс каждые 10 или в конце
        if (i + 1) % 10 == 0 or (i + 1) == len(dialogs_to_process):
            progress = int(((i + 1) / len(dialogs_to_process)) * 100)
            await message.edit_text(
                f"⏳ Накрутка... {progress}% ({i + 1}/{len(dialogs_to_process)})\n✅ Успешно: {success_count} • ❌ Ошибок: {fail_count}",
                reply_markup=get_cancel_button()
            )

        # Небольшая пауза между запросами
        await asyncio.sleep(0.2)

    CANCEL_PROCESS.pop(user_id, None)

    await message.edit_text(
        f"✅ Накрутка завершена!\n\n📊 Результат:\n• Обработано: {len(dialogs_to_process)}\n• Успешно: {success_count}\n• Ошибок: {fail_count}\n⏱ Время: {time.time() - start_time:.1f} сек.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить диалоги", callback_data="refresh_dialogs_main")],
            [InlineKeyboardButton("Назад", callback_data="unread_messages")]
        ])
    )

    return True

async def show_dialogs_stats_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать детальную статистику с объяснением"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    tokens = load_tokens()
    token = tokens.get(user_id)
    
    if not token:
        await query.edit_message_text(
            "❌ Токен не найден!",
            reply_markup=get_back_button()
        )
        return
    
    message = await query.edit_message_text(
        "📊 Анализирую статистику...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="unread_messages")]])
    )
    
    try:
        total_count, read_count, unread_count, analyzed_count = await get_conversations_stats_simple(token)
        
        user_info = await get_vk_user_info(token)
        user_name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}" if user_info else "Неизвестно"
        
        # Создаем клавиатуру
        keyboard = []
        
        if read_count > 0:
            keyboard.append([InlineKeyboardButton("🚀 Накрутить сообщения", callback_data="start_unread")])
        
        keyboard.extend([
            [InlineKeyboardButton("🔄 Обновить статистику", callback_data="refresh_dialogs_main")],
            [InlineKeyboardButton("Назад", callback_data="unread_messages")]
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Объяснение статистики
        if analyzed_count > 0:
            unread_percent = (unread_count / analyzed_count) * 100
            read_percent = (read_count / analyzed_count) * 100
        else:
            unread_percent = read_percent = 0
        
        explanation = ""
        if read_count == 0:
            explanation = (
                "\n\n📝 Пояснение:\n"
                "• Все диалоги имеют синие точки (непрочитанные)\n"
                "• Чтобы появились прочитанные диалоги:\n"
                "  1. Зайдите в любой диалог\n"
                "  2. Прочитайте сообщения\n"
                "  3. Синие точки исчезнут\n"
                "  4. Затем обновите статистику"
            )
        else:
            explanation = (
                f"\n\n📝 Пояснение:\n"
                f"• {read_count} диалогов без синих точек\n"
                f"• Можно поставить на них синие точки\n"
                f"• Это создаст видимость непрочитанных сообщений"
            )
        
        await message.edit_text(
            f"📊 Детальная статистика\n\n"
            f"👤 Пользователь: {user_name}\n"
            f"📈 Общая статистика:\n"
            f"• Всего диалогов в VK: {total_count}\n"
            f"• Проанализировано: {analyzed_count}\n"
            f"• 🔵 С синими точками: {unread_count} ({unread_percent:.1f}%)\n"
            f"• ✅ Без синих точек: {read_count} ({read_percent:.1f}%)"
            f"{explanation}",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        await message.edit_text(
            "❌ Ошибка при получении статистики!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="unread_messages")]])
        )


async def show_account_limits_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает оставшиеся лимиты (фото и накрутка сообщений) для всех подключенных аккаунтов."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        reply_target = query
    else:
        reply_target = update.message

    tokens = load_tokens()
    if not tokens:
        await reply_target.reply_text("❌ Нет подключенных аккаунтов (токенов).")
        return

    lines = []
    total_accounts = len(tokens)
    
    for tg_user_id, vk_token in tokens.items():
        try:
            # Получаем информацию о пользователе VK
            user_info = await get_vk_user_info(vk_token)
            if not user_info:
                lines.append(f"👤 TG:{tg_user_id} • VK: ❌ Недействительный токен")
                continue

            vk_id = user_info.get('id', '—')
            vk_name = f"{user_info.get('first_name','')} {user_info.get('last_name','')}"
            
            # Получаем статистику диалогов для накрутки сообщений
            total, read, unread, analyzed = await get_conversations_stats_simple(vk_token)
            
            # Дневной лимит по фото
            today = datetime.now().date().isoformat()
            daily_info = USER_DAILY_LIMITS.get(tg_user_id, {})
            daily_used = daily_info.get('count', 0) if daily_info.get('date') == today else 0
            remaining_photos = max(0, 10000 - daily_used)
            
            # Проверяем валидность токена через простой запрос
            token_valid = "✅" if await check_token_validity(vk_token) else "❌"
            
            lines.append(
                f"👤 {token_valid} {vk_name} (ID:{vk_id})\n"
                f"   📸 Фото: {remaining_photos}/10000 (использовано: {daily_used})\n"
                f"   💬 Сообщения: {read} диалогов для накрутки\n"
                f"   🔗 TG ID: {tg_user_id}\n"
            )
            
        except Exception as e:
            logger.error(f"Ошибка при получении лимитов для {tg_user_id}: {e}")
            lines.append(f"👤 TG:{tg_user_id} • ❌ Ошибка получения данных")

    message_text = (
        f"📊 Лимиты подключенных аккаунтов ({total_accounts}):\n\n" + 
        "\n".join(lines) +
        f"\n💡 Лимиты учитываются по каждому подключенному аккаунту VK"
    )

    # Ограничим длину сообщения
    if len(message_text) > 3900:
        message_text = message_text[:3900] + "\n..."

    if update.callback_query:
        await reply_target.edit_message_text(message_text)
    else:
        await reply_target.reply_text(message_text)

async def check_token_validity(token: str) -> bool:
    """Проверяет валидность токена"""
    try:
        response = requests.get(
            'https://api.vk.com/method/users.get',
            params={
                'access_token': token,
                'v': '5.199'
            },
            timeout=5
        )
        data = response.json()
        return 'response' in data and len(data['response']) > 0
    except:
        return False

async def cancel_unread_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик отмены процесса накрутки"""
    query = update.callback_query
    # Незамедлительная отмена процесса накрутки
    try:
        await query.answer("🛑 Накрутка остановлена")
    except:
        pass

    user_id = str(query.from_user.id)
    CANCEL_PROCESS[user_id] = True

    # Обновляем сообщение интерфейса сразу
    try:
        await safe_edit_message(
            query.message,
            "🛑 <b>НАКРУТКА ОСТАНОВЛЕНА</b>\n\nВсе процессы прерваны по вашему запросу.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
            ])
        )
    except Exception:
        # Если не удалось отредактировать текущее сообщение, просто покажем меню
        await show_menu(update, context)

    # Флаг отмены оставляем до тех пор, пока рабочий процесс не завершит и сам не очистит его

# ========== ФУНКЦИИ ДЛЯ ФОТОГРАФИЙ ==========
async def show_photo_upload_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать информацию о накрутке фотографий с новым форматом"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    daily_used = await get_user_daily_photo_limit(user_id)
    remaining = 10000 - daily_used
    
    keyboard = [
        [InlineKeyboardButton("🚀 Начать накрутку фото", callback_data="start_photo_upload")],
        [InlineKeyboardButton("📊 Статистика лимитов", callback_data="photo_stats")],
        [InlineKeyboardButton("🔙 Назад к функциям", callback_data="vk_functions")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "📸 <b>ПРОДВИНУТАЯ НАКРУТКА ФОТОГРАФИЙ</b>\n\n"
        "🎯 <b>Два способа запуска:</b>\n\n"
        "1. <b>Одно сообщение:</b>\n"
        "   Отправьте фото + текст:\n"
        "   <code>ссылка количество название</code>\n\n"
        "2. <b>Два сообщения:</b>\n"
        "   • Сначала отправьте фото\n"
        "   • Затем введите данные\n\n"
        "💡 <b>Особенности названия:</b>\n"
        "• <b>Необязательно</b> - если не указать, фото будут БЕЗ подписей\n"
        "• <b>Указывается один раз</b> - для ВСЕХ фото\n"
        "• <b>Без номеров</b> - одинаковый текст для каждого фото\n\n"
        f"📊 <b>Лимиты:</b>\n"
        f"• Использовано сегодня: {daily_used} фото\n"
        f"• Осталось: {remaining} фото\n"
        f"• Максимум за раз: 1000 фото",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def get_user_daily_photo_limit(user_id: str) -> int:
    """Получить количество загруженных фото за сегодня"""
    today = datetime.now().date().isoformat()
    if user_id in USER_DAILY_LIMITS and USER_DAILY_LIMITS[user_id].get('date') == today:
        return USER_DAILY_LIMITS[user_id].get('count', 0)
    return 0

async def update_user_daily_photo_limit(user_id: str, count: int):
    """Обновить дневной лимит пользователя"""
    today = datetime.now().date().isoformat()
    if user_id not in USER_DAILY_LIMITS or USER_DAILY_LIMITS[user_id].get('date') != today:
        USER_DAILY_LIMITS[user_id] = {'date': today, 'count': 0}
    
    USER_DAILY_LIMITS[user_id]['count'] += count

async def start_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать процесс загрузки фотографий с защитой от дублирования"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    # 🔧 ПРОВЕРКА АКТИВНОГО ПРОЦЕССА
    if PHOTO_PROCESS.get(user_id):
        await query.answer("⚠️ Процесс уже запущен!", show_alert=True)
        return

    await query.answer()
        
    # 🔧 ОЧИСТКА СТАРЫХ ДАННЫХ ПЕРЕД НОВЫМ ПРОЦЕССОМ
    context.user_data.pop('waiting_for_photo_info', None)
    context.user_data.pop('waiting_for_photo_details', None)
    context.user_data.pop('pending_photo', None)
    context.user_data.pop('current_photo_count', None)
    
    # 🔧 УСТАНАВЛИВАЕМ СОСТОЯНИЕ ОЖИДАНИЯ ФОТО
    context.user_data['waiting_for_photo_info'] = True

    # Проверяем дневной лимит
    daily_used = await get_user_daily_photo_limit(user_id)
    if daily_used >= 10000:
        await query.edit_message_text(
            "❌ Достигнут дневной лимит в 10,000 фотографий!\n"
            "Попробуйте завтра.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Назад", callback_data="photo_upload")]
            ])
        )
        return
    
    remaining = 10000 - daily_used
    
    await query.edit_message_text(
        f"📸 Готов к загрузке фотографий!\n\n"
        f"📝 Отправьте ОДНО сообщение в формате:\n"
        f"<b>(ссылка на альбом) (количество)</b> + фото\n\n"
        f"📋 Пример сообщения:\n"
        f"<code>https://vk.com/album-12345678_123456789 100</code>\n"
        f"+ прикрепите фотографию из галереи\n\n"
        f"📊 Лимиты:\n"
        f"• Можно загрузить сегодня: {remaining} фото\n"
        f"• Максимум за раз: 1000 фото",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
        ])
    )

async def handle_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ИСПРАВЛЕННЫЙ обработчик загрузки фотографий с защитой от дублирования"""
    user_id = str(update.message.from_user.id)
    
    # 🔧 ПРОВЕРКА АКТИВНОГО ПРОЦЕССА
    if PHOTO_PROCESS.get(user_id):
        await update.message.reply_text(
            "⏳ <b>УЖЕ ИДЕТ ПРОЦЕСС НАКРУТКИ!</b>\n\n"
            "Дождитесь завершения текущей операции",
            parse_mode='HTML'
        )
        return
        
    if not context.user_data.get('waiting_for_photo_info'):
        logger.debug(f"🔍 DEBUG: Пользователь {user_id} не ожидает фото, игнорируем")
        return
    
    # 🔧 ФИКС: Проверяем токен
    tokens = load_tokens()
    token = tokens.get(user_id)
    if not token:
        await update.message.reply_text(
            "❌ Токен не найден! Сначала подключите токен VK.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Подключить токен", callback_data="connect")],
                [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
            ])
        )
        return

    # 🔧 ФИКС: Проверяем наличие фото
    if not update.message.photo:
        await update.message.reply_text(
            "❌ Нужно отправить сообщение с фотографией!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
            ])
        )
        return

    try:
        # 🔧 СНИМАЕМ ФЛАГ ОЖИДАНИЯ СРАЗУ
        context.user_data['waiting_for_photo_info'] = False

        # 🔧 ФИКС: Проверяем наличие текста команды
        if not update.message.caption:
            # Если нет текста - запрашиваем данные отдельно
            context.user_data['pending_photo'] = update.message.photo[-1]
            context.user_data['waiting_for_photo_details'] = True
            
            await update.message.reply_text(
                "📝 <b>ВВЕДИТЕ ДАННЫЕ ДЛЯ НАКРУТКИ</b>\n\n"
                "📋 <b>Формат:</b> <code>ссылка количество название</code>\n\n"
                "🎯 <b>Примеры:</b>\n"
                "• <code>https://vk.com/album-12345678_123456789 100</code>\n"
                "• <code>https://vk.com/album-12345678_123456789 50 Отпуск</code>\n\n"
                "💡 <b>Пояснение:</b>\n"
                "• <b>Ссылка</b> - на альбом VK\n"
                "• <b>Количество</b> - сколько фото загрузить\n"
                "• <b>Название</b> - подпись для фото (необязательно)\n\n"
                "⚠️ Если не укажете название - фото будут БЕЗ подписей",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return

        # Парсим команду из caption
        caption = update.message.caption.strip()
        parts = caption.split()
        
        if len(parts) < 2:
            await update.message.reply_text(
                "❌ <b>НЕДОСТАТОЧНО ДАННЫХ</b>\n\n"
                "📝 <b>Нужно как минимум:</b>\n"
                "• <b>Ссылка</b> на альбом\n"
                "• <b>Количество</b> фото\n\n"
                "🎯 <b>Полный формат:</b>\n"
                "<code>ссылка количество название</code>\n\n"
                "💡 <b>Название</b> - необязательный параметр\n\n"
                "📝 <b>Пример:</b>\n"
                "<code>https://vk.com/album-12345678_123456789 100</code>",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        album_url = parts[0]
        
        try:
            photo_count = int(parts[1])
        except ValueError:
            await update.message.reply_text(
                "❌ <b>НЕВЕРНОЕ КОЛИЧЕСТВО</b>\n\n"
                "Количество должно быть <b>числом</b>\n"
                "Пример: <code>50</code>",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        # Извлекаем название фото (все что после количества)
        photo_caption = ' '.join(parts[2:]) if len(parts) > 2 else None
        
        logger.debug(f"🔍 DEBUG: Альбом: {album_url}, Количество: {photo_count}, Название: {photo_caption}")
        
        # Проверяем лимиты
        daily_used = await get_user_daily_photo_limit(user_id)
        if daily_used >= 10000:
            await update.message.reply_text(
                "❌ Достигнут дневной лимит в 10,000 фотографий!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        if photo_count > 1000:
            await update.message.reply_text(
                "❌ Максимальное количество за раз - 1000 фото!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        if daily_used + photo_count > 10000:
            remaining = 10000 - daily_used
            await update.message.reply_text(
                f"❌ Превышен дневной лимит!\n"
                f"Можно загрузить еще: {remaining} фото",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        # 🔧 Проверяем альбом
        album_result = await extract_album_id(album_url, token, user_id)
        
        if album_result['status'] != 'success':
            await update.message.reply_text(
                album_result['message'],
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        album_id = album_result['album_id']
        album_title = album_result.get('album_title', 'альбом')
        
                # В функции handle_photo_upload ДО запуска process_photo_upload добавить:
        # 🔧 ПРОВЕРКА АЛЬБОМА ПЕРЕД НАЧАЛОМ
        album_check = await verify_album_before_upload(token, album_id)
        if not album_check:
            await update.message.reply_text(
                "❌ <b>ОШИБКА ДОСТУПА К АЛЬБОМУ</b>\n\n"
                "Проверьте:\n"
                "• Существует ли альбом\n"
                "• Есть ли у вас права на запись\n"
                "• Корректность ссылки на альбом",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Попробовать снова", callback_data="start_photo_upload")]
                ])
            )
            return
        
        # Запускаем процесс загрузки
        message = await update.message.reply_text(
            f"🎯 <b>ПОДТВЕРЖДЕНИЕ НАКРУТКИ</b>\n\n"
            f"📁 Альбом: {album_title}\n"
            f"🔢 Количество: {photo_count} фото\n"
            f"🏷️ Название: {photo_caption or 'БЕЗ НАЗВАНИЯ'}\n\n"
            f"🚀 <i>Подготавливаю процесс...</i>",
            parse_mode='HTML',
            reply_markup=get_photo_cancel_button()
        )
        
        await process_photo_upload(update, context, token, album_id, photo_count, update.message.photo[-1], message, photo_caption)
        
    except Exception as e:
        logger.error(f"Ошибка в handle_photo_upload: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при обработке запроса",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
            ])
        )

async def safe_process_cleanup(user_id: str, context: ContextTypes.DEFAULT_TYPE = None):
    """Безопасная очистка всех флагов процесса"""
    async with PROCESS_LOCK:
        # Очищаем все флаги
        ACTIVE_USER_PROCESSES.pop(user_id, None)
        CANCEL_FLAGS.pop(user_id, None)
        PHOTO_PROCESS.pop(user_id, None)
        
        if context:
            context.user_data.pop('waiting_for_photo_info', None)
            context.user_data.pop('waiting_for_photo_details', None)
            context.user_data.pop('pending_photo', None)
            context.user_data.pop('current_photo_count', None)
    
    logger.info(f"🔧 Завершена очистка процессов для {user_id}")

async def handle_photo_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ИСПРАВЛЕННЫЙ обработчик ввода данных после отправки фото - С ДВОЙНОЙ ЗАЩИТОЙ"""
    user_id = str(update.message.from_user.id)
    
    # 🔧 ФИКС: ДВОЙНАЯ ПРОВЕРКА НА АКТИВНЫЙ ПРОЦЕСС
    if PHOTO_PROCESS.get(user_id) or MESSAGE_PROCESSING.get(user_id):
        await update.message.reply_text(
            "⏳ <b>Уже идет процесс накрутки!</b>\n\n"
            "Дождитесь завершения текущей операции",
            parse_mode='HTML'
        )
        return
        
    if not context.user_data.get('waiting_for_photo_details'):
        logger.debug(f"🔍 DEBUG: Пользователь {user_id} не ожидает детали фото, игнорируем")
        return
    
    # 🔧 ФИКС: Устанавливаем флаг обработки сообщения
    MESSAGE_PROCESSING[user_id] = True
    
    # Проверяем токен пользователя
    tokens = load_tokens()
    token = tokens.get(user_id)
    
    if not token:
        await update.message.reply_text(
            "❌ Токен не найден!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
            ])
        )
        return
    
    try:
        text = update.message.text.strip()
        parts = text.split()
        
        if len(parts) < 2:
            await update.message.reply_text(
                "❌ <b>НЕДОСТАТОЧНО ДАННЫХ</b>\n\n"
                "📝 <b>Нужно как минимум:</b>\n"
                "• <b>Ссылка</b> на альбом\n"
                "• <b>Количество</b> фото\n\n"
                "🎯 <b>Формат:</b> <code>ссылка количество название</code>",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        album_url = parts[0]
        
        try:
            photo_count = int(parts[1])
        except ValueError:
            await update.message.reply_text(
                "❌ <b>НЕВЕРНОЕ КОЛИЧЕСТВО</b>\n\n"
                "Количество должно быть <b>числом</b>",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        # Извлекаем название фото (все что после количества)
        photo_caption = ' '.join(parts[2:]) if len(parts) > 2 else None
        
        # Проверяем лимиты
        daily_used = await get_user_daily_photo_limit(user_id)
        if daily_used >= 10000:
            await update.message.reply_text(
                "❌ Достигнут дневной лимит в 10,000 фотографий!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        if photo_count > 1000:
            await update.message.reply_text(
                "❌ Максимальное количество за раз - 1000 фото!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        if daily_used + photo_count > 10000:
            remaining = 10000 - daily_used
            await update.message.reply_text(
                f"❌ Превышен дневной лимит!\n"
                f"Можно загрузить еще: {remaining} фото",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        # 🔧 Проверяем альбом
        album_result = await extract_album_id(album_url, token, user_id)
        
        if album_result['status'] != 'success':
            await update.message.reply_text(
                album_result['message'],
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        album_id = album_result['album_id']
        album_title = album_result.get('album_title', 'альбом')
        
        # 🔧 Проверяем upload access
        upload_check = await verify_album_upload_access(token, album_id)
        
        if upload_check['status'] != 'success':
            await update.message.reply_text(
                f"❌ <b>ОШИБКА ДОСТУПА</b>\n\n{upload_check['message']}",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Попробовать снова", callback_data="start_photo_upload")],
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        # Запускаем процесс загрузки
        context.user_data['waiting_for_photo_details'] = False
        photo = context.user_data.pop('pending_photo', None)
        
        if not photo:
            await update.message.reply_text(
                "❌ Ошибка: фото не найдено",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
                ])
            )
            return
        
        message = await update.message.reply_text(
            f"🎯 <b>ПОДТВЕРЖДЕНИЕ НАКРУТКИ</b>\n\n"
            f"📁 Альбом: {album_title}\n"
            f"🔢 Количество: {photo_count} фото\n"
            f"🏷️ Название: {photo_caption or 'БЕЗ НАЗВАНИЯ'}\n\n"
            f"🚀 <i>Подготавливаю процесс...</i>",
            parse_mode='HTML',
            reply_markup=get_photo_cancel_button()
        )
        
        await process_photo_upload(update, context, token, album_id, photo_count, photo, message, photo_caption)
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(
            "❌ <b>НЕОЖИДАННАЯ ОШИБКА</b>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="photo_upload")]
            ])
        )

async def extract_album_id(album_url: str, token: str, user_id: str) -> dict:
    """Исправленный парсинг альбома с ТОЧНОЙ проверкой существования"""
    try:
        logger.debug(f"🔍 DEBUG: Начинаем парсинг: {album_url}")
        
        # Получаем информацию о текущем пользователе
        current_user_info = await get_vk_user_info(token)
        if not current_user_info:
            return {"status": "error", "message": "❌ Не удалось получить информацию о профиле VK"}
            
        current_user_id = current_user_info.get('id')
        logger.debug(f"🔍 DEBUG: Пользователь VK ID: {current_user_id}")
        
        # Очищаем URL
        clean_url = album_url.strip().split('?')[0].rstrip('/')
        logger.debug(f"🔍 DEBUG: Очищенный URL: {clean_url}")
        
        # ПАРСИНГ разных форматов ссылок VK
        album_id = None
        owner_id = None
        
        # Формат 1: https://vk.com/album191451023_311364753
        if 'vk.com/album' in clean_url and '_' in clean_url:
            try:
                # Извлекаем часть после 'album'
                album_part = clean_url.split('album')[-1]
                if '_' in album_part:
                    parts = album_part.split('_')
                    if len(parts) >= 2:
                        owner_id = parts[0]
                        album_id = parts[1].split('/')[0].split('?')[0]
                        logger.debug(f"🔍 DEBUG: Формат 1 - owner: {owner_id}, album: {album_id}")
            except Exception as e:
                logger.debug(f"🔍 DEBUG: Ошибка парсинга формата 1: {e}")
        
        # Формат 2: https://vk.com/albums191451023?z=photo191451023_457239017%2Falbum191451023_0
        if not album_id and 'albums' in clean_url:
            try:
                import urllib.parse
                parsed_url = urllib.parse.urlparse(clean_url)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                
                if 'z' in query_params:
                    z_param = query_params['z'][0]
                    if 'album' in z_param:
                        album_part = z_param.split('album')[-1]
                        if '_' in album_part:
                            album_id = album_part.split('_')[1].split('%')[0]
                            owner_id = album_part.split('_')[0]
                            logger.debug(f"🔍 DEBUG: Формат 2 - owner: {owner_id}, album: {album_id}")
            except Exception as e:
                logger.debug(f"🔍 DEBUG: Ошибка парсинга формата 2: {e}")
        
        # Формат 3: Просто ID альбома (число)
        if not album_id and clean_url.isdigit():
            album_id = clean_url
            owner_id = current_user_id
            logger.debug(f"🔍 DEBUG: Формат 3 - прямой ID: {album_id}")
        
        # Если не удалось распарсить, пробуем извлечь цифры
        if not album_id:
            import re
            numbers = re.findall(r'\d+', clean_url)
            if len(numbers) >= 2:
                owner_id = numbers[0]
                album_id = numbers[1]
                logger.debug(f"🔍 DEBUG: Найдены цифры - owner: {owner_id}, album: {album_id}")
            elif len(numbers) == 1:
                album_id = numbers[0]
                owner_id = current_user_id
                logger.debug(f"🔍 DEBUG: Одна цифра - album: {album_id}")
        
        logger.debug(f"🔍 DEBUG: Результат парсинга - album_id: {album_id}, owner_id: {owner_id}")
        
        if not album_id:
            return {
                "status": "error", 
                "message": "❌ Не удалось определить ID альбома из ссылки\n\n"
                          "📝 Пример правильной ссылки:\n"
                          "• https://vk.com/album123456789_123456789\n"
                          "• https://vk.com/albums123456789?z=photo..."
            }
        
        # 🔧 Убедимся что album_id состоит ТОЛЬКО из цифр
        if not album_id.isdigit():
            clean_album_id = ''.join(filter(str.isdigit, album_id))
            logger.debug(f"🔍 DEBUG: Очистка album_id: {album_id} -> {clean_album_id}")
            album_id = clean_album_id
        
        # 🔧 КРИТИЧЕСКИЙ ФИКС: ТОЧНАЯ проверка существования альбома
        return await verify_album_exists_and_accessible(album_id, token, current_user_id)
            
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке альбома: {e}")
        return {
            "status": "error", 
            "message": f"❌ Ошибка при проверке альбома: {str(e)}"
        }

async def verify_album_exists_and_accessible(album_id: str, token: str, current_user_id: int) -> dict:
    """ТОЧНАЯ проверка что альбом существует и доступен для загрузки"""
    try:
        logger.debug(f"🔍 DEBUG: Точная проверка альбома ID: {album_id} для пользователя {current_user_id}")
        
        # ШАГ 1: Проверяем существование альбома через photos.getAlbums
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,
                'need_system': 0  # Исключаем системные альбомы
            },
            timeout=10
        )
        data = response.json()
        
        logger.debug(f"🔍 DEBUG: Ответ photos.getAlbums: {data}")
        
        if 'response' in data and data['response']['count'] > 0:
            album = data['response']['items'][0]
            album_owner_id = album.get('owner_id')
            album_title = album.get('title', 'Без названия')
            album_real_id = str(album.get('id'))
            album_size = album.get('size', 0)
            
            logger.debug(f"🔍 DEBUG: Найден альбом: '{album_title}', Владелец: {album_owner_id}, ID: {album_real_id}, Размер: {album_size}")
            
            # Проверяем что альбом принадлежит текущему пользователю
            if str(album_owner_id) != str(current_user_id):
                owner_info = await get_vk_user_info_by_id(album_owner_id, token)
                owner_name = owner_info.get('name', f'ID {album_owner_id}') if owner_info else f'ID {album_owner_id}'
                
                return {
                    "status": "not_owner",
                    "message": f"❌ Альбом принадлежит другому пользователю: {owner_name}\n\n"
                              f"Вы можете загружать фото только в СВОИ альбомы!"
                }
            
            # ШАГ 2: Проверяем что можно загружать в этот альбом
            upload_check = await verify_album_upload_access(token, album_real_id)
            if upload_check['status'] != 'success':
                return {
                    "status": "no_access",
                    "message": f"❌ Нет прав для загрузки в альбом '{album_title}'!\n\n"
                              f"Причина: {upload_check['message']}\n\n"
                              f"Проверьте настройки приватности альбома в VK."
                }
            
            # ШАГ 3: Дополнительная проверка - получаем информацию о фото в альбоме
            photos_check = await verify_album_has_photos(token, album_real_id, album_owner_id)
            if not photos_check:
                logger.warning(f"⚠️ Альбом {album_real_id} существует, но не содержит фото (возможно скрытый или системный)")
            
            logger.debug(f"✅ DEBUG: Альбом ВАЛИДЕН: {album_real_id} - '{album_title}'")
            return {
                "status": "success",
                "album_id": album_real_id,
                "album_title": album_title,
                "owner_id": album_owner_id,
                "size": album_size
            }
        
        # Если альбом не найден через прямой запрос, проверяем все альбомы пользователя
        logger.debug(f"🔍 DEBUG: Прямой запрос не удался, проверяем все альбомы пользователя...")
        
        response2 = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'owner_id': current_user_id,
                'need_system': 0
            },
            timeout=10
        )
        data2 = response2.json()
        
        if 'response' in data2 and data2['response']['count'] > 0:
            albums = data2['response']['items']
            logger.debug(f"🔍 DEBUG: Найдено альбомов пользователя: {len(albums)}")
            
            # Ищем альбом по ID среди всех альбомов пользователя
            for album in albums:
                album_real_id = str(album.get('id'))
                
                if album_real_id == album_id:
                    album_title = album.get('title', 'Без названия')
                    album_size = album.get('size', 0)
                    
                    # Проверяем доступ для загрузки
                    upload_check = await verify_album_upload_access(token, album_real_id)
                    if upload_check['status'] == 'success':
                        logger.debug(f"✅ DEBUG: Найден реальный альбом: {album_real_id}")
                        return {
                            "status": "success", 
                            "album_id": album_real_id,
                            "album_title": album_title,
                            "owner_id": current_user_id,
                            "size": album_size
                        }
            
            # Альбом не найден, показываем доступные альбомы
            available_albums = "\n".join([f"• {a['title']} (ID: {a['id']}) - {a.get('size', 0)} фото" for a in albums[:5]])
            return {
                "status": "error",
                "message": f"❌ Альбом ID {album_id} не найден среди ваших альбомов!\n\n"
                          f"📁 Ваши доступные альбомы:\n{available_albums}"
            }
        
        return {
            "status": "error",
            "message": "❌ Альбом не найден или недоступен!\n\n"
                      "Возможные причины:\n"
                      "• Альбом не существует\n"
                      "• Альбом удален\n" 
                      "• У вас нет прав доступа\n"
                      "• Это системный альбом VK"
        }
            
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке альбома: {e}")
        return {
            "status": "error", 
            "message": f"❌ Ошибка при проверке альбома: {str(e)}"
        }
    
async def verify_album_has_photos(token: str, album_id: str, owner_id: str) -> bool:
    """Проверяет что альбом содержит фото (дополнительная проверка существования)"""
    try:
        response = requests.get(
            'https://api.vk.com/method/photos.get',
            params={
                'access_token': token,
                'v': '5.199',
                'owner_id': owner_id,
                'album_id': album_id,
                'count': 1
            },
            timeout=5
        )
        data = response.json()
        return 'response' in data and data['response']['count'] >= 0
    except:
        return False

async def find_real_album(album_id: str, token: str, user_id: int) -> dict:
    """Находит реальный альбом пользователя, избегая системных"""
    try:
        logger.debug(f"🔍 DEBUG: Ищем реальный альбом ID: {album_id} для пользователя {user_id}")
        
        # 🔧 МЕТОД 1: Прямой запрос альбома (может вернуть системный)
        response1 = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,
                'need_system': 0  # 🔥 ВАЖНО: 0 чтобы исключить системные!
            },
            timeout=10
        )
        data1 = response1.json()
        
        logger.debug(f"🔍 DEBUG: Прямой запрос (need_system=0): {data1}")
        
        if 'response' in data1 and data1['response']['count'] > 0:
            album = data1['response']['items'][0]
            album_real_id = str(album.get('id'))
            album_title = album.get('title', 'Без названия')
            
            logger.debug(f"🔍 DEBUG: Найден реальный альбом: '{album_title}' (ID: {album_real_id})")
            
            # Проверяем что это НЕ системный альбом
            if album_real_id.startswith('-'):
                logger.debug(f"🔍 DEBUG: Обнаружен системный альбом: {album_real_id}")
                return {
                    "status": "system_album",
                    "message": f"❌ Обнаружен системный альбом: {album_title}"
                }
            
            return {
                "status": "success",
                "album_id": album_real_id,
                "album_title": album_title
            }
        
        # 🔧 МЕТОД 2: Получаем ВСЕ альбомы пользователя и ищем нужный
        logger.debug(f"🔍 DEBUG: Прямой запрос не удался, получаем все альбомы...")
        
        response2 = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'owner_id': user_id,
                'need_system': 0  # 🔥 Только пользовательские альбомы
            },
            timeout=10
        )
        data2 = response2.json()
        
        logger.debug(f"🔍 DEBUG: Все альбомы пользователя: {data2}")
        
        if 'response' in data2 and data2['response']['count'] > 0:
            albums = data2['response']['items']
            logger.debug(f"🔍 DEBUG: Найдено альбомов: {len(albums)}")
            
            # Ищем альбом по ID
            for album in albums:
                album_real_id = str(album.get('id'))
                album_title = album.get('title', 'Без названия')
                
                logger.debug(f"🔍 DEBUG: Проверяем альбом: {album_real_id} - '{album_title}'")
                
                if album_real_id == album_id:
                    logger.debug(f"✅ DEBUG: Найден реальный альбом: {album_real_id}")
                    return {
                        "status": "success", 
                        "album_id": album_real_id,
                        "album_title": album_title
                    }
            
            # Альбом не найден, показываем какие есть
            if albums:
                available_albums = "\n".join([f"• {a['title']} (ID: {a['id']})" for a in albums[:3]])
                return {
                    "status": "error",
                    "message": f"❌ Альбом ID {album_id} не найден\n\nВаши альбомы:\n{available_albums}"
                }
        
        return {
            "status": "error",
            "message": "❌ Альбом не найден или недоступен"
        }
            
    except Exception as e:
        logger.debug(f"🔍 DEBUG: Ошибка поиска альбома: {e}")
        return {
            "status": "error", 
            "message": "❌ Ошибка при поиске альбома"
        }
    
async def verify_album_upload_access(token: str, album_id: str) -> dict:
    """Проверяет что альбом существует и доступен для загрузки"""
    try:
        logger.debug(f"🔍 DEBUG: Проверяем upload access для альбома: {album_id}")
        
        response = requests.post(
            'https://api.vk.com/method/photos.getUploadServer',
            params={
                'access_token': token,
                'v': '5.199',
                'album_id': album_id
            },
            timeout=10
        )
        data = response.json()
        
        logger.debug(f"🔍 DEBUG: Ответ upload server: {data}")
        
        if 'error' in data:
            error_msg = data['error'].get('error_msg', 'Unknown error')
            return {
                "status": "error",
                "message": f"Ошибка VK: {error_msg}"
            }
        
        return {
            "status": "success",
            "upload_url": data['response']['upload_url']
        }
        
    except Exception as e:
        logger.debug(f"🔍 DEBUG: Ошибка проверки upload: {e}")
        return {
            "status": "error",
            "message": f"Ошибка соединения: {str(e)}"
        }

def get_photo_cancel_button():
    """Кнопка отмены загрузки фото"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 ОСТАНОВИТЬ НАКРУТКУ", callback_data="universal_cancel")]
    ])
    
async def get_user_albums_fallback(token: str, user_id: int, target_album_id: str) -> dict:
    """Альтернативный метод - получаем все альбомы пользователя"""
    try:
        logger.debug(f"🔍 DEBUG: Fallback - получаем все альбомы пользователя {user_id}")
        
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'owner_id': user_id,
                'need_system': 0
            },
            timeout=10
        )
        data = response.json()
        
        logger.debug(f"🔍 DEBUG: Fallback ответ: {data}")
        
        if 'response' in data and data['response']['count'] > 0:
            albums = data['response']['items']
            logger.debug(f"🔍 DEBUG: Найдено альбомов: {len(albums)}")
            
            for i, album in enumerate(albums):
                album_real_id = str(album.get('id'))
                album_title = album.get('title', 'Без названия')
                logger.debug(f"🔍 DEBUG: Альбом {i+1}: ID={album_real_id}, Название='{album_title}'")
                
                if album_real_id == target_album_id:
                    logger.debug(f"✅ DEBUG: Найден альбом в fallback: {album_real_id}")
                    return {
                        "status": "success",
                        "album_id": album_real_id,
                        "album_title": album_title
                    }
            
            # Если альбом не найден, показываем какие альбомы есть
            album_list = "\n".join([f"• {a.get('title')} (ID: {a.get('id')})" for a in albums[:5]])
            return {
                "status": "error", 
                "message": f"❌ Альбом ID {target_album_id} не найден\n\nВаши альбомы:\n{album_list}"
            }
        else:
            return {
                "status": "error", 
                "message": "❌ У вас нет доступных альбомов"
            }
            
    except Exception as e:
        logger.debug(f"🔍 DEBUG: Ошибка fallback: {e}")
        return {
            "status": "error", 
            "message": "❌ Ошибка при получении списка альбомов"
        }

async def get_vk_user_info_by_id(user_id: str, token: str) -> dict:
    """Получает информацию о пользователе VK по ID"""
    try:
        response = requests.get(
            'https://api.vk.com/method/users.get',
            params={
                'access_token': token,
                'v': '5.199',
                'user_ids': user_id,
                'fields': 'first_name,last_name,domain'
            },
            timeout=5
        )
        data = response.json()
        
        if 'response' in data and len(data['response']) > 0:
            user = data['response'][0]
            return {
                'name': f"{user.get('first_name', '')} {user.get('last_name', '')}",
                'domain': user.get('domain', f"id{user_id}")
            }
        return {}
    except:
        return {}
    
async def simple_album_check(album_id: str, token: str, current_user_id: int, expected_owner_id: str = None) -> str:
    """Простая проверка альбома"""
    try:
        logger.debug(f"DEBUG: Проверяем альбом ID: {album_id}, ожидаемый владелец: {expected_owner_id}")
        
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,  # Передаем ТОЛЬКО числовой ID
                'need_system': 1
            },
            timeout=10
        )
        data = response.json()
        
        logger.debug(f"DEBUG: Ответ API: {data}")
        
        if 'response' in data and data['response']['count'] > 0:
            album = data['response']['items'][0]
            album_owner_id = album.get('owner_id')
            album_title = album.get('title', 'Unknown')
            album_real_id = str(album.get('id'))
            
            logger.debug(f"DEBUG: Найден альбом: '{album_title}', Владелец: {album_owner_id}, ID: {album_real_id}")
            
            # Проверяем принадлежность пользователю
            if str(album_owner_id) != str(current_user_id) and str(album_owner_id) != str(-current_user_id):
                logger.debug(f"DEBUG: Альбом принадлежит другому пользователю: {album_owner_id} != {current_user_id}")
                return "not_owner"
            
            # ТОЛЬКО явные системные альбомы
            system_ids = ['-6', '-7', '-15']
            if album_real_id in system_ids:
                logger.debug(f"DEBUG: Системный альбом по ID: {album_real_id}")
                return "system_album"
            
            logger.debug(f"DEBUG: Альбом ВАЛИДЕН: {album_real_id}")
            return album_real_id
        else:
            logger.debug(f"DEBUG: Альбом не найден в API")
            return None
            
    except Exception as e:
        logger.debug(f"DEBUG: Ошибка проверки альбома: {e}")
        return None
    
async def test_album_parsing():
    """Функция для тестирования парсинга разных форматов ссылок"""
    test_urls = [
        "https://vk.com/album708740556_310377517",
        "https://vk.com/album-708740556_310377517", 
        "album708740556_310377517",
        "708740556_310377517",
        "310377517"
    ]
    
    for url in test_urls:
        logger.debug(f"\n=== Тестируем: {url} ===")
        # Имитируем вызов extract_album_id
        # Эта функция поможет понять какой формат ссылки у тебя

async def verify_album_exists_and_valid(album_id: str, token: str, current_user_id: int) -> str:
    """Проверяет существование альбома и что он НЕ системный"""
    try:
        # СНАЧАЛА проверяем что альбом существует и получаем его реальные данные
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,
                'need_system': 1  # Включаем системные чтобы их отсеять
            },
            timeout=10
        )
        data = response.json()
        
        logger.debug(f"DEBUG: Ответ photos.getAlbums: {data}")
        
        if 'response' in data and data['response']['count'] > 0:
            album = data['response']['items'][0]
            album_owner_id = album.get('owner_id')
            album_title = album.get('title', 'Unknown')
            album_real_id = album.get('id')
            album_size = album.get('size', 0)
            album_thumb = album.get('thumb', {})
            
            logger.debug(f"DEBUG: Найден альбом: {album_title}, Владелец: {album_owner_id}, Реальный ID: {album_real_id}, Размер: {album_size}")
            
            # Проверяем, что альбом принадлежит текущему пользователю
            if not await is_album_owned_by_user(str(album_owner_id), current_user_id):
                logger.debug(f"DEBUG: Альбом принадлежит другому пользователю: {album_owner_id}")
                return "not_owner"
            
            # ЖЕСТКАЯ ПРОВЕРКА СИСТЕМНЫХ АЛЬБОМОВ
            system_album_titles = ['стен', 'wall', 'profile', 'saved', 'tagged', 'отметк', 'сохранен']
            album_title_lower = album_title.lower()
            
            if any(keyword in album_title_lower for keyword in system_album_titles):
                logger.debug(f"DEBUG: Обнаружен системный альбом по названию: {album_title}")
                return "system_album"
            
            # Проверяем по ID системных альбомов
            system_album_ids = [-6, -7, -15, -9000]
            if album_real_id in system_album_ids:
                logger.debug(f"DEBUG: Обнаружен системный альбом по реальному ID: {album_real_id}")
                return "system_album"
            
            # Проверяем что у альбома есть обложка (у системных часто нет)
            if not album_thumb:
                logger.debug(f"DEBUG: У альбома нет обложки, возможно системный")
                return "system_album"
                
            logger.debug(f"DEBUG: Альбом {album_id} ВАЛИДНЫЙ и принадлежит пользователю")
            return str(album_real_id)
        else:
            logger.debug(f"DEBUG: Альбом {album_id} не существует или недоступен")
            return None
            
    except Exception as e:
        logger.debug(f"DEBUG: Ошибка проверки альбома: {e}")
        return None
    
async def is_system_album(album_id: str) -> bool:
    """Проверяет, является ли альбом системным"""
    try:
        system_albums = [
            '-6',   # wall - Фото на стене
            '-7',   # saved - Сохраненные фото
            '-15',  # tag - Отметки на фото
            '-9000' # profile - Фото профиля (устаревшее)
        ]
        
        # Также проверяем отрицательные ID
        if album_id.startswith('-'):
            return True
            
        return album_id in system_albums
        
    except:
        return False
    
async def verify_user_album_exists(album_id: str, token: str, current_user_id: int) -> str:
    """Проверяет существование альбома, его принадлежность и что он НЕ системный"""
    try:
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,
                'need_system': 0  # ИСКЛЮЧАЕМ системные альбомы
            },
            timeout=10
        )
        data = response.json()
        
        logger.debug(f"DEBUG: Ответ photos.getAlbums: {data}")
        
        if 'response' in data and data['response']['count'] > 0:
            album = data['response']['items'][0]
            album_owner_id = album.get('owner_id')
            album_title = album.get('title', 'Unknown')
            album_real_id = album.get('id')
            
            logger.debug(f"DEBUG: Найден альбом: {album_title}, Владелец: {album_owner_id}, Реальный ID: {album_real_id}")
            
            # Проверяем, что альбом принадлежит текущему пользователю
            if not await is_album_owned_by_user(str(album_owner_id), current_user_id):
                logger.debug(f"DEBUG: Альбом принадлежит другому пользователю: {album_owner_id}")
                return "not_owner"
            
            # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: убеждаемся что это реальный альбом
            if await is_system_album(str(album_real_id)):
                logger.debug(f"DEBUG: Альбом системный по реальному ID: {album_real_id}")
                return "system_album"
                
            logger.debug(f"DEBUG: Альбом {album_id} существует и принадлежит пользователю")
            return str(album_real_id)
        else:
            logger.debug(f"DEBUG: Альбом {album_id} не существует или недоступен")
            return None
            
    except Exception as e:
        logger.debug(f"DEBUG: Ошибка проверки альбома: {e}")
        return None
    
async def verify_album_ownership(album_id: str, token: str, current_user_id: int) -> str:
    """Проверяет существование альбома и его принадлежность"""
    try:
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,
                'need_system': 1
            },
            timeout=10
        )
        data = response.json()
        
        logger.debug(f"DEBUG: Ответ photos.getAlbums: {data}")
        
        if 'response' in data and data['response']['count'] > 0:
            album = data['response']['items'][0]
            album_owner_id = album.get('owner_id')
            
            logger.debug(f"DEBUG: Владелец альбома: {album_owner_id}, Текущий пользователь: {current_user_id}")
            
            # Проверяем, что альбом принадлежит текущему пользователю
            if str(album_owner_id) != str(current_user_id) and str(album_owner_id) != str(-current_user_id):
                logger.debug(f"DEBUG: Альбом принадлежит другому пользователю: {album_owner_id}")
                return "not_owner"
                
            logger.debug(f"DEBUG: Альбом {album_id} существует и принадлежит пользователю")
            return album_id
        else:
            logger.debug(f"DEBUG: Альбом {album_id} не существует или недоступен")
            return None
            
    except Exception as e:
        logger.debug(f"DEBUG: Ошибка проверки альбома: {e}")
        return None
    
async def is_album_owned_by_user(owner_id_from_url: str, current_user_id: int) -> bool:
    """Проверяет, принадлежит ли альбом текущему пользователю"""
    try:
        # Преобразуем owner_id из строки в число
        owner_id = int(owner_id_from_url)
        current_id = int(current_user_id)
        
        # Альбом принадлежит пользователю если:
        # 1. owner_id равен current_user_id
        # 2. owner_id равен -current_user_id (для групп)
        return owner_id == current_id or owner_id == -current_id
        
    except ValueError:
        return False
    
async def verify_user_album(album_id: str, token: str, current_user_id: int) -> str:
    """Проверяет существование альбома и его принадлежность пользователю"""
    try:
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,
                'need_system': 0  # ИСКЛЮЧАЕМ системные альбомы
            },
            timeout=10
        )
        data = response.json()
        
        logger.debug(f"DEBUG: Ответ photos.getAlbums: {data}")
        
        if 'response' in data and data['response']['count'] > 0:
            album = data['response']['items'][0]
            album_owner_id = album.get('owner_id')
            album_title = album.get('title', 'Unknown')
            
            logger.debug(f"DEBUG: Найден альбом: {album_title}, Владелец: {album_owner_id}")
            
            # Проверяем, что альбом принадлежит текущему пользователю
            if not await is_album_owned_by_user(str(album_owner_id), current_user_id):
                logger.debug(f"DEBUG: Альбом принадлежит другому пользователю: {album_owner_id}")
                return "not_owner"
            
            # Проверяем что это НЕ системный альбом
            album_type = album.get('id', 0)
            if album_type in [-6, -7, -15, -9000]:  # Системные альбомы
                logger.debug(f"DEBUG: Обнаружен системный альбом: {album_title}")
                return "system_album"
                
            logger.debug(f"DEBUG: Альбом {album_id} существует и принадлежит пользователю")
            return album_id
        else:
            logger.debug(f"DEBUG: Альбом {album_id} не существует или недоступен")
            return None
            
    except Exception as e:
        logger.debug(f"DEBUG: Ошибка проверки альбома: {e}")
        return None
    
async def find_user_available_album(token: str, user_id: int) -> str:
    """Находит доступный для записи альбом текущего пользователя (ИСКЛЮЧАЯ системные)"""
    try:
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'need_system': 0  # ИСКЛЮЧАЕМ системные альбомы
            },
            timeout=10
        )
        data = response.json()
        
        if 'response' in data:
            albums = data['response'].get('items', [])
            logger.debug(f"DEBUG: Найдено пользовательских альбомов: {len(albums)}")
            
            # Ищем первый доступный альбом пользователя
            for album in albums:
                album_owner_id = album.get('owner_id')
                album_id = str(album['id'])
                album_title = album.get('title', 'Unknown')
                
                # Проверяем, что альбом принадлежит текущему пользователю
                if not await is_album_owned_by_user(str(album_owner_id), user_id):
                    continue
                    
                logger.debug(f"DEBUG: Проверяем альбом пользователя: {album_title} (id: {album_id})")
                
                # Проверяем можно ли загружать в этот альбом
                if await can_upload_to_album(album_id, token):
                    logger.debug(f"DEBUG: Используем альбом пользователя: {album_id}")
                    return album_id
            
        logger.debug("DEBUG: Не найден доступный альбом пользователя для загрузки")
        return None
        
    except Exception as e:
        logger.debug(f"DEBUG: Ошибка поиска альбома: {e}")
        return None

async def verify_album_exists(album_id: str, token: str) -> str:
    """Проверяет существование альбома"""
    try:
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'album_ids': album_id,
                'need_system': 1
            },
            timeout=10
        )
        data = response.json()
        
        if 'response' in data and data['response']['count'] > 0:
            logger.debug(f"DEBUG: Альбом {album_id} существует и доступен")
            return album_id
        else:
            logger.debug(f"DEBUG: Альбом {album_id} не существует или недоступен")
            return None
            
    except Exception as e:
        logger.debug(f"DEBUG: Ошибка проверки альбома: {e}")
        return None

async def find_available_album(token: str) -> str:
    """Находит доступный для записи альбом"""
    try:
        response = requests.get(
            'https://api.vk.com/method/photos.getAlbums',
            params={
                'access_token': token,
                'v': '5.199',
                'need_system': 1
            },
            timeout=10
        )
        data = response.json()
        
        if 'response' in data:
            albums = data['response'].get('items', [])
            logger.debug(f"DEBUG: Найдено альбомов: {len(albums)}")
            
            # Пробуем найти первый доступный альбом
            for album in albums:
                album_id = str(album['id'])
                album_title = album.get('title', 'Unknown')
                logger.debug(f"DEBUG: Проверяем альбом: {album_title} (id: {album_id})")
                
                # Проверяем можно ли загружать в этот альбом
                if await can_upload_to_album(album_id, token):
                    logger.debug(f"DEBUG: Используем альбом: {album_id}")
                    return album_id
            
        logger.debug("DEBUG: Не найден доступный альбом для загрузки")
        return None
        
    except Exception as e:
        logger.debug(f"DEBUG: Ошибка поиска альбома: {e}")
        return None

async def can_upload_to_album(album_id: str, token: str) -> bool:
    """Проверяет можно ли загружать фото в альбом"""
    try:
        # Пробуем получить upload server для альбома
        response = requests.post(
            'https://api.vk.com/method/photos.getUploadServer',
            params={
                'access_token': token,
                'v': '5.199',
                'album_id': album_id
            },
            timeout=5
        )
        data = response.json()
        return 'error' not in data
        
    except:
        return False

async def cancel_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена процесса загрузки фото"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    PHOTO_PROCESS[user_id] = False
    
    await query.edit_message_text(
        "❌ Загрузка фото отменена!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Назад к функциям", callback_data="photo_upload")]
        ])
    )

async def show_photo_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику по лимитам"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    daily_used = await get_user_daily_photo_limit(user_id)
    remaining = 10000 - daily_used
    
    await query.edit_message_text(
        f"📊 Статистика накрутки фото\n\n"
        f"📅 За сегодня:\n"
        f"• ✅ Использовано: {daily_used} фото\n"
        f"• 📈 Осталось: {remaining} фото\n"
        f"• 🎯 Всего лимит: 10,000 фото",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Начать накрутку", callback_data="start_photo_upload")],
            [InlineKeyboardButton("Назад", callback_data="photo_upload")]
        ])
    )

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

async def get_vk_user_info(token: str) -> dict:
    """Получение информации о пользователе VK с дополнительными полями"""
    try:
        response = requests.get(
            'https://api.vk.com/method/users.get',
            params={
                'access_token': token,
                'v': '5.199',
                'fields': 'first_name,last_name,domain,screen_name,photo_100'
            },
            timeout=10
        )
        data = response.json()
        
        if 'response' in data and len(data['response']) > 0:
            user_info = data['response'][0]
            
            # Добавляем полную ссылку на профиль
            domain = user_info.get('domain') or user_info.get('screen_name') or f"id{user_info.get('id')}"
            user_info['profile_url'] = f"https://vk.com/{domain}"
            
            return user_info
        return {}
    except Exception as e:
        logger.error(f"Ошибка при получении информации о пользователе VK: {e}")
        return {}

# ========== ОБРАБОТЧИК СООБЩЕНИЙ ==========
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех сообщений - С ЗАЩИТОЙ ОТ ДУБЛИРОВАНИЯ"""
    user_id = str(update.message.from_user.id)
    
    logger.debug(f"🔍 DEBUG: Новое сообщение от {user_id}, текст: '{update.message.text}', фото: {bool(update.message.photo)}")

    # 🔧 ЗАЩИТА ОТ ДУБЛИРОВАНИЯ: проверяем временную метку
    current_time = time.time()
    if user_id in MESSAGE_PROCESSING:
        # Если сообщение обрабатывалось менее 2 секунд назад - игнорируем дубль
        if current_time - MESSAGE_PROCESSING[user_id] < 2:
            logger.debug(f"🔍 DEBUG: Дублирующее сообщение от {user_id}, игнорируем (разница: {current_time - MESSAGE_PROCESSING[user_id]:.2f} сек)")
            return
    
    # 🔧 УСТАНАВЛИВАЕМ НОВУЮ ВРЕМЕННУЮ МЕТКУ
    MESSAGE_PROCESSING[user_id] = current_time

    try:
        # 🔧 КРИТИЧЕСКИЙ ФИКС: Проверяем активный процесс ПЕРВЫМ делом
        if PHOTO_PROCESS.get(user_id):
            logger.debug(f"🔍 DEBUG: Процесс активен для {user_id}, ИГНОРИРУЕМ сообщение полностью")
            return

        # Пропускаем команды
        if update.message.text and update.message.text.startswith('/'):
            await handle_regular_message(update, context)
            return

        # Проверяем верификацию
        if not USER_VERIFIED.get(user_id):
            await handle_first_message(update, context)
            return

        # 🔧 ФИКС: ТОЛЬКО явные состояния обрабатываем
        current_state = None
        if context.user_data.get('waiting_for_token') or context.user_data.get('updating_token'):
            current_state = 'token'
        elif context.user_data.get('waiting_for_photo_details'):
            current_state = 'photo_details'
        elif context.user_data.get('waiting_for_photo_info'):
            current_state = 'photo_info'

        logger.debug(f"🔍 DEBUG: Текущее состояние для {user_id}: {current_state}")

        # 🔧 ФИКС: Обрабатываем ТОЛЬКО если есть явное состояние
        if current_state == 'token':
            logger.debug("🔍 DEBUG: Обрабатываем токен")
            await handle_token_message(update, context)
        elif current_state == 'photo_details' and update.message.text:
            logger.debug("🔍 DEBUG: Обрабатываем детали фото")
            await handle_photo_details(update, context)
        elif current_state == 'photo_info' and update.message.photo:
            logger.debug("🔍 DEBUG: Обрабатываем фото")
            await handle_photo_upload(update, context)
        else:
            logger.debug(f"🔍 DEBUG: Нет подходящего состояния, игнорируем сообщение")
            # 🔥 ВАЖНО: Не отвечаем на непонятные сообщения
            return
            
    except Exception as e:
        logger.error(f"Ошибка в handle_message для {user_id}: {e}")
        logger.debug(f"🔍 DEBUG: Ошибка обработки сообщения: {e}")

# 🔧 ДОБАВИТЬ ФУНКЦИЮ АВТООЧИСТКИ (опционально)
async def auto_cleanup_old_flags():
    """Очищает старые флаги обработки (старше 10 минут)"""
    while True:
        await asyncio.sleep(300)  # Проверяем каждые 5 минут
        current_time = time.time()
        old_keys = []
        for user_id, timestamp in MESSAGE_PROCESSING.items():
            if current_time - timestamp > 600:  # 10 минут
                old_keys.append(user_id)
        
        for user_id in old_keys:
            del MESSAGE_PROCESSING[user_id]
            logger.debug(f"🔧 Автоочистка старого флага для {user_id}")

# 🔧 ДОБАВИТЬ ЭТУ ФУНКЦИЮ
async def auto_cleanup_processing_flags():
    """Автоматически очищает зависшие флаги обработки"""
    while True:
        await asyncio.sleep(60)  # Проверяем каждую минуту
        current_time = time.time()
        # Очищаем флаги, которые висят больше 5 минут
        for user_id in list(MESSAGE_PROCESSING.keys()):
            if MESSAGE_PROCESSING[user_id] and hasattr(MESSAGE_PROCESSING[user_id], 'timestamp'):
                if current_time - MESSAGE_PROCESSING[user_id].timestamp > 300:  # 5 минут
                    MESSAGE_PROCESSING[user_id] = False
                    logger.info(f"🔧 Автоочистка зависшего флага для {user_id}")

async def update_token_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик обновления токена"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="connect_vk")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🔄 Введите новый токен VK:\n\n"
        "⚠️ Внимание: токен должен начинаться с 'vk1.a.'\n"
        "❌ Для отмены введите /cancel",
        reply_markup=reply_markup
    )
    context.user_data['waiting_for_token'] = True
    context.user_data['updating_token'] = True

async def delete_token_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик удаления токена"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    tokens = load_tokens()
    
    if user_id in tokens:
        del tokens[user_id]
        save_tokens(tokens)
    
    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="connect_vk")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "✅ Токен успешно удален!",
        reply_markup=reply_markup
    )

# 🔧 УЛУЧШЕННАЯ ОТМЕНА
async def universal_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Мгновенная отмена всех процессов"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    try:
        await query.answer("🛑 Останавливаю накрутку...")
    except:
        pass
    
    logger.info(f"Пользователь {user_id} отменил процесс накрутки")

    # 🔧 СБРАСЫВАЕМ ВСЕ ФЛАГИ
    PHOTO_PROCESS[user_id] = False
    CANCEL_FLAGS[user_id] = True
    
    # 🔧 ОЧИСТКА ДАННЫХ ПОЛЬЗОВАТЕЛЯ
    context.user_data.pop('waiting_for_photo_info', None)
    context.user_data.pop('waiting_for_photo_details', None)
    context.user_data.pop('pending_photo', None)
    
    # 🔧 СБРАСЫВАЕМ ФЛАГ ОБРАБОТКИ
    await set_user_processing(user_id, False)

    await safe_edit_message(
        query.message,
        "🛑 <b>НАКРУТКА ОСТАНОВЛЕНА</b>\n\n"
        "❌ Все процессы прерваны по вашему запросу.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📸 Новая накрутка", callback_data="start_photo_upload")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
        ])
    )

async def safe_edit_message(message, text: str, reply_markup=None, parse_mode='HTML'):
    """Безопасное обновление сообщения"""
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
        return True
    except Exception as e:
        logger.warning(f"Не удалось обновить сообщение: {e}")
        return False
    
async def safe_reply_message(update, text: str, reply_markup=None, parse_mode='HTML'):
    """Безопасная отправка сообщения"""
    try:
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
        return True
    except Exception as e:
        logger.warning(f"Не удалось отправить сообщение: {e}")
        return False

# ========== ОБРАБОТЧИК КНОПОК ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки - С ЗАЩИТОЙ ОТ ОШИБОК"""
    query = update.callback_query
    
    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"Не удалось ответить на callback_query: {e}")
        return  # 🔥 ВАЖНО: выходим если не удалось ответить
    
    user_id = str(query.from_user.id)
    
    # Проверка верификации
    if query.data != "captcha_verify" and not USER_VERIFIED.get(user_id):
        return
    
    try:
        # 🔧 ФИКС: Обработка всех кнопок ПЕРЕНЕСЕНА ВНУТРЬ try блока
        handlers = {
            "subscription": show_subscription_menu,
            "buy_subscription": show_buy_subscription,
            "check_subscription": check_subscription_status,
            "admin_panel": admin_panel,
            "admin_stats": admin_panel,  # Заглушки
            "admin_users": admin_panel,  # Заглушки
            "admin_subscriptions": admin_panel,  # Заглушки
            "captcha_verify": captcha_verify,
            "universal_cancel": universal_cancel_handler,
            "menu": show_menu,
            "connect": show_connect_menu,
            "profile": show_profile,
            "vk_functions": show_vk_functions,
            "unread_messages": show_unread_messages_info,
            "refresh_dialogs_main": refresh_dialogs_main,
            "dialogs_stats_main": show_dialogs_stats_main,
            "start_unread": start_unread_process,
            "cancel_unread": cancel_unread_process,
            "photo_upload": show_photo_upload_info,
            "start_photo_upload": start_photo_upload,
            "cancel_photo_upload": cancel_photo_upload,
            "photo_stats": show_photo_stats,
            "update_token": update_token_handler,
            "delete_token": delete_token_handler,
            "connect_vk": show_connect_menu
        }
        
        handler = handlers.get(query.data)
        if handler:
            await handler(update, context)
        else:
            await query.edit_message_text(
                "Функция в разработке...",
                reply_markup=get_back_button()
            )
            
    except Exception as e:
        logger.error(f"Ошибка в обработчике кнопок {query.data}: {e}")
        try:
            await query.edit_message_text(
                "❌ Произошла ошибка при обработке запроса",
                reply_markup=get_back_button()
            )
        except:
            pass  # Если не удалось отправить сообщение об ошибке

# ========== ФУНКЦИИ ДЛЯ ПОДКЛЮЧЕНИЯ ==========

async def show_connect_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню подключения"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    tokens = load_tokens()
    
    if user_id in tokens:
        keyboard = [
            [InlineKeyboardButton("Обновить токен", callback_data="update_token")],
            [InlineKeyboardButton("Удалить токен", callback_data="delete_token")],
            [InlineKeyboardButton("Назад в меню", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🔗 Управление подключением VK:\n\n"
            f"У вас уже есть активный токен:\n"
            f"🔑 ...{tokens[user_id][-5:]}\n\n"
            f"Выберите действие:",
            reply_markup=reply_markup
        )
    else:
        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🔗 Для подключения токена VK:\n\n"
            "1. Откройте приложение Kate Mobile\n"
            "2. Перейдите в Настройки → Работа с API\n"
            "3. Скопируйте ваш токен\n"
            "4. Отправьте его мне в ответном сообщении\n\n"
            "⚠️ Внимание: токен должен начинаться с 'vk1.a.'\n"
            "❌ Для отмены введите /cancel",
            reply_markup=reply_markup
        )
        context.user_data['waiting_for_token'] = True

async def upload_single_photo_guaranteed(token: str, album_id: str, photo_bytes: bytes, photo_caption: str, index: int, user_id: str) -> bool:
    """ГАРАНТИРОВАННАЯ загрузка одного фото с максимальной надежностью"""
    if not PHOTO_PROCESS.get(user_id):
        return False

    try:
        # 🔧 ПЕРВЫЙ ШАГ: Получаем upload server
        upload_url = await get_upload_server_guaranteed(token, album_id)
        if not upload_url:
            return False

        # 🔧 ВТОРОЙ ШАГ: Загружаем фото на сервер VK
        upload_result = await upload_to_server_guaranteed(upload_url, photo_bytes, index)
        if not upload_result:
            return False

        # 🔧 ТРЕТИЙ ШАГ: Сохраняем фото в альбом
        save_result = await save_photo_guaranteed(token, album_id, upload_result, photo_caption)
        return save_result

    except Exception as e:
        logger.debug(f"Ошибка загрузки фото {index}: {e}")
        return False
    
async def get_upload_server_guaranteed(token: str, album_id: str) -> str:
    """Гарантированное получение upload server"""
    for attempt in range(5):  # 5 попыток
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://api.vk.com/method/photos.getUploadServer',
                    params={
                        'access_token': token,
                        'v': '5.199',
                        'album_id': album_id
                    },
                    timeout=10
                ) as response:
                    data = await response.json()
                    
                    if 'response' in data and data['response'].get('upload_url'):
                        return data['response']['upload_url']
                    
                    # Если ошибка - ждем и пробуем снова
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.debug(f"Ошибка получения upload server (попытка {attempt + 1}): {e}")
            await asyncio.sleep(1)
    
    return None

async def upload_to_server_guaranteed(upload_url: str, photo_bytes: bytes, index: int) -> dict:
    """Гарантированная загрузка фото на сервер VK"""
    for attempt in range(5):  # 5 попыток
        try:
            async with aiohttp.ClientSession() as session:
                form_data = aiohttp.FormData()
                form_data.add_field('file', photo_bytes, filename=f'photo_{index}.jpg', content_type='image/jpeg')
                
                async with session.post(
                    upload_url,
                    data=form_data,
                    timeout=30
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        # Проверяем обязательные поля
                        if all(field in result for field in ['server', 'photos_list', 'hash']):
                            return result
                    
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.debug(f"Ошибка загрузки на сервер (попытка {attempt + 1}): {e}")
            await asyncio.sleep(1)
    
    return None

async def save_photo_guaranteed(token: str, album_id: str, upload_result: dict, photo_caption: str) -> bool:
    """Гарантированное сохранение фото в альбом"""
    for attempt in range(5):  # 5 попыток
        try:
            async with aiohttp.ClientSession() as session:
                save_params = {
                    'access_token': token,
                    'v': '5.199',
                    'album_id': album_id,
                    'server': str(upload_result['server']),
                    'photos_list': str(upload_result['photos_list']),
                    'hash': str(upload_result['hash']),
                    'caption': photo_caption or ''
                }

                async with session.post(
                    'https://api.vk.com/method/photos.save',
                    params=save_params,
                    timeout=15
                ) as response:
                    if response.status == 200:
                        save_data = await response.json()
                        
                        # УСПЕШНО если есть response с данными
                        if 'response' in save_data and isinstance(save_data['response'], list) and len(save_data['response']) > 0:
                            return True
                    
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.debug(f"Ошибка сохранения фото (попытка {attempt + 1}): {e}")
            await asyncio.sleep(1)
    
    return False

async def handle_token_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик сообщений с токенами"""
    user_id = str(update.message.from_user.id)
    token = update.message.text.strip()
    
    tokens = load_tokens()
    
    # Проверка формата токена
    if not token.startswith('vk1.a.'):
        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ Неверный формат токена!\n"
            "Токен должен начинаться с 'vk1.a.'",
            reply_markup=reply_markup
        )
        return
    
    # Сохранение токена
    tokens[user_id] = token
    save_tokens(tokens)
    context.user_data.pop('waiting_for_token', None)
    context.user_data.pop('updating_token', None)
    
    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "✅ Токен успешно подключен!",
        reply_markup=reply_markup
    )

# ========== ЗАПУСК БОТА ==========
def main():
    """Основная функция"""
    application = Application.builder().token("8243355053:AAEsMfXRAWO_-SSzXTWxm2b_aZt63obWb44").build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Один обработчик для всех сообщений
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    # Добавьте в начало main() функции:
    load_subscriptions()

    # Добавьте обработчик команды админа в main():
    application.add_handler(CommandHandler("adm", admin_command))
    application.add_handler(CommandHandler("unadm", unadm_command))  # 🔧 НОВАЯ КОМАНДА
    # Команда для показа лимитов подключенных аккаунтов
    application.add_handler(CommandHandler("limits", show_account_limits_summary))
    
    # Запуск бота
    print("Bot started")
    application.run_polling()

if __name__ == '__main__':
    main()