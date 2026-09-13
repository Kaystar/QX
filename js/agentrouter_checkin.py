#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ql author: AgentRouter
# ql name: AgentRouter签到
# ql cron: 0 9 * * *
# ql desc: AgentRouter 多账号自动签到，确认签到日志并查询签到后最新余额

"""
AgentRouter 自动签到脚本 (青龙面板 / 任意 Python3 环境)
站点: https://agentrouter.org

===== 签到原理 =====
本站签到 = 完成一次账号密码登录:

  向 POST /api/user/login 发送 {username: 邮箱, password: 密码}
  -> 登录接口返回 success=true，即表示本次签到流程完成。
  -> data.checked_in=true 通常表示本次登录触发了当天签到/额度发放；
     false 通常表示此前已经完成或本次没有重复发放，不影响“登录成功即签到成功”。

旧接口 POST /api/user/checkin 已失效，当前不应继续调用。

登录成功后查询 GET /api/user/self 获取签到后的最新余额。最新余额查询失败时
保留登录响应额度，并在输出和通知中明确标注“最新余额未刷新”。

随后可选查询 GET /api/log/self/ 作为日志侧证。日志查询失败不会影响签到结论，
但会在输出和通知中明确标注“日志未确认”。

===== 配置方式 =====
单账号:
  AGENTROUTER_ACCOUNT  必填. 格式: 邮箱#密码
    例: user@example.com#你的密码

多账号(可选):
  AGENTROUTER_ACCOUNTS 选填. JSON 数组:
    [{"name":"甲","account":"a@x.com#pwdA"},
     {"name":"乙","account":"b@x.com#pwdB"}]

  兼容旧格式: {"name":"...","email":"...","password":"..."}
  设置了多账号变量后，不再回退到单账号变量。

===== 可选环境变量 =====
  AGENTROUTER_BASE_URL          默认 https://agentrouter.org
                                备用 https://ps.air-outer.com
  AGENTROUTER_PROXY             代理地址, 如 http://127.0.0.1:10808
  AGENTROUTER_FORCE_IPV4=1      强制 IPv4
  AGENTROUTER_TIMEOUT=20        单次请求超时秒数, 默认 20
  AGENTROUTER_MAX_ATTEMPTS=3    最大尝试次数, 默认 3
  AGENTROUTER_RETRY_BACKOFF=1   重试基础等待秒数, 默认 1
  AGENTROUTER_VERIFY_LOGS=1     登录后查询日志, 默认开启
  AGENTROUTER_TIMEZONE_OFFSET=8 日志“当天”判断时区, 默认北京时间 UTC+8

===== 青龙定时 =====
  命令: task agentrouter_checkin.py
  Cron: 0 9 * * *
  依赖: requests

退出码: 全部账号成功/已签到返回 0；配置错误、任一账号失败或未处理异常返回 1。
"""

import json
import math
import os
import random
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("缺少依赖 requests, 请先执行: pip install requests")
    sys.exit(2)


# ---------- 环境变量解析 ----------
def _env_int(name, default, minimum, maximum):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _env_float(name, default, minimum, maximum):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(minimum, min(maximum, value))


def _env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _normalize_base_url(raw):
    value = (raw or "https://agentrouter.org").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"无效的 AGENTROUTER_BASE_URL: {value!r}")
    if parsed.query or parsed.fragment:
        raise ValueError("AGENTROUTER_BASE_URL 不能包含查询参数或片段")
    return value


try:
    BASE_URL = _normalize_base_url(os.environ.get("AGENTROUTER_BASE_URL"))
except ValueError as exc:
    print(f"配置错误: {exc}")
    sys.exit(2)

LOGIN_PATH = "/api/user/login"
SELF_LOG_PATH = "/api/log/self/"
SELF_INFO_PATH = "/api/user/self"
USER_ID_HEADER = "New-API-User"
CHECKIN_LOG_TYPE = 4

TIMEOUT_SECONDS = _env_float("AGENTROUTER_TIMEOUT", 20.0, 1.0, 120.0)
REQUEST_TIMEOUT = (min(10.0, TIMEOUT_SECONDS), TIMEOUT_SECONDS)
MAX_ATTEMPTS = _env_int("AGENTROUTER_MAX_ATTEMPTS", 3, 1, 10)
RETRY_BACKOFF = _env_float("AGENTROUTER_RETRY_BACKOFF", 1.0, 0.0, 30.0)
VERIFY_LOGS = _env_bool("AGENTROUTER_VERIFY_LOGS", True)
TIMEZONE_OFFSET = _env_float("AGENTROUTER_TIMEZONE_OFFSET", 8.0, -12.0, 14.0)
LOCAL_TZ = timezone(timedelta(hours=TIMEZONE_OFFSET))

PROXY = os.environ.get("AGENTROUTER_PROXY", "").strip()
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None
RETRYABLE_STATUS_CODES = frozenset((408, 425, 429, 500, 502, 503, 504))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)

if os.environ.get("AGENTROUTER_FORCE_IPV4", "").strip().lower() in ("1", "true", "yes", "on"):
    import socket as _socket

    _orig_getaddrinfo = _socket.getaddrinfo

    def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, _socket.AF_INET, type, proto, flags)

    _socket.getaddrinfo = _getaddrinfo_ipv4

# 青龙自带 notify；缺失时仅打印，不影响签到。
send = None
try:
    from notify import send  # type: ignore
except Exception:
    send = None


# ---------- 通用工具 ----------
def log(message):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    line = f"[{ts}] {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # Windows GBK 控制台无法输出 emoji 时，降级为当前编码可接受的字符，
        # 避免日志本身触发异常并掩盖真正的签到结果。
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        fallback = line.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(fallback, flush=True)


def compact_text(value, limit=240):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def safe_notify(title, content):
    if not send:
        log(f"[通知] {title}\n{content}")
        return
    try:
        send(title, content)
    except Exception as exc:
        log(f"通知发送失败(不影响签到): {compact_text(exc)}")


def parse_bool(value):
    """兼容 bool、0/1 和字符串形式的 true/false。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on", "ok")
    return False


def parse_timestamp(value):
    """解析秒/毫秒时间戳或 ISO 8601，返回整数秒；无法解析返回 None。"""
    if isinstance(value, bool) or value is None:
        return None

    timestamp = None
    if isinstance(value, (int, float)):
        timestamp = float(value)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            timestamp = float(raw)
        except ValueError:
            try:
                normalized = raw.replace("Z", "+00:00")
                parsed = datetime.fromisoformat(normalized)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                timestamp = parsed.timestamp()
            except (TypeError, ValueError, OverflowError):
                return None

    if timestamp is None or not math.isfinite(timestamp) or timestamp <= 0:
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return int(timestamp)


def day_key(timestamp):
    return datetime.fromtimestamp(timestamp, tz=LOCAL_TZ).strftime("%Y-%m-%d")


def format_ago(now, timestamp):
    ago = max(0, int(now - timestamp))
    if ago < 60:
        return f"{ago} 秒前"
    if ago < 3600:
        return f"{ago // 60} 分钟前"
    if ago < 86400:
        return f"{ago // 3600} 小时前"
    return f"{ago // 86400} 天前"


def parse_account(raw):
    """把 '邮箱#密码' 拆成 (email, password)，密码中可包含 #。"""
    if not isinstance(raw, str):
        return "", ""
    raw = raw.strip()
    if "#" not in raw:
        return raw, ""
    email, password = raw.split("#", 1)
    return email.strip(), password.strip()


def extract_quota(payload):
    """从用户对象或其常见嵌套结构中提取余额字段。"""
    if not isinstance(payload, dict):
        return None

    candidates = [payload]
    for key in ("user", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
            nested_user = nested.get("user")
            if isinstance(nested_user, dict):
                candidates.append(nested_user)

    for candidate in candidates:
        for key in ("quota", "remainder_quota", "balance"):
            if key in candidate and candidate[key] is not None:
                return candidate[key]
    return None


def normalize_uid(value):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return text if text.isdigit() else None


def response_is_html(response):
    try:
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if "text/html" in content_type:
            return True
        text = str(getattr(response, "text", "") or "").lstrip().lower()
        return text.startswith("<!doctype html") or text.startswith("<html")
    except Exception:
        return False


def response_message(response):
    try:
        payload = response.json()
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("error")
            if isinstance(message, dict):
                message = message.get("message") or message.get("code")
            if message:
                return compact_text(message)
    except Exception:
        pass
    return compact_text(getattr(response, "text", ""))


def _retry_after_seconds(header_value):
    if not header_value:
        return None
    try:
        value = float(str(header_value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return max(0.0, value)


def _retry_delay(attempt, retry_after=None):
    base = RETRY_BACKOFF * (2 ** max(0, attempt - 1))
    if retry_after is not None:
        base = max(base, retry_after)
    base = min(60.0, base)
    return base + random.uniform(0.0, min(0.5, base * 0.1 + 0.05))


def request_with_retries(session, method, url, operation, **kwargs):
    """
    执行 HTTP 请求并处理网络异常/429/5xx。
    返回 (response, error_text)。error_text 非空时应直接按失败处理。
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            if attempt >= MAX_ATTEMPTS:
                return None, (
                    f"{operation}失败：网络异常（已尝试 {MAX_ATTEMPTS} 次）："
                    f"{compact_text(exc)}"
                )
            delay = _retry_delay(attempt)
            log(f"{operation}网络异常，{delay:.1f} 秒后重试 "
                f"({attempt}/{MAX_ATTEMPTS}): {compact_text(exc, 120)}")
            time.sleep(delay)
            continue
        except Exception as exc:
            return None, (
                f"{operation}失败：请求初始化异常 "
                f"{type(exc).__name__}: {compact_text(exc)}"
            )

        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code not in RETRYABLE_STATUS_CODES:
            return response, None

        if attempt >= MAX_ATTEMPTS:
            detail = response_message(response)
            suffix = f"；响应: {detail}" if detail else ""
            return response, (
                f"{operation}失败：HTTP {status_code} "
                f"（已尝试 {MAX_ATTEMPTS} 次）{suffix}"
            )

        retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
        delay = _retry_delay(attempt, retry_after)
        log(f"{operation}返回 HTTP {status_code}，{delay:.1f} 秒后重试 "
            f"({attempt}/{MAX_ATTEMPTS})")
        try:
            response.close()
        except Exception:
            pass
        time.sleep(delay)

    return None, f"{operation}失败：超过最大尝试次数"


# ---------- 签到日志核验 ----------
def verify_checkin(session, uid, slack_new=300):
    """
    查询个人日志，仅作为登录成功的补充证据。

    返回 (level, detail, timestamp, content):
      new      本机时间窗口内出现签到日志
      today    配置时区下当天出现签到日志
      none     没有找到当天签到日志
      error    日志接口异常或缺少 uid
    """
    uid = normalize_uid(uid)
    if not uid:
        return "error", "缺少有效 uid, 跳过日志核验", None, None

    response, request_error = request_with_retries(
        session,
        "GET",
        f"{BASE_URL}{SELF_LOG_PATH}",
        "日志查询",
        params={"p": 1, "page_size": 20},
        headers={USER_ID_HEADER: uid},
    )
    if request_error:
        return "error", request_error, None, None

    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code != 200:
        return "error", f"日志接口返回 HTTP {status_code}", None, None
    if response_is_html(response):
        return "error", "日志接口返回 HTML（可能被 WAF 拦截）", None, None

    try:
        payload = response.json()
    except Exception as exc:
        return "error", f"日志响应非 JSON: {compact_text(exc)}", None, None

    if not isinstance(payload, dict):
        return "error", "日志响应结构异常: 顶层不是 JSON 对象", None, None
    if "success" in payload and not parse_bool(payload.get("success")):
        return "error", f"日志接口返回失败: {compact_text(payload.get('message'))}", None, None

    data = payload.get("data")
    if isinstance(data, dict):
        items = data.get("items") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    if not isinstance(items, list):
        return "error", "日志响应结构异常: items 不是数组", None, None

    newest_ts = None
    newest_content = None
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        content_lower = content.lower()
        log_type = item.get("type")
        type_is_checkin = str(log_type) == str(CHECKIN_LOG_TYPE)
        is_checkin_log = (
            "签到成功" in content
            or ("签到" in content and type_is_checkin)
            or "check-in successful" in content_lower
            or ("daily check-in" in content_lower and type_is_checkin)
        )
        if not is_checkin_log:
            continue

        timestamp = parse_timestamp(
            item.get("created_at")
            if item.get("created_at") is not None
            else item.get("timestamp")
        )
        if timestamp is None:
            continue
        if newest_ts is None or timestamp > newest_ts:
            newest_ts = timestamp
            newest_content = content

    if newest_ts is None:
        return "none", "日志中未找到有效签到记录", None, None

    now = int(time.time())
    ago = format_ago(now, newest_ts)
    if newest_ts >= now - max(0, int(slack_new)):
        return "new", f"本次运行已生成签到日志（{ago}）", newest_ts, newest_content
    if day_key(newest_ts) == day_key(now):
        return "today", f"当天已存在签到日志（{ago}）", newest_ts, newest_content
    return "none", f"最近签到日志不在当天（{ago}）", newest_ts, newest_content


# ---------- 最新余额查询 ----------
def fetch_latest_quota(session, uid):
    """登录后查询 /api/user/self，返回 (最新余额, 说明)。"""
    uid = normalize_uid(uid)
    if not uid:
        return None, "缺少有效 uid, 无法查询最新余额"

    response, request_error = request_with_retries(
        session,
        "GET",
        f"{BASE_URL}{SELF_INFO_PATH}",
        "最新余额查询",
        headers={USER_ID_HEADER: uid},
    )
    if request_error:
        return None, request_error

    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code != 200:
        detail = response_message(response)
        suffix = f": {detail}" if detail else ""
        return None, f"用户信息接口返回 HTTP {status_code}{suffix}"
    if response_is_html(response):
        return None, "用户信息接口返回 HTML（可能被 WAF 拦截）"

    try:
        payload = response.json()
    except Exception as exc:
        return None, f"用户信息响应非 JSON: {compact_text(exc)}"

    if not isinstance(payload, dict):
        return None, "用户信息响应结构异常: 顶层不是 JSON 对象"
    if "success" in payload and not parse_bool(payload.get("success")):
        return None, f"用户信息接口返回失败: {compact_text(payload.get('message'))}"

    quota = extract_quota(payload)
    if quota is None:
        return None, "用户信息响应中未找到 quota/remainder_quota/balance"
    return quota, "查询成功"


# ---------- 登录签到 ----------
def password_login(account):
    name = str(account.get("name") or "默认账号")
    email = str(account.get("email") or "").strip()
    password = str(account.get("password") or "").strip()
    if not email or not password:
        return _result(name, "fail", "未配置 email/password, 跳过", None, None)

    log(f"====== 开始处理账号: {name} ======")
    try:
        with requests.Session() as session:
            session.headers.update({
                "User-Agent": UA,
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Cache-Control": "no-store",
                "Referer": f"{BASE_URL}/login",
                "Origin": BASE_URL,
            })
            if PROXIES:
                session.proxies.update(PROXIES)

            response, request_error = request_with_retries(
                session,
                "POST",
                f"{BASE_URL}{LOGIN_PATH}",
                "登录",
                json={"username": email, "password": password},
            )
            if request_error:
                return _result(name, "fail", request_error, None, None)
            if response_is_html(response):
                return _result(
                    name,
                    "fail",
                    "登录接口返回 HTML（可能被 WAF 拦截或路径变化）",
                    None,
                    None,
                )

            try:
                payload = response.json()
            except Exception as exc:
                return _result(
                    name,
                    "fail",
                    f"登录响应非 JSON（HTTP {response.status_code}）: {compact_text(exc)}",
                    None,
                    None,
                )

            if not isinstance(payload, dict):
                return _result(name, "fail", "登录响应结构异常: 顶层不是 JSON 对象", None, None)

            if not parse_bool(payload.get("success")):
                detail = payload.get("message") or response_message(response)
                status_code = int(getattr(response, "status_code", 0) or 0)
                return _result(
                    name,
                    "fail",
                    f"登录失败（HTTP {status_code}）: {compact_text(detail)}",
                    None,
                    None,
                )

            raw_data = payload.get("data")
            data = raw_data if isinstance(raw_data, dict) else {}
            checked_in = parse_bool(data.get("checked_in"))
            uid = normalize_uid(data.get("id"))
            username = data.get("username") or data.get("display_name") or email
            quota = extract_quota(data)

            latest_quota, balance_detail = fetch_latest_quota(session, uid)
            if latest_quota is not None:
                quota = latest_quota
                balance_note = f"最新余额: {quota}"
            else:
                balance_note = f"最新余额未刷新: {balance_detail}"

            if VERIFY_LOGS:
                level, detail, _, _ = verify_checkin(session, uid)
            else:
                level, detail = "disabled", "日志核验已关闭"

            if level in ("new", "today"):
                log_note = f"日志已确认: {detail}"
            elif level == "disabled":
                log_note = detail
            else:
                log_note = f"日志未确认: {detail}"

            if checked_in:
                message = f"登录成功，本次已触发签到；{log_note}；{balance_note}"
                status = "success"
            else:
                message = f"登录成功，签到流程已完成（可能此前已签到）；{log_note}；{balance_note}"
                status = "already" if level in ("new", "today") else "success"

            return _result(name, status, message, str(username), quota)
    except Exception as exc:
        log(f"[{name}] 登录处理异常:\n{traceback.format_exc()}")
        return _result(
            name,
            "fail",
            f"未处理异常 {type(exc).__name__}: {compact_text(exc)}",
            None,
            None,
        )


def do_checkin(account):
    email = str(account.get("email") or "").strip()
    password = str(account.get("password") or "").strip()
    if email and password:
        return password_login(account)
    return _result(
        str(account.get("name") or "默认账号"),
        "fail",
        "账号未配置 email/password, 跳过",
        None,
        None,
    )


def _result(name, status, message, username, quota):
    result = {
        "name": name,
        "status": status,
        "message": message,
        "username": username or "",
        "quota": quota,
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }
    tag = {
        "success": "✅ 成功",
        "already": "🟡 已签到",
        "fail": "❌ 失败",
    }.get(status, status)
    quota_text = "未知" if quota is None else str(quota)
    log(f"[{name}] {tag} | {message} | 额度: {quota_text}")
    return result


def collect_accounts():
    multi = os.environ.get("AGENTROUTER_ACCOUNTS", "").strip()
    if multi:
        try:
            raw_accounts = json.loads(multi)
        except Exception as exc:
            log(f"AGENTROUTER_ACCOUNTS JSON 解析失败: {compact_text(exc)}")
            return []
        if not isinstance(raw_accounts, list):
            log("AGENTROUTER_ACCOUNTS 必须是 JSON 数组")
            return []

        accounts = []
        seen = set()
        for index, item in enumerate(raw_accounts):
            default_name = f"账号{index + 1}"
            if not isinstance(item, dict):
                log(f"[{default_name}] 配置项不是对象, 已跳过")
                continue

            name = str(item.get("name") or default_name).strip() or default_name
            account_text = item.get("account")
            email, password = parse_account(account_text) if isinstance(account_text, str) else ("", "")

            if not email or not password:
                old_email = item.get("email")
                old_password = item.get("password")
                if isinstance(old_email, str) and isinstance(old_password, str):
                    email = old_email.strip()
                    password = old_password.strip()

            if not email or not password:
                log(f"[{name}] 缺少有效的 account 或 email/password, 已跳过")
                continue

            key = email.casefold()
            if key in seen:
                log(f"[{name}] 与前面的账号重复, 已跳过")
                continue
            seen.add(key)
            accounts.append({"name": name, "email": email, "password": password})

        if not accounts:
            log("AGENTROUTER_ACCOUNTS 中没有有效账号")
            return []
        log(f"已读取多账号配置, 共 {len(accounts)} 个")
        return accounts

    single = os.environ.get("AGENTROUTER_ACCOUNT", "").strip()
    if single:
        email, password = parse_account(single)
        if email and password:
            log("已读取单账号配置")
            return [{"name": "默认账号", "email": email, "password": password}]
        log("AGENTROUTER_ACCOUNT 格式错误, 应为 邮箱#密码")
        return []

    log("未检测到账号配置: 请设置 AGENTROUTER_ACCOUNT 或 AGENTROUTER_ACCOUNTS")
    return []


def main():
    log("AgentRouter 自动签到启动")
    accounts = collect_accounts()
    if not accounts:
        safe_notify("[AgentRouter] 签到失败", "未检测到有效账号配置, 请检查环境变量")
        return 1

    results = []
    for index, account in enumerate(accounts):
        try:
            result = do_checkin(account)
            if isinstance(result, dict):
                results.append(result)
            else:
                name = str(account.get("name") or "?")
                results.append(_result(
                    name,
                    "fail",
                    "签到处理未返回有效结果",
                    None,
                    None,
                ))
        except Exception as exc:
            name = str(account.get("name") or "?")
            log(f"[{name}] 处理异常:\n{traceback.format_exc()}")
            results.append(_result(
                name,
                "fail",
                f"未处理异常 {type(exc).__name__}: {compact_text(exc)}",
                None,
                None,
            ))

        if index < len(accounts) - 1:
            time.sleep(random.uniform(10.0, 20.0))

    failures = [item for item in results if item.get("status") not in ("success", "already")]
    lines = []
    for item in results:
        tag = {
            "success": "✅",
            "already": "🟡",
            "fail": "❌",
        }.get(item.get("status"), "❔")
        quota_text = "未知" if item.get("quota") is None else str(item.get("quota"))
        who = item.get("username") or item.get("name")
        lines.append(
            f"{tag} {item.get('name')}({who})：{item.get('message')} | 额度 {quota_text}"
        )

    if failures:
        title = "[AgentRouter] 签到部分失败" if len(failures) < len(results) else "[AgentRouter] 签到失败"
    else:
        title = "[AgentRouter] 签到汇总"
    safe_notify(title, "\n".join(lines))
    log(f"处理完毕: 成功/已签到 {len(results) - len(failures)} 个, 失败 {len(failures)} 个")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())