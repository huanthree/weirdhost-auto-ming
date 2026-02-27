#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# scripts/weirdhost_renew.py

import os
import time
import asyncio
import aiohttp
import base64
import random
import re
import subprocess
import json
from datetime import datetime, timedelta
from urllib.parse import unquote

from seleniumbase import SB

try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

BASE_URL = "https://hub.weirdhost.xyz/server/"
DOMAIN = "hub.weirdhost.xyz"

RENEW_THRESHOLD_DAYS = int(os.environ.get("RENEW_THRESHOLD_DAYS", "2"))


def mask_sensitive(text, show_chars=3):
    if not text:
        return "***"
    text = str(text)
    if len(text) <= show_chars * 2:
        return "*" * len(text)
    return text[:show_chars] + "*" * (len(text) - show_chars * 2) + text[-show_chars:]


def mask_email(email):
    if not email or "@" not in email:
        return mask_sensitive(email)
    local, domain = email.rsplit("@", 1)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


def mask_server_id(server_id):
    if not server_id:
        return "***"
    if len(server_id) <= 4:
        return "*" * len(server_id)
    return server_id[:2] + "*" * (len(server_id) - 4) + server_id[-2:]


def mask_url(url):
    if not url:
        return "***"
    if "/server/" in url:
        parts = url.split("/server/")
        if len(parts) == 2:
            return parts[0] + "/server/" + mask_server_id(parts[1])
    return url


def parse_accounts():
    """解析 ACCOUNTS 环境变量"""
    accounts_str = os.environ.get("ACCOUNTS", "").strip()
    
    if not accounts_str:
        print("\n" + "=" * 60)
        print("❌ 错误: WEIRDHOST_ACCOUNTS 环境变量未设置")
        print("=" * 60)
        return []
    
    try:
        accounts = json.loads(accounts_str)
        if not isinstance(accounts, list):
            print("\n" + "=" * 60)
            print("❌ 错误: ACCOUNTS 格式错误，应为 JSON 数组")
            print("=" * 60)
            return []
        
        valid_accounts = []
        for i, acc in enumerate(accounts):
            if not isinstance(acc, dict):
                continue
            
            missing = []
            if not acc.get("id"):
                missing.append("id")
            if not acc.get("cookie_env"):
                missing.append("cookie_env")
            
            if missing:
                print(f"[!] 账号 {i+1} 缺少必要字段: {', '.join(missing)}")
                continue
            
            valid_accounts.append(acc)
        
        if not valid_accounts:
            return []
        
        print(f"[+] 解析到 {len(valid_accounts)} 个有效账号配置")
        return valid_accounts
        
    except json.JSONDecodeError as e:
        print("\n" + "=" * 60)
        print("❌ 错误: ACCOUNTS JSON 解析失败")
        print("=" * 60)
        return []


def parse_weirdhost_cookie(cookie_str):
    if not cookie_str:
        return (None, None)
    cookie_str = cookie_str.strip()
    if "=" in cookie_str:
        parts = cookie_str.split("=", 1)
        if len(parts) == 2:
            return (parts[0].strip(), unquote(parts[1].strip()))
    return (None, None)


def build_server_url(server_id):
    if not server_id:
        return None
    server_id = server_id.strip()
    return server_id if server_id.startswith("http") else f"{BASE_URL}{server_id}"


def calculate_remaining_time(expiry_str):
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                expiry_dt = datetime.strptime(expiry_str.strip(), fmt)
                diff = expiry_dt - datetime.now()
                if diff.total_seconds() < 0:
                    return "⚠️ 已过期"
                days = diff.days
                hours = diff.seconds // 3600
                minutes = (diff.seconds % 3600) // 60
                parts = []
                if days > 0:
                    parts.append(f"{days}天")
                if hours > 0:
                    parts.append(f"{hours}小时")
                if minutes > 0 and days == 0:
                    parts.append(f"{minutes}分钟")
                return " ".join(parts) if parts else "不到1分钟"
            except ValueError:
                continue
        return "无法解析"
    except:
        return "计算失败"


def parse_expiry_to_datetime(expiry_str):
    if not expiry_str or expiry_str == "Unknown":
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(expiry_str.strip(), fmt)
        except ValueError:
            continue
    return None


def get_remaining_days(expiry_str):
    expiry_dt = parse_expiry_to_datetime(expiry_str)
    if not expiry_dt:
        return None
    diff = expiry_dt - datetime.now()
    return diff.total_seconds() / 86400


def should_renew(expiry_str):
    remaining_days = get_remaining_days(expiry_str)
    if remaining_days is None:
        return True
    return remaining_days <= RENEW_THRESHOLD_DAYS


def random_delay(min_sec=0.5, max_sec=2.0):
    time.sleep(random.uniform(min_sec, max_sec))


async def tg_notify(message):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        print("[TG] 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过通知")
        return
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            )
        except Exception as e:
            print(f"[TG] 发送失败: {e}")


async def tg_notify_photo(photo_path, caption=""):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id or not os.path.exists(photo_path):
        return
    async with aiohttp.ClientSession() as session:
        try:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=os.path.basename(photo_path))
                data.add_field("caption", caption)
                data.add_field("parse_mode", "HTML")
                await session.post(f"https://api.telegram.org/bot{token}/sendPhoto", data=data)
        except Exception as e:
            print(f"[TG] 图片发送失败: {e}")


def sync_tg_notify(message):
    asyncio.run(tg_notify(message))


def sync_tg_notify_photo(photo_path, caption=""):
    asyncio.run(tg_notify_photo(photo_path, caption))


def encrypt_secret(public_key, secret_value):
    pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def update_github_secret(secret_name, secret_value):
    repo_token = os.environ.get("REPO_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo_token or not repository or not NACL_AVAILABLE:
        return False
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {repo_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with aiohttp.ClientSession() as session:
        try:
            pk_url = f"https://api.github.com/repos/{repository}/actions/secrets/public-key"
            async with session.get(pk_url, headers=headers) as resp:
                if resp.status != 200:
                    return False
                pk_data = await resp.json()
            encrypted_value = encrypt_secret(pk_data["key"], secret_value)
            secret_url = f"https://api.github.com/repos/{repository}/actions/secrets/{secret_name}"
            async with session.put(secret_url, headers=headers, json={
                "encrypted_value": encrypted_value, "key_id": pk_data["key_id"]
            }) as resp:
                return resp.status in (201, 204)
        except:
            return False


def get_expiry_from_page(sb):
    try:
        page_text = sb.get_page_source()
        match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', page_text)
        if match:
            return match.group(1).strip()
        match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', page_text)
        if match:
            return match.group(1).strip()
        return "Unknown"
    except:
        return "Unknown"


def is_logged_in(sb):
    try:
        url = sb.get_current_url()
        if "/login" in url or "/auth" in url:
            return False
        if get_expiry_from_page(sb) != "Unknown":
            return True
        if sb.is_element_present("//button//span[contains(text(), '시간추가')]"):
            return True
        return False
    except:
        return False


EXPAND_POPUP_JS = """
(function() {
    var turnstileInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (!turnstileInput) return 'no turnstile input';

    var el = turnstileInput;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var style = window.getComputedStyle(el);
        if (style.overflow === 'hidden' || style.overflowX === 'hidden' || style.overflowY === 'hidden') {
            el.style.overflow = 'visible';
        }
        el.style.minWidth = 'max-content';
    }

    var turnstileContainers = document.querySelectorAll('[class*="sc-fKFyDc"], [class*="nwOmR"]');
    turnstileContainers.forEach(function(container) {
        container.style.overflow = 'visible';
        container.style.width = '300px';
        container.style.minWidth = '300px';
        container.style.height = '65px';
    });

    var iframes = document.querySelectorAll('iframe');
    iframes.forEach(function(iframe) {
        if (iframe.src && iframe.src.includes('challenges.cloudflare.com')) {
            iframe.style.width = '300px';
            iframe.style.height = '65px';
            iframe.style.minWidth = '300px';
            iframe.style.visibility = 'visible';
            iframe.style.opacity = '1';
        }
    });

    return 'done';
})();
"""


def check_turnstile_exists(sb):
    try:
        return sb.execute_script("""
            return document.querySelector('input[name="cf-turnstile-response"]') !== null;
        """)
    except:
        return False


def check_turnstile_solved(sb):
    try:
        return sb.execute_script("""
            var input = document.querySelector('input[name="cf-turnstile-response"]');
            return input && input.value && input.value.length > 20;
        """)
    except:
        return False


def get_turnstile_checkbox_coords(sb):
    try:
        coords = sb.execute_script("""
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {
                var src = iframes[i].src || '';
                if (src.includes('cloudflare') || src.includes('turnstile')) {
                    var rect = iframes[i].getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height,
                            click_x: Math.round(rect.x + 30),
                            click_y: Math.round(rect.y + rect.height / 2)
                        };
                    }
                }
            }
            return null;
        """)
        return coords
    except:
        return None


def activate_browser_window():
    try:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--class", "chrome"],
            capture_output=True, text=True, timeout=3
        )
        window_ids = result.stdout.strip().split('\n')
        if window_ids and window_ids[0]:
            subprocess.run(
                ["xdotool", "windowactivate", window_ids[0]],
                timeout=2, stderr=subprocess.DEVNULL
            )
            time.sleep(0.2)
            return True
    except:
        pass
    return False


def xdotool_click(x, y):
    x, y = int(x), int(y)
    activate_browser_window()
    try:
        subprocess.run(["xdotool", "mousemove", str(x), str(y)], timeout=2, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
        return True
    except:
        pass
    try:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")
        return True
    except:
        return False


def click_turnstile_checkbox(sb):
    coords = get_turnstile_checkbox_coords(sb)
    if not coords:
        print("[!] 无法获取 Turnstile 坐标")
        return False

    print(f"[*] Turnstile 位置: ({coords['x']:.0f}, {coords['y']:.0f})")

    try:
        window_info = sb.execute_script("""
            return {
                screenX: window.screenX || 0,
                screenY: window.screenY || 0,
                outerHeight: window.outerHeight,
                innerHeight: window.innerHeight
            };
        """)
        chrome_bar_height = window_info["outerHeight"] - window_info["innerHeight"]
        abs_x = coords["click_x"] + window_info["screenX"]
        abs_y = coords["click_y"] + window_info["screenY"] + chrome_bar_height
        return xdotool_click(abs_x, abs_y)
    except Exception as e:
        print(f"[!] 坐标计算失败: {e}")
        return False


def check_result_popup(sb):
    try:
        result = sb.execute_script("""
            var buttons = document.querySelectorAll('button');
            var hasNextBtn = false;
            for (var i = 0; i < buttons.length; i++) {
                if (buttons[i].innerText.includes('NEXT') || buttons[i].innerText.includes('Next')) {
                    hasNextBtn = true;
                    break;
                }
            }
            var bodyText = document.body.innerText || '';
            var hasSuccessTitle = bodyText.includes('Success');
            var hasSuccessContent = bodyText.includes('성공') || 
                                    bodyText.includes('갱신') ||
                                    bodyText.includes('연장');
            var hasCooldown = bodyText.includes('아직') || 
                              bodyText.includes('Error');
            if (hasNextBtn || hasSuccessTitle) {
                if (hasCooldown && bodyText.includes('아직')) {
                    return 'cooldown';
                }
                if (hasSuccessTitle && hasSuccessContent) {
                    return 'success';
                }
                if (hasNextBtn) {
                    if (hasCooldown) return 'cooldown';
                    if (hasSuccessContent) return 'success';
                }
            }
            return null;
        """)
        return result
    except:
        return None


def check_popup_still_open(sb):
    try:
        return sb.execute_script("""
            var turnstileInput = document.querySelector('input[name="cf-turnstile-response"]');
            if (!turnstileInput) return false;
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                var text = buttons[i].innerText || '';
                if (text.includes('시간추가') && !text.includes('DELETE')) {
                    var rect = buttons[i].getBoundingClientRect();
                    if (rect.x > 200 && rect.width > 0) {
                        return true;
                    }
                }
            }
            return false;
        """)
    except:
        return False


def click_next_button(sb):
    try:
        next_selectors = [
            "//button[contains(text(), 'NEXT')]",
            "//button[contains(text(), 'Next')]",
            "//button//span[contains(text(), 'NEXT')]",
        ]
        for sel in next_selectors:
            if sb.is_element_visible(sel):
                sb.click(sel)
                return True
    except:
        pass
    return False


def handle_renewal_popup(sb, screenshot_prefix="", timeout=90):
    screenshot_name = f"{screenshot_prefix}_popup.png" if screenshot_prefix else "popup_fixed.png"
    turnstile_ready = False
    
    for _ in range(20):
        result = check_result_popup(sb)
        if result == "cooldown":
            sb.save_screenshot(screenshot_name)
            return {"status": "cooldown", "screenshot": screenshot_name}
        if result == "success":
            sb.save_screenshot(screenshot_name)
            return {"status": "success", "screenshot": screenshot_name}
        if check_turnstile_exists(sb):
            turnstile_ready = True
            break
        time.sleep(1)

    if not turnstile_ready:
        sb.save_screenshot(screenshot_name)
        return {"status": "error", "message": "未检测到 Turnstile", "screenshot": screenshot_name}

    for _ in range(3):
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.5)

    sb.save_screenshot(screenshot_name)

    for attempt in range(6):
        if check_turnstile_solved(sb):
            break
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.3)
        click_turnstile_checkbox(sb)
        for _ in range(8):
            time.sleep(0.5)
            if check_turnstile_solved(sb):
                break
        if check_turnstile_solved(sb):
            break
        sb.save_screenshot(f"{screenshot_prefix}_turnstile_{attempt}.png" if screenshot_prefix else f"turnstile_attempt_{attempt}.png")

    result_timeout = 45
    result_start = time.time()
    last_screenshot_time = 0

    while time.time() - result_start < result_timeout:
        result = check_result_popup(sb)
        if result == "success":
            sb.save_screenshot(screenshot_name)
            time.sleep(1)
            click_next_button(sb)
            return {"status": "success", "screenshot": screenshot_name}
        if result == "cooldown":
            sb.save_screenshot(screenshot_name)
            time.sleep(1)
            click_next_button(sb)
            return {"status": "cooldown", "screenshot": screenshot_name}
        if not check_popup_still_open(sb):
            time.sleep(2)
            result = check_result_popup(sb)
            if result:
                sb.save_screenshot(screenshot_name)
                if result == "success":
                    click_next_button(sb)
                    return {"status": "success", "screenshot": screenshot_name}
                elif result == "cooldown":
                    click_next_button(sb)
                    return {"status": "cooldown", "screenshot": screenshot_name}
        if time.time() - last_screenshot_time > 5:
            sb.save_screenshot(screenshot_name)
            last_screenshot_time = time.time()
        time.sleep(1)

    sb.save_screenshot(screenshot_name)
    return {"status": "timeout", "screenshot": screenshot_name}


def check_and_update_cookie(sb, cookie_env, original_cookie_value):
    try:
        cookies = sb.get_cookies()
        for cookie in cookies:
            if cookie.get("name", "").startswith("remember_web"):
                new_val = cookie.get("value", "")
                cookie_name = cookie.get("name", "")
                if new_val and new_val != original_cookie_value:
                    new_cookie_str = f"{cookie_name}={new_val}"
                    if asyncio.run(update_github_secret(cookie_env, new_cookie_str)):
                        return True
                    else:
                        return False
                break
    except Exception as e:
        pass
    return False


def process_single_account(sb, account, account_index):
    remark = account.get("remark", f"账号{account_index + 1}")
    server_id = account.get("id", "").strip()
    cookie_env = account.get("cookie_env", "").strip()
    display_name = mask_email(remark) if "@" in remark else remark

    result = {
        "remark": remark,
        "display_name": display_name,
        "server_id": server_id,
        "cookie_env": cookie_env,
        "status": "unknown",
        "original_expiry": "Unknown",
        "new_expiry": "Unknown",
        "message": "",
        "screenshot": None,
        "cookie_updated": False,
        "skipped": False
    }

    print(f"\n{'=' * 60}")
    print(f"处理账号 [{account_index + 1}]: {display_name}")
    print(f"{'=' * 60}")

    if not server_id or not cookie_env:
        result["status"] = "error"
        result["message"] = "配置缺失"
        return result

    cookie_str = os.environ.get(cookie_env, "").strip()
    if not cookie_str:
        result["status"] = "error"
        result["message"] = f"{cookie_env} 未设置"
        return result

    cookie_name, cookie_value = parse_weirdhost_cookie(cookie_str)
    server_url = build_server_url(server_id)

    if not cookie_name or not cookie_value:
        result["status"] = "error"
        result["message"] = "Cookie 格式错误"
        return result

    screenshot_prefix = f"account_{account_index + 1}"

    try:
        print("\n[步骤1] 设置 Cookie")
        try:
            sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=3)
            time.sleep(1)
            sb.delete_all_cookies()
        except:
            pass

        sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=3)
        time.sleep(2)

        # ----------------------------------------------------
        # 新增调试逻辑：在注入 Cookie 之前强制收集环境信息并截图
        # ----------------------------------------------------
        try:
            current_url = sb.get_current_url()
            current_title = sb.get_page_title()
            print(f"[*] 注入前页面 URL: {current_url}")
            print(f"[*] 注入前页面标题: {current_title}")
            
            debug_screenshot_path = f"{screenshot_prefix}_debug_pre_cookie.png"
            sb.save_screenshot(debug_screenshot_path)
            print(f"[*] 📸 已保存强制调试截图: {debug_screenshot_path}")
            
            if DOMAIN not in current_url:
                print(f"[!] ⚠️ 警告: 浏览器当前未停留在 {DOMAIN}！这可能会导致注入 Cookie 失败。")
        except Exception as e:
            print(f"[!] 获取页面环境信息失败: {e}")

        # ----------------------------------------------------
        # 异常捕获机制：防止直接崩溃退出
        # ----------------------------------------------------
        try:
            sb.add_cookie({
                "name": cookie_name, "value": cookie_value,
                "domain": DOMAIN, "path": "/"
            })
            print("[+] Cookie 已成功设置")
        except Exception as cookie_err:
            print(f"[!] ❌ 致命错误: 无法注入 Cookie: {cookie_err}")
            err_screenshot_path = f"{screenshot_prefix}_cookie_fail.png"
            sb.save_screenshot(err_screenshot_path)
            result["status"] = "error"
            result["message"] = "无法注入Cookie(域名不符/已被拦截)"
            result["screenshot"] = err_screenshot_path
            return result

        print("\n[步骤2] 获取到期时间")
        sb.uc_open_with_reconnect(server_url, reconnect_time=5)
        time.sleep(3)

        if not is_logged_in(sb):
            sb.add_cookie({
                "name": cookie_name, "value": cookie_value,
                "domain": DOMAIN, "path": "/"
            })
            sb.uc_open_with_reconnect(server_url, reconnect_time=5)
            time.sleep(3)

        if not is_logged_in(sb):
            screenshot_path = f"{screenshot_prefix}_login_failed.png"
            sb.save_screenshot(screenshot_path)
            result["status"] = "error"
            result["message"] = "Cookie 失效，请重新获取"
            result["screenshot"] = screenshot_path
            return result

        original_expiry = get_expiry_from_page(sb)
        remaining_days = get_remaining_days(original_expiry)
        result["original_expiry"] = original_expiry

        need_renew = should_renew(original_expiry)
        if not need_renew:
            result["status"] = "skipped"
            result["skipped"] = True
            result["new_expiry"] = original_expiry
            result["message"] = "无需续期"
            if check_and_update_cookie(sb, cookie_env, cookie_value):
                result["cookie_updated"] = True
            return result

        print("\n[步骤4] 点击侧栏续期按钮")
        random_delay(1.0, 2.0)
        sidebar_btn_xpath = "//button//span[contains(text(), '시간추가')]/parent::button"
        if not sb.is_element_present(sidebar_btn_xpath):
            sidebar_btn_xpath = "//button[contains(., '시간추가')]"

        if not sb.is_element_present(sidebar_btn_xpath):
            screenshot_path = f"{screenshot_prefix}_no_button.png"
            sb.save_screenshot(screenshot_path)
            result["status"] = "error"
            result["message"] = "未找到续期按钮"
            result["screenshot"] = screenshot_path
            return result

        sb.click(sidebar_btn_xpath)
        time.sleep(3)

        print("\n[步骤5] 处理续期弹窗")
        popup_result = handle_renewal_popup(sb, screenshot_prefix=screenshot_prefix, timeout=90)
        result["screenshot"] = popup_result.get("screenshot")

        print("\n[步骤6] 验证续期结果")
        time.sleep(3)
        sb.uc_open_with_reconnect(server_url, reconnect_time=3)
        time.sleep(3)

        new_expiry = get_expiry_from_page(sb)
        result["new_expiry"] = new_expiry

        original_dt = parse_expiry_to_datetime(original_expiry)
        new_dt = parse_expiry_to_datetime(new_expiry)

        if popup_result["status"] == "cooldown":
            result["status"] = "cooldown"
            result["message"] = "冷却期内"
        elif original_dt and new_dt and new_dt > original_dt:
            diff_h = (new_dt - original_dt).total_seconds() / 3600
            result["status"] = "success"
            result["message"] = f"延长了 {diff_h:.1f} 小时"
        elif popup_result["status"] == "success":
            result["status"] = "success"
            result["message"] = "操作完成"
        else:
            result["status"] = popup_result["status"]
            result["message"] = popup_result.get("message", "未知状态")

        if check_and_update_cookie(sb, cookie_env, cookie_value):
            result["cookie_updated"] = True

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)[:100]

    return result


def send_summary_report(results):
    success_count = sum(1 for r in results if r["status"] == "success")
    skipped_count = sum(1 for r in results if r["status"] == "skipped")
    error_count = sum(1 for r in results if r["status"] in ["error", "timeout", "unknown", "cooldown"])

    lines = [
        "🎁 <b>Weirdhost 多账号续期报告</b>",
        "",
        f"📊 共 {len(results)} 个账号",
        f"✅ 成功: {success_count}  ⏭️ 跳过: {skipped_count}  ❌ 失败: {error_count}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]

    for i, r in enumerate(results):
        status_icon = {
            "success": "✅",
            "cooldown": "⏳",
            "skipped": "⏭️",
            "error": "❌",
            "timeout": "⚠️"
        }.get(r["status"], "❓")
        remark = r.get("remark", f"账号{i+1}")
        lines.append(f"\n{status_icon} <b>{remark}</b>")
        if r.get("message"):
            lines.append(f"   📝 {r['message']}")

    message = "\n".join(lines)
    
    screenshot = None
    for r in results:
        if r["status"] in ["success", "cooldown", "error", "timeout"]:
            if r.get("screenshot") and os.path.exists(r["screenshot"]):
                screenshot = r["screenshot"]
                break

    if screenshot:
        sync_tg_notify_photo(screenshot, message)
    else:
        sync_tg_notify(message)


def add_server_time():
    accounts = parse_accounts()
    if not accounts:
        return

    results = []
    try:
        with SB(
            uc=True,
            test=True,
            locale="ko",
            headless=False,
            chromium_arg="--disable-dev-shm-usage,--no-sandbox,--disable-gpu,--disable-software-rasterizer,--disable-background-timer-throttling"
        ) as sb:
            for i, account in enumerate(accounts):
                result = process_single_account(sb, account, i)
                results.append(result)
                if i < len(accounts) - 1:
                    time.sleep(random.randint(2, 4))
    except Exception as e:
        if results:
            send_summary_report(results)
        return

    send_summary_report(results)

if __name__ == "__main__":
    add_server_time()
