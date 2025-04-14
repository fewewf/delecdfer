

import re
import json
import time
import base64
import imaplib
import email
import logging
import sys
import os
import ddddocr
import requests
from bs4 import BeautifulSoup
from email.header import decode_header
import datetime
from datetime import datetime, timedelta
import pytz
from telegram import Bot
import aiohttp
import asyncio
import signal


logging.getLogger("ddddocr").setLevel(logging.WARNING)


LOG_FILE = "/root/euserv_renewal.log"
def setup_logging():

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stdout)
        ]
    )

    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 10 * 1024 * 1024:
        with open(LOG_FILE, "w") as f:
            f.truncate(0)

# 账户信息：用户名和密码
USERNAME = ''  # 德鸡登录用户名
PASSWORD = ''  # 德鸡登录密码


WXPUSHER_TOKEN = ""  
WXPUSHER_TOPIC_ID = ""  


TELEGRAM_BOT_TOKEN = ""  
TELEGRAM_CHAT_ID = ""  


GMAIL_USER = ''      
GMAIL_APP_PASSWORD = '' 
GMAIL_FOLDER = "INBOX" 
IMAP_SERVER = "imap.gmail.com" 
IMAP_PORT = 993  


LOGIN_MAX_RETRY_COUNT = 10  


WAITING_TIME_OF_PIN = 15


ocr = ddddocr.DdddOcr(show_ad=False)  


user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


renewal_performed = False
last_execution_date = None
desp = ""

def log(info: str):

    emoji_map = {
        "正在续费": "🔄",
        "检测到": "🔍",
        "ServerID": "🔗",
        "无需更新": "✅",
        "续订错误": "⚠️",
        "已成功续订": "🎉",
        "所有工作完成": "🏁",
        "登陆失败": "❗",
        "验证通过": "✔️",
        "验证失败": "❌",
        "验证码是": "🔢",
        "登录尝试": "🔑",
        "[Gmail]": "📧",
        "[ddddocr]": "🧩",
        "[德鸡自动续期]": "🌐",
    }

    for key, emoji in emoji_map.items():
        if key in info:
            info = emoji + " " + info
            break

    logging.info(info)
    
    global desp
    desp += info + "\n\n"


def login_retry(max_retry=3):
    def wrapper(func):
        def inner(*args, **kwargs):
            ret, ret_session = func(*args, **kwargs)
            number = 0
            if ret == "-1":
                while number < max_retry:
                    number += 1
                    if number > 1:
                        log(f"[德鸡自动续期] 登录尝试第 {number} 次")
                    sess_id, session = func(*args, **kwargs)
                    if sess_id != "-1":
                        return sess_id, session
                    else:
                        if number == max_retry:
                            return sess_id, session
                    time.sleep(2)  
            else:
                return ret, ret_session
        return inner
    return wrapper
    

@login_retry(max_retry=LOGIN_MAX_RETRY_COUNT)
def login(username: str, password: str) -> (str, requests.session):
    """登录函数"""
    headers = {"user-agent": user_agent, "origin": "https://www.euserv.com"}
    url = "https://support.euserv.com/index.iphp"
    ddddocr_image_url = "https://support.euserv.com/securimage_show.php"
    session = requests.Session()


    log("[德鸡自动续期] 正在获取登录页面...")
    sess = session.get(url, headers=headers)
    sess_id = re.findall("PHPSESSID=(\\w{10,100});", str(sess.headers))[0]
    log(f"[德鸡自动续期] 获取到 PHPSESSID: {sess_id}")
    

    session.get("https://support.euserv.com/pic/logo_small.png", headers=headers)
    time.sleep(1)  

    login_data = {
        "email": username,
        "password": password,
        "form_selected_language": "en",
        "Submit": "Login",
        "subaction": "login",
        "sess_id": sess_id,
    }
    log("[德鸡自动续期] 正在提交登录请求...")
    f = session.post(url, headers=headers, data=login_data)
    f.raise_for_status()

    if "Hello" not in f.text and "Confirm or change your customer data here" not in f.text:
        if "To finish the login process please solve the following captcha." not in f.text:
            log(f"[德鸡自动续期] 登录失败，响应内容: {f.text}")
            return "-1", session
        else:
            log("[ddddocr] 检测到验证码，正在进行验证码识别...")
            ddddocr_code = ddddocr_solver(ddddocr_image_url, session)
            log("[ddddocr] 识别的验证码是: {}".format(ddddocr_code))

            f2 = session.post(
                url,
                headers=headers,
                data={
                    "subaction": "login",
                    "sess_id": sess_id,
                    "captcha_code": ddddocr_code,
                },
            )
            if "To finish the login process please solve the following captcha." not in f2.text:
                log("[ddddocr] 验证通过")
                return sess_id, session
            else:
                log("[ddddocr] 验证失败")
                log(f"[ddddocr] 完整响应: {f2.text}")
                return "-1", session
    else:
        log("[德鸡自动续期] 登录成功")
        return sess_id, session
        

def ddddocr_solver(ddddocr_image_url: str, session: requests.session) -> str:
    log("[ddddocr] 正在下载验证码图片...")
    response = session.get(ddddocr_image_url)
    log("[ddddocr] 验证码图片下载完成，开始识别...")
    result = ocr.classification(response.content)
    return result
    

def get_pin_from_gmail() -> str:
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)       

    mail.select(GMAIL_FOLDER)
        
    status, messages = mail.search(None, "ALL")  
    if status != "OK":
        log("[Gmail] 无法检索邮件列表")
        return None

    latest_email_id = messages[0].split()[-1]   
    status, msg_data = mail.fetch(latest_email_id, "(RFC822)") 
    if status != "OK":
        log("[Gmail] 无法检索邮件内容")
        return None

    raw_email = msg_data[0][1] 
    msg = email.message_from_bytes(raw_email)
    
    pin = None  
    
    # 提取邮件正文
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                body = part.get_payload(decode=True).decode()
                pin_match = re.search(r'PIN:\s*(\d{6})', body)
                if pin_match:
                    pin = pin_match.group(1)
                    break
    else:
        body = msg.get_payload(decode=True).decode()
        pin_match = re.search(r'PIN:\s*(\d{6})', body)  
        if pin_match:
            pin = pin_match.group(1)

    mail.logout() 

    if pin:
        log(f"[Gmail] 成功获取PIN: {pin}")
        return pin
    else:
        raise Exception("未能从邮件中提取PIN")

def get_servers(sess_id: str, session: requests.session) -> {}:
    """获取服务器列表"""
    d = {}
    url = "https://support.euserv.com/index.iphp?sess_id=" + sess_id
    headers = {"user-agent": user_agent, "origin": "https://www.euserv.com"}
    f = session.get(url=url, headers=headers)
    f.raise_for_status()
    soup = BeautifulSoup(f.text, "html.parser")
    for tr in soup.select(
        "#kc2_order_customer_orders_tab_content_1 .kc2_order_table.kc2_content_table tr"
    ):
        server_id = tr.select(".td-z1-sp1-kc")
        if not len(server_id) == 1:
            continue
        flag = (
            True
            if tr.select(".td-z1-sp2-kc .kc2_order_action_container")[0]
            .get_text()
            .find("Contract extension possible from")
            == -1
            else False
        )
        d[server_id[0].get_text()] = flag
    return d
    
# 发送 WxPusher 通知
async def send_wxpusher_notification(message: str):
    """发送微信通知"""
    data = {
        "appToken": WXPUSHER_TOKEN,
        "content": message,
        "contentType": 2,  
        "topicIds": [int(WXPUSHER_TOPIC_ID)],
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "http://wxpusher.zjiecode.com/api/send/message",
                json=data
            ) as response:
                if response.status != 200:
                    log("[德鸡自动续期] WxPusher 推送失败")
                else:
                    log("[德鸡自动续期] 续期结果已推送至微信")
        except Exception as e:
            log(f"[德鸡自动续期] 发送WxPusher通知时发生错误: {str(e)}")


async def send_telegram_notification(message: str):
    """发送Telegram通知"""
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='HTML')
        log("[德鸡自动续期] 续期结果已推送至Telegram")
    except Exception as e:
        log(f"[德鸡自动续期] 发送Telegram通知时发生错误: {str(e)}")


def renew(sess_id: str, session: requests.session, password: str, order_id: str) -> bool:
    global renewal_performed
    
    url = "https://support.euserv.com/index.iphp"
    headers = {
        "user-agent": user_agent,
        "Host": "support.euserv.com",
        "origin": "https://support.euserv.com",
        "Referer": "https://support.euserv.com/index.iphp",
    }
    data = {
        "Submit": "Extend contract",
        "sess_id": sess_id,
        "ord_no": order_id,
        "subaction": "choose_order",
        "choose_order_subaction": "show_contract_details",
    }
    session.post(url, headers=headers, data=data)


    session.post(
        url,
        headers=headers,
        data={
            "sess_id": sess_id,
            "subaction": "show_kc2_security_password_dialog",
            "prefix": "kc2_customer_contract_details_extend_contract_",
            "type": "1",
        },
    )

    log("[Gmail] 等待PIN邮件到达...")
    time.sleep(WAITING_TIME_OF_PIN)
        
    retry_count = 3
    pin = None
    for i in range(retry_count):
        try:
            pin = get_pin_from_gmail()
            if pin:
                break
        except Exception as e:
            if i < retry_count - 1:
                log(f"[Gmail] 第{i+1}次尝试获取PIN失败，等待后重试...")
                time.sleep(5)
            else:
                raise Exception(f"多次尝试获取PIN均失败: {str(e)}")
        
    if not pin:
        return False

 
    data = {
        "auth": pin,
        "sess_id": sess_id,
        "subaction": "kc2_security_password_get_token",
        "prefix": "kc2_customer_contract_details_extend_contract_",
        "type": 1,
        "ident": f"kc2_customer_contract_details_extend_contract_{order_id}",
    }
    f = session.post(url, headers=headers, data=data)
    f.raise_for_status()
    
    if not json.loads(f.text)["rs"] == "success":
        return False
        
    token = json.loads(f.text)["token"]["value"]
    data = {
        "sess_id": sess_id,
        "ord_id": order_id,
        "subaction": "kc2_customer_contract_details_extend_contract_term",
        "token": token,
    }
    
    response = session.post(url, headers=headers, data=data)
    if response.status_code == 200:
        renewal_performed = True
        return True
    return False


def check(sess_id: str, session: requests.session):
    log("[德鸡自动续期] 正在检查续期状态...")
    d = get_servers(sess_id, session)
    flag = True
    for key, val in d.items():
        if val:
            flag = False
            log("[德鸡自动续期] ServerID: %s 续期失败!" % key)

    if flag:
        log("[德鸡自动续期] 所有德鸡续期完成。开启挂机人生！")


async def process_renewal():
    global renewal_performed, desp, last_execution_date
    renewal_performed = False
    desp = ""  
    
    if not USERNAME or not PASSWORD:
        log("[德鸡自动续期] 你没有添加任何账户")
        return
        
    user_list = USERNAME.strip().split()
    passwd_list = PASSWORD.strip().split()
    if len(user_list) != len(passwd_list):
        log("[德鸡自动续期] 用户名和密码数量不匹配!")
        return

    try:
        for i in range(len(user_list)):
            log("[德鸡自动续期] 正在续费第 %d 个账号" % (i + 1))
            sessid, s = login(user_list[i], passwd_list[i])
            
            if sessid == "-1":
                log("[德鸡自动续期] 第 %d 个账号登陆失败，请检查登录信息" % (i + 1))
                continue
                
            SERVERS = get_servers(sessid, s)
            log("[德鸡自动续期] 检测到第 {} 个账号有 {} 台 VPS，正在尝试续期".format(i + 1, len(SERVERS)))
            
            for k, v in SERVERS.items():
                if v:
                    try:
                        if not renew(sessid, s, passwd_list[i], k):
                            log("[德鸡自动续期] ServerID: %s 续订错误!" % k)
                        else:
                            log("[德鸡自动续期] ServerID: %s 已成功续订!" % k)
                    except Exception as e:
                        log(f"[德鸡自动续期] 续订 ServerID: {k} 时发生错误: {str(e)}")
                else:
                    log("[德鸡自动续期] ServerID: %s 无需更新" % k)
            
            time.sleep(15)
            check(sessid, s)
            time.sleep(5)

    except Exception as e:
        error_msg = f"[德鸡自动续期] 续期过程发生错误: {str(e)}"
        log(error_msg)

 
    tg_message = f"<b>德鸡续期结果</b>\n\n{desp}"
    wx_message = f"<b>德鸡续期结果</b>\n\n{desp}"
    await send_telegram_notification(tg_message)
    if WXPUSHER_TOKEN and WXPUSHER_TOPIC_ID:
        await send_wxpusher_notification(wx_message)

def get_next_run_time():
    now = datetime.now()

    hours = now.hour
    next_interval = ((hours // 6) + 1) * 6  # 找到下个6小时点
    if next_interval >= 24:
        next_run = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        next_run = now.replace(hour=next_interval, minute=0, second=0, microsecond=0)
    return next_run

async def main():
    log("[德鸡自动续期] 脚本启动")
    log(f"[德鸡自动续期] Python executable: {sys.executable}")
    log(f"[德鸡自动续期] sys.path: {sys.path}")
    
    while True:
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        current_second = now.second


        if current_hour % 6 == 0 and current_minute == 0 and current_second == 0:
            log("[德鸡自动续期] 当前时间为 {}，开始执行续期流程".format(now.strftime("%H:%M")))
            await process_renewal()
            log("[德鸡自动续期] 续期流程执行完成")
            time.sleep(60)  # 等待1分钟，避免重复执行
        else:

            next_run = get_next_run_time()
            seconds_until_next_run = (next_run - now).total_seconds()
            log("[德鸡自动续期] 下次运行时间: {}，将在 {} 秒后执行".format(
                next_run.strftime("%Y-%m-%d %H:%M:%S"), int(seconds_until_next_run)))
            time.sleep(seconds_until_next_run)

def handle_exit(signum, frame):
    """处理退出信号"""
    log("[德鸡自动续期] 收到退出信号，正在关闭守护进程...")
    sys.exit(0)

if __name__ == "__main__":
    try:

        setup_logging()
        

        if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN" or TELEGRAM_CHAT_ID == "YOUR_TELEGRAM_CHAT_ID":
            log("[德鸡自动续期] 请配置 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID")
            sys.exit(1)


        required_modules = ['pytz', 'requests', 'bs4', 'ddddocr', 'telegram', 'aiohttp']  # 修改 beautifulsoup4 为 bs4
        missing_modules = []
        for module in required_modules:
            try:
                __import__(module)
            except ImportError:
                missing_modules.append(module)
        if missing_modules:
            log(f"[德鸡自动续期] 缺少以下依赖: {', '.join(missing_modules)}")
            log("[德鸡自动续期] 请安装依赖: pip3 install " + " ".join(missing_modules) + " -i https://pypi.tuna.tsinghua.edu.cn/simple")
            sys.exit(1)


        signal.signal(signal.SIGINT, handle_exit)
        signal.signal(signal.SIGTERM, handle_exit)
        

        asyncio.run(main())
    except Exception as e:
        log(f"[德鸡自动续期] 程序异常退出: {str(e)}")
        sys.exit(1)