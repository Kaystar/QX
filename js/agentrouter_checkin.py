#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ql author: AgentRouter
# ql name: AgentRouter签到
# ql cron: 0 9 * * *
# ql desc: AgentRouter 多账号自动签到，通知显示奖励、美元余额与历史消耗

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

登录成功后查询 GET /api/user/self 获取最新余额和 used_quota 累计历史消耗，
并折算为美元显示。签到奖励优先从登录响应、签到状态接口和签到日志中提取；
无法可靠获取时省略“+$X”，不影响签到结论。

通知正文保持简洁，只显示签到状态/奖励、美元余额和历史消耗；详细日志仅写控制台。

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
  AGENTROUTER_QUOTA_PER_UNIT=500000 额度换算单位, 500000 额度约等于 $1

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
import re
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
CHECKIN_STATUS_PATH = '/api/user/checkin'

TIMEOUT_SECONDS = _env_float("AGENTROUTER_TIMEOUT", 20.0, 1.0, 120.0)
REQUEST_TIMEOUT = (min(10.0, TIMEOUT_SECONDS), TIMEOUT_SECONDS)
MAX_ATTEMPTS = _env_int("AGENTROUTER_MAX_ATTEMPTS", 3, 1, 10)
RETRY_BACKOFF = _env_float("AGENTROUTER_RETRY_BACKOFF", 1.0, 0.0, 30.0)
VERIFY_LOGS = _env_bool("AGENTROUTER_VERIFY_LOGS", True)
TIMEZONE_OFFSET = _env_float("AGENTROUTER_TIMEZONE_OFFSET", 8.0, -12.0, 14.0)
LOCAL_TZ = timezone(timedelta(hours=TIMEZONE_OFFSET))
QUOTA_PER_UNIT = _env_float("AGENTROUTER_QUOTA_PER_UNIT", 500000.0, 1.0, 1_000_000_000_000.0)

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


def format_quota(value, plus=False, trim_cents=False):
    """把内部 quota 折算为美元；可用于余额或本次奖励。"""
    if value is None:
        return "未知"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return str(value)
    usd = number / QUOTA_PER_UNIT
    if trim_cents and usd.is_integer():
        usd_text = f"{usd:,.0f}"
    else:
        usd_text = f"{usd:,.2f}"
    prefix = "+" if plus and usd >= 0 else ""
    return f"{prefix}${usd_text}"


def safe_notify(title, content):
    if not send:
        log(f"[通知] {title}\n{content}")
        return
    try:
        send(title, content)
    except Exception as exc:
        log(f"通知发送失败(不影响签到): {compact_text(exc)}")


def parse_number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def parse_award_from_text(content):
    """从签到日志文案中尽量提取本次奖励，统一返回内部 quota。"""
    text = str(content or "").strip()
    if not text:
        return None

    # "获得额度 ＄25.000000 额度" / "增加额度 $25" 等美元文案。
    currency_patterns = (
        r"(?:签到|奖励|增加|获得|发放)[^0-9+＋$＄]{0,30}[+＋]?\s*[＄$]\s*([0-9]+(?:\.[0-9]+)?)",
        r"[＄$]\s*([0-9]+(?:\.[0-9]+)?)",
    )
    for pattern in currency_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1)) * QUOTA_PER_UNIT

    # "+25" 等没有货币符号的文案：较小数值按美元，较大数值按内部额度。
    match = re.search(r"[+＋]\s*([0-9]+(?:\.[0-9]+)?)", text)
    if match:
        value = float(match.group(1))
        return value * QUOTA_PER_UNIT if value <= 1000 else value

    # "增加额度 12500000" / "12500000 点额度" 等内部额度文案。
    match = re.search(
        r"(?:额度|点数|积分)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)|([0-9]{4,}(?:\.[0-9]+)?)\s*(?:点|积分|额度)",
        text,
        re.IGNORECASE,
    )
    if match:
        raw = match.group(1) or match.group(2)
        value = float(raw)
        return value * QUOTA_PER_UNIT if value <= 1000 else value
    return None


def extract_checkin_award(payload):
    """从接口响应中提取本次签到奖励。"""
    for key in ("quota_awarded", "award_quota", "checkin_quota", "quota_change"):
        number = parse_number(extract_user_metric(payload, (key,)))
        if number is not None and number > 0:
            return number
    return None


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


def _user_payload_candidates(payload):
    """返回用户对象及其常见嵌套对象。"""
    if not isinstance(payload, dict):
        return []
    candidates = [payload]
    for key in ("user", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
            nested_user = nested.get("user")
            if isinstance(nested_user, dict):
                candidates.append(nested_user)
    return candidates


def extract_user_metric(payload, keys):
    """从用户对象中提取首个有效字段。"""
    for candidate in _user_payload_candidates(payload):
        for key in keys:
            if key in candidate and candidate[key] is not None:
                return candidate[key]
    return None


def extract_quota(payload):
    """从用户对象中提取余额字段。"""
    return extract_user_metric(payload, ("quota", "remainder_quota", "balance"))


def extract_used_quota(payload):
    """从用户对象中提取累计历史消耗字段。"""
    return extract_user_metric(payload, ("used_quota",))




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
    查询个人日志，作为签到成功的补充证据，并尽量提取本次奖励。

    返回 (level, detail, timestamp, content, award_quota):
      new      本机时间窗口内出现签到日志
      today    配置时区下当天出现签到日志
      none     没有找到当天签到日志
      error    日志接口异常或缺少 uid
    """
    uid = normalize_uid(uid)
    if not uid:
        return "error", "缺少有效 uid, 跳过日志核验", None, None, None

    response, request_error = request_with_retries(
        session,
        "GET",
        f"{BASE_URL}{SELF_LOG_PATH}",
        "日志查询",
        params={"p": 1, "page_size": 20},
        headers={USER_ID_HEADER: uid},
    )
    if request_error:
        return "error", request_error, None, None, None

    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code != 200:
        return "error", f"日志接口返回 HTTP {status_code}", None, None, None
    if response_is_html(response):
        return "error", "日志接口返回 HTML（可能被 WAF 拦截）", None, None, None

    try:
        payload = response.json()
    except Exception as exc:
        return "error", f"日志响应非 JSON: {compact_text(exc)}", None, None, None

    if not isinstance(payload, dict):
        return "error", "日志响应结构异常: 顶层不是 JSON 对象", None, None, None
    if "success" in payload and not parse_bool(payload.get("success")):
        return "error", f"日志接口返回失败: {compact_text(payload.get('message'))}", None, None, None

    data = payload.get("data")
    if isinstance(data, dict):
        items = data.get("items") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    if not isinstance(items, list):
        return "error", "日志响应结构异常: items 不是数组", None, None, None

    newest_ts = None
    newest_content = None
    newest_award = None
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

        award = parse_award_from_text(content)
        if award is None:
            award = extract_checkin_award(item)
        if award is None:
            quota_value = parse_number(item.get("quota"))
            if quota_value is not None and quota_value > 0:
                award = quota_value

        if newest_ts is None or timestamp > newest_ts:
            newest_ts = timestamp
            newest_content = content
            newest_award = award

    if newest_ts is None:
        return "none", "日志中未找到有效签到记录", None, None, None

    now = int(time.time())
    ago = format_ago(now, newest_ts)
    if newest_ts >= now - max(0, int(slack_new)):
        return "new", f"本次运行已生成签到日志（{ago}）", newest_ts, newest_content, newest_award
    if day_key(newest_ts) == day_key(now):
        return "today", f"当天已存在签到日志（{ago}）", newest_ts, newest_content, newest_award
    return "none", f"最近签到日志不在当天（{ago}）", newest_ts, newest_content, newest_award

# ---------- 最新余额与历史消耗查询 ----------
def fetch_latest_user_info(session, uid):
    """登录后查询 /api/user/self，返回 (余额与历史消耗, 说明)。"""
    uid = normalize_uid(uid)
    if not uid:
        return None, "缺少有效 uid, 无法查询最新余额"

    response, request_error = request_with_retries(
        session,
        "GET",
        f"{BASE_URL}{SELF_INFO_PATH}",
        "余额查询",
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

    info = {
        "quota": extract_quota(payload),
        "used_quota": extract_used_quota(payload),
    }
    if info["quota"] is None and info["used_quota"] is None:
        return None, "用户信息响应中未找到 quota 和 used_quota"
    return info, "查询成功"


def fetch_checkin_award(session, uid):
    """从签到状态接口读取当天记录，返回本次/当天奖励的内部 quota。"""
    uid = normalize_uid(uid)
    if not uid:
        return None

    now = time.time()
    month = datetime.fromtimestamp(now, tz=LOCAL_TZ).strftime("%Y-%m")
    today = datetime.fromtimestamp(now, tz=LOCAL_TZ).strftime("%Y-%m-%d")
    response, request_error = request_with_retries(
        session,
        "GET",
        f"{BASE_URL}{CHECKIN_STATUS_PATH}",
        "签到奖励查询",
        params={"month": month},
        headers={USER_ID_HEADER: uid},
    )
    if request_error or response is None:
        return None

    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code != 200 or response_is_html(response):
        return None

    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict) or not parse_bool(payload.get("success")):
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    stats = data.get("stats") if isinstance(data.get("stats"), dict) else data
    records = stats.get("records") if isinstance(stats, dict) else None
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            if str(record.get("checkin_date") or "") != today:
                continue
            award = parse_number(record.get("quota_awarded"))
            if award is not None and award > 0:
                return award

    return extract_checkin_award(data)


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
            used_quota = extract_used_quota(data)
            checkin_award = extract_checkin_award(payload)

            latest_info, balance_detail = fetch_latest_user_info(session, uid)
            info_notes = []
            if latest_info is not None:
                latest_quota = latest_info.get("quota")
                if latest_quota is not None:
                    quota = latest_quota
                else:
                    info_notes.append("最新余额未刷新: 响应缺少 quota")

                latest_used_quota = latest_info.get("used_quota")
                if latest_used_quota is not None:
                    used_quota = latest_used_quota
            else:
                info_notes.append(f"最新余额未刷新: {balance_detail}")

            log_award = None
            if VERIFY_LOGS:
                level, detail, _, _, log_award = verify_checkin(session, uid)
            else:
                level, detail = "disabled", "日志核验已关闭"

            if checkin_award is None:
                checkin_award = log_award
            if checkin_award is None:
                checkin_award = fetch_checkin_award(session, uid)

            if level in ("new", "today"):
                log_note = f"日志已确认: {detail}"
            elif level == "disabled":
                log_note = detail
            else:
                log_note = f"日志未确认: {detail}"

            extra_note = f"；{'；'.join(info_notes)}" if info_notes else ""
            if checked_in:
                message = f"登录成功，本次已触发签到；{log_note}{extra_note}"
                status = "success"
            else:
                message = f"登录成功，签到流程已完成（可能此前已签到）；{log_note}{extra_note}"
                status = "already" if level in ("new", "today") else "success"

            return _result(name, status, message, str(username), quota, used_quota, checkin_award)
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


def _result(name, status, message, username, quota, used_quota=None, checkin_award=None):
    result = {
        "name": name,
        "status": status,
        "message": message,
        "username": username or "",
        "quota": quota,
        "used_quota": used_quota,
        "checkin_award": checkin_award,
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }
    tag = {
        "success": "✅ 成功",
        "already": "🟡 已签到",
        "fail": "❌ 失败",
    }.get(status, status)
    if status == "fail":
        log(f"[{name}] {tag} | {message}")
    else:
        reward_text = ""
        if checkin_award is not None:
            reward_text = f" | 本次奖励: {format_quota(checkin_award, plus=True, trim_cents=True)}"
        log(
            f"[{name}] {tag} | {message} | 余额: {format_quota(quota)}"
            f" | 历史消耗: {format_quota(used_quota)}{reward_text}"
        )
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
        status = item.get("status")
        if status == "success":
            status_text = "签到成功"
        elif status == "already":
            status_text = "今日已签到"
        else:
            status_text = f"签到失败：{item.get('message')}"
        if status == "fail":
            lines.append(f"{tag} {item.get('name')}：{status_text}")
        else:
            if item.get("checkin_award") is not None:
                status_text += f" {format_quota(item.get('checkin_award'), plus=True, trim_cents=True)}"
            lines.append(
                f"{tag} {item.get('name')}：{status_text} | "
                f"余额 {format_quota(item.get('quota'))} | "
                f"历史消耗 {format_quota(item.get('used_quota'))}"
            )

    if failures:
        title = "[AgentRouter] 签到部分失败" if len(failures) < len(results) else "[AgentRouter] 签到失败"
    else:
        title = "📅 AgentRouter 签到"
    safe_notify(title, "\n".join(lines))
    log(f"处理完毕: 成功/已签到 {len(results) - len(failures)} 个, 失败 {len(failures)} 个")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())