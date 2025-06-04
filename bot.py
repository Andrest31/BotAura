import os
import logging
import json
import asyncio
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Настройка токена (добавьте в переменные окружения Railway)
TOKEN = os.environ.get('TOKEN', '8196025392:AAGhJVa3gLMlnnRQPFywVnUEP-qihiz57uQ')

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация Google Sheets
SPREADSHEET_ID = "1e5bOACbvTHXGfihhEGURvVX0AIOBSEOgziAfnQNr-Dc"
async def check_creds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            json.loads(os.environ['SERVICE_ACCOUNT_JSON']),
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        await update.message.reply_text("✅ Google Sheets доступен!")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
# Инициализация Google Sheets
def get_sheets_service():
    try:
        # Проверка существования переменной
        if 'SERVICE_ACCOUNT_JSON' not in os.environ:
            raise ValueError("Переменная SERVICE_ACCOUNT_JSON не найдена в окружении")
            
        # Загрузка и проверка JSON
        sa_json = os.environ['SERVICE_ACCOUNT_JSON']
        if not sa_json.strip().startswith('{'):
            raise ValueError("Некорректный формат SERVICE_ACCOUNT_JSON")
            
        service_account_info = json.loads(sa_json)
        
        # Создание credentials
        creds = Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        return build('sheets', 'v4', credentials=creds).spreadsheets()
        
    except json.JSONDecodeError:
        logger.error("Ошибка декодирования SERVICE_ACCOUNT_JSON: невалидный JSON")
        raise
    except Exception as e:
        logger.error(f"Критическая ошибка инициализации Google Sheets: {str(e)}")
        raise

# Состояния
(
    SELECTING_ACTION, ADDING_PRODUCT, ADDING_PLAN_QUANTITY,
    ADDING_MADE_QUANTITY, SELECTING_PRODUCT_FOR_PLAN,
    SELECTING_PRODUCT_FOR_MADE, SELECTING_PRODUCT_FOR_DELETE,
    CONFIRMING_DELETE
) = range(8)

# Клавиатуры
def main_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Новый продукт"],
        ["📝 Добавить план"],
        ["✅ Добавить выполнено"],
        ["📊 Статус производства"],
        ["🗑 Удалить продукт"]
    ], resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏭 Бот для учета производства. Выберите действие:", reply_markup=main_keyboard())
    return SELECTING_ACTION

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите название нового изделия:", reply_markup=cancel_keyboard())
    return ADDING_PRODUCT

async def save_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product_name = update.message.text
    try:
        service = get_sheets_service()
        service.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Лист1!A2:D",
            valueInputOption="USER_ENTERED",
            body={"values": [[product_name, 0, 0, "0%"]]}
        ).execute()
        await update.message.reply_text(f"✅ Изделие '{product_name}' добавлено!", reply_markup=main_keyboard())
    except Exception as e:
        logger.error(f"Ошибка при добавлении: {e}")
        await update.message.reply_text("❌ Ошибка при добавлении изделия", reply_markup=main_keyboard())
    return SELECTING_ACTION

async def select_product(update, context, action: str, next_state: int):
    try:
        service = get_sheets_service()
        result = service.values().get(spreadsheetId=SPREADSHEET_ID, range="Лист1!A2:D").execute()
        rows = result.get('values', [])
        products = [row[0] for row in rows if row]

        if not products:
            await update.message.reply_text("❌ Нет изделий", reply_markup=main_keyboard())
            return SELECTING_ACTION

        context.user_data['all_products_data'] = rows
        context.user_data['available_products'] = products
        context.user_data['action_type'] = action

        keyboard = [[p] for p in products] + [["❌ Отмена"]]
        await update.message.reply_text(f"Выберите изделие для добавления {action}:", 
                                      reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
        return next_state
    except Exception as e:
        logger.error(f"Ошибка выбора изделия: {e}")
        await update.message.reply_text("❌ Ошибка получения списка", reply_markup=main_keyboard())
        return SELECTING_ACTION

async def select_product_for_plan(update, context): 
    return await select_product(update, context, "плана", SELECTING_PRODUCT_FOR_PLAN)
    
async def select_product_for_made(update, context): 
    return await select_product(update, context, "выполненных изделий", SELECTING_PRODUCT_FOR_MADE)

async def handle_quantity_input(update, context, action, column_idx, state):
    text = update.message.text
    if 'selected_product' not in context.user_data:
        if text in context.user_data.get('available_products', []):
            context.user_data['selected_product'] = text
            context.user_data['column_idx'] = column_idx
            await update.message.reply_text(f"Выбрано: {text}\nВведите количество {action}:", 
                                          reply_markup=cancel_keyboard())
            return state
        else:
            await update.message.reply_text("❌ Выберите изделие из списка", reply_markup=main_keyboard())
            return SELECTING_ACTION

    try:
        quantity = int(text)
        product_name = context.user_data['selected_product']
        column_idx = context.user_data['column_idx']
        rows = context.user_data['all_products_data']

        for i, row in enumerate(rows, start=2):
            if row[0] == product_name:
                service = get_sheets_service()
                current_value = int(row[column_idx]) if len(row) > column_idx and row[column_idx] else 0
                plan_value = int(row[2]) if len(row) > 2 and row[2] else 0

                if column_idx == 1 and (current_value + quantity) > plan_value:
                    await update.message.reply_text(
                        f"⚠️ Нельзя добавить {quantity}, превышает план ({plan_value}). Введите меньшее число:",
                        reply_markup=cancel_keyboard())
                    return state

                new_value = current_value + quantity
                service.values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"Лист1!{chr(65+column_idx)}{i}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [[new_value]]}
                ).execute()

                update_status(service, i)
                await update.message.reply_text(f"✅ Добавлено {quantity} к '{product_name}'", 
                                              reply_markup=main_keyboard())
                clear_context(context)
                return SELECTING_ACTION

        await update.message.reply_text("❌ Изделие не найдено", reply_markup=main_keyboard())
        return SELECTING_ACTION

    except ValueError:
        await update.message.reply_text("⚠️ Введите целое число", reply_markup=cancel_keyboard())
        return state
    except Exception as e:
        logger.error(f"Ошибка добавления: {e}")
        await update.message.reply_text("❌ Ошибка обновления", reply_markup=main_keyboard())
        return SELECTING_ACTION

async def add_plan_quantity(update, context): 
    return await handle_quantity_input(update, context, "плана", 2, ADDING_PLAN_QUANTITY)
    
async def add_made_quantity(update, context): 
    return await handle_quantity_input(update, context, "выполненных изделий", 1, ADDING_MADE_QUANTITY)

def update_status(service, row):
    result = service.values().get(spreadsheetId=SPREADSHEET_ID, range=f"Лист1!B{row}:C{row}").execute()
    values = result.get('values', [[]])[0]
    made = int(values[0]) if len(values) > 0 and values[0] else 0
    plan = int(values[1]) if len(values) > 1 and values[1] else 0
    percent = f"{round((made/plan)*100)}%" if plan > 0 else "0%"
    service.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Лист1!D{row}",
        valueInputOption="USER_ENTERED",
        body={"values": [[percent]]}
    ).execute()

async def show_status(update, context):
    try:
        service = get_sheets_service()
        result = service.values().get(spreadsheetId=SPREADSHEET_ID, range="Лист1!A2:D").execute()
        rows = result.get('values', [])
        text = "📊 Статус производства:\n\n"
        for row in rows:
            if len(row) >= 4:
                text += f"🔹 {row[0]}\nИзготовлено: {row[1]}\nПлан: {row[2]}\nВыполнение: {row[3]}\n\n"
        await update.message.reply_text(text.strip(), reply_markup=main_keyboard())
    except Exception as e:
        logger.error(f"Ошибка статуса: {e}")
        await update.message.reply_text("❌ Ошибка получения данных", reply_markup=main_keyboard())
    return SELECTING_ACTION

async def delete_product_start(update, context):
    service = get_sheets_service()
    result = service.values().get(spreadsheetId=SPREADSHEET_ID, range="Лист1!A2:A").execute()
    products = [row[0] for row in result.get('values', []) if row]
    if not products:
        await update.message.reply_text("❌ Нет изделий", reply_markup=main_keyboard())
        return SELECTING_ACTION
    context.user_data['available_products'] = products
    keyboard = [[p] for p in products] + [["❌ Отмена"]]
    await update.message.reply_text("Выберите изделие для удаления:", 
                                 reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return SELECTING_PRODUCT_FOR_DELETE

async def confirm_delete_product(update, context):
    product = update.message.text
    if product not in context.user_data.get('available_products', []):
        await update.message.reply_text("❌ Выберите изделие из списка", reply_markup=main_keyboard())
        return SELECTING_ACTION
    context.user_data['delete_product'] = product
    await update.message.reply_text(f"Удалить '{product}'?", 
                                  reply_markup=ReplyKeyboardMarkup([["✅ Да", "❌ Нет"]], resize_keyboard=True))
    return CONFIRMING_DELETE

async def do_delete_product(update, context):
    if update.message.text == "✅ Да":
        product = context.user_data['delete_product']
        service = get_sheets_service()
        result = service.values().get(spreadsheetId=SPREADSHEET_ID, range="Лист1!A2:D").execute()
        for i, row in enumerate(result.get('values', []), start=2):
            if row and row[0] == product:
                service.values().clear(spreadsheetId=SPREADSHEET_ID, range=f"Лист1!A{i}:D{i}").execute()
                await update.message.reply_text(f"🗑 Удалено: {product}", reply_markup=main_keyboard())
                clear_context(context)
                return SELECTING_ACTION
        await update.message.reply_text("❌ Не найдено", reply_markup=main_keyboard())
    else:
        await update.message.reply_text("Удаление отменено", reply_markup=main_keyboard())
    clear_context(context)
    return SELECTING_ACTION

async def cancel(update, context):
    clear_context(context)
    await update.message.reply_text("Операция отменена", reply_markup=main_keyboard())
    return SELECTING_ACTION

def clear_context(context):
    context.user_data.clear()

async def post_init(application: Application):
    await application.bot.delete_webhook(drop_pending_updates=True)

def main():

    app = Application.builder() \
        .token(TOKEN) \
        .post_init(post_init) \
        .build()
    app.add_handler(CommandHandler('check', check_creds))
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECTING_ACTION: [
                MessageHandler(filters.Regex('^➕ Новый продукт$'), add_product),
                MessageHandler(filters.Regex('^📝 Добавить план$'), select_product_for_plan),
                MessageHandler(filters.Regex('^✅ Добавить выполнено$'), select_product_for_made),
                MessageHandler(filters.Regex('^📊 Статус производства$'), show_status),
                MessageHandler(filters.Regex('^🗑 Удалить продукт$'), delete_product_start),
            ],
            ADDING_PRODUCT: [
                MessageHandler(filters.TEXT & ~filters.Regex('^❌ Отмена$'), save_product),
                MessageHandler(filters.Regex('^❌ Отмена$'), cancel),
            ],
            SELECTING_PRODUCT_FOR_PLAN: [
                MessageHandler(filters.TEXT & ~filters.Regex('^❌ Отмена$'), add_plan_quantity),
                MessageHandler(filters.Regex('^❌ Отмена$'), cancel),
            ],
            SELECTING_PRODUCT_FOR_MADE: [
                MessageHandler(filters.TEXT & ~filters.Regex('^❌ Отмена$'), add_made_quantity),
                MessageHandler(filters.Regex('^❌ Отмена$'), cancel),
            ],
            ADDING_PLAN_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.Regex('^❌ Отмена$'), add_plan_quantity),
                MessageHandler(filters.Regex('^❌ Отмена$'), cancel),
            ],
            ADDING_MADE_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.Regex('^❌ Отмена$'), add_made_quantity),
                MessageHandler(filters.Regex('^❌ Отмена$'), cancel),
            ],
            SELECTING_PRODUCT_FOR_DELETE: [
                MessageHandler(filters.TEXT & ~filters.Regex('^❌ Отмена$'), confirm_delete_product),
                MessageHandler(filters.Regex('^❌ Отмена$'), cancel),
            ],
            CONFIRMING_DELETE: [
                MessageHandler(filters.Regex('^(✅ Да|❌ Нет)$'), do_delete_product),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv)

    # Жёсткая проверка переменных
    if 'SERVICE_ACCOUNT_JSON' not in os.environ:
        logger.critical("FATAL: SERVICE_ACCOUNT_JSON не найдена!")
        logger.critical("Добавьте её в Railway: Settings → Variables")
        logger.critical(f"Текущие переменные: {list(os.environ.keys())}")
        exit(1)
    
    # Проверка формата JSON
    try:
        json.loads(os.environ['SERVICE_ACCOUNT_JSON'])
        logger.info("✅ SERVICE_ACCOUNT_JSON валидна")
    except json.JSONDecodeError:
        logger.critical("FATAL: Неправильный JSON в SERVICE_ACCOUNT_JSON!")
        exit(1)
    
    logger.info("Бот запускается...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()