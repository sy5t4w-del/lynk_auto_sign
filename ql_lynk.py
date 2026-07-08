#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
领克 APP 自动签到 - 青龙面板专用版 v2
====================================================
新增:
  - accessToken 本地缓存 (避免每次强制 refresh, 与 app 行为一致)
  - Markdown 格式推送输出 (企业微信/钉钉/飞书/Telegram/PushPlus/Bark/Server酱)
  - 自动分享任务 (配 LYNK_TOKEN_B, 每天自动刷分享积分)

★★★ 最简单用法 ★★★
  1. 编辑下面 USER_CONFIG 块, 把 USER_REFRESH_TOKEN 改成你自己的 (28 天有效的那种)
  2. python3 ql_lynk.py      直接跑就行

可选环境变量 (会覆盖脚本顶部默认值):
  LYNK_REFRESH_TOKEN     主账号 refreshToken (bearer<uuid>)
  LYNK_DEVICE_ID         设备 ID (默认内置)
  LYNK_TOKEN_B           B 账号 refreshToken (逗号分隔, 启用 auto-share 时用)
  LYNK_SHARE_CONTENT_ID  分享文章 ID (默认 2072260486405246976)
  LYNK_AUTO_SHARE        1/true 启用自动分享 (默认 False, 仅生成 URL)
  PUSH_WECOM_WEBHOOK     企业微信机器人 webhook
  PUSH_DINGTALK_WEBHOOK  钉钉机器人 webhook
  PUSH_FEISHU_WEBHOOK    飞书机器人 webhook
  PUSH_TG_BOT_TOKEN      Telegram Bot Token
  PUSH_TG_CHAT_ID        Telegram Chat ID
  PUSH_SERVERCHAN_KEY    Server酱 SendKey
  PUSH_PUSHPLUS_TOKEN    PushPlus Token
  PUSH_BARK_URL          Bark 推送 URL

青龙定时: 0 9 * * *  (每天 9 点)
"""

import os
import sys
import json
import time
import uuid
import hmac
import base64
import hashlib
import argparse
import traceback
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, quote

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests


# ==================== 配置常量 ====================
API_BASE = "https://app-api-gw-toc.lynkco.com"
OAUTH_BASE = "https://app-services.lynkco.com.cn"
REFRESH_URL = OAUTH_BASE + "/auth/login/refresh"

CA_KEY = "204644386"
CA_SECRET = "QCl7udM3PB9cOIOwquwPglikFQnzJRsX"
SIG_HDRS = "X-Ca-Key,X-Ca-Timestamp,X-Ca-Nonce,X-Ca-Signature-Method"

EP_SIGN = "/up/api/v1/user/sign"
EP_SIGN_INFO = "/up/api/v1/userReward/getContinueDaysAndSignCard"
EP_ENERGY = "/app/energy/myEnergy"
EP_TASKS = "/up/api/v1/userReward/getTaskList"           # 签到任务进度 (连续7天/月度/季度/年度)
EP_GROWTH = "/app/energy/my/growth"                       # 成长等级 + 成长值

# 分享相关端点 (从 lynk_sign.py 迁移)
EP_SHARE_CHECK = "/app/v1/task/shareContentContectCheck"
EP_SHARE_REPORT = "/app/v1/task/shareContentContectReporting"
EP_SHARE_LOOKUP = "/app/v1/task/shareCodeToUserId"
EP_GET_SHARE_CODE = "/app/v1/task/getShareCode"

# H5 分享链接模板 (手工复制到微信发, 别人点击后给主账号加 5 能量体)
SHARE_URL_TEMPLATE = "https://h5.lynkco.com/app-h5/dist/web/pages/exploration/article/index.html?id={cid}&isShare={is_share}&shareCode={code}"

APP_CODE = "3fa3314998bd4195a9fe2df3e85e6a12"
DEFAULT_SHARE_CONTENT_ID = "2072260486405246976"

# ==================== License 验证 (RSA 离线签名) ====================
# 公钥 (公开无风险). 私钥 lynk_private.pem 仅作者持有, 严禁上传 GitHub.
# 用户首次运行会提示要 license, 跑 --request-license 生成 request_code 发作者换 license.txt.
# (删除: 之前是可开关的, 现在强制开启)
# 强制开启, 没 license 就别想跑 (开关已删除, 不可关闭).
LICENSE_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAvLIKMMIL7ZamhCRxldus
fgbDV1m7kj5LN+WfMhqu7QkgDQ6GW//Tv7zhNAg78AAkVG66sqkCLqLH8maLKAxx
ty6Hf/LNoAqpNu08YjQN13q8MzpWRvvSscT4h79gw38vS2D8zVSHgJfkhXSJob2v
Q9xrODbnshQotDtzHgEbr48jroqUvOE52iEW2jEV8XYqTIsqdtP740eiGn4+rtvG
ozkbYagGZIz+Bm/hWgGrqkO9MQhocJ1PmqtUuT8RP8wp7P47lGoMlygionmV3Z66
DKSID1N29HW7OrslLjR3idJaRUm9ZpWk11b79niSMmlVc9cvx/d5+efiTpAMgDuX
PQIDAQAB
-----END PUBLIC KEY-----"""


# ==================== ★★★ 用户配置 (只改这里就行) ★★★ ====================
# 主账号 refreshToken (28 天有效), 从领克 app 里抓包 app-services.lynkco.com.cn 域名的 mobileCodeLogin 响应里拿
# 必须自己填, 留空脚本会报错退出
USER_REFRESH_TOKEN = ""
# 设备 ID (从登录 URL 的 deviceId 参数里拿), 留空脚本会报错退出
USER_DEVICE_ID = ""
# 自动分享刷积分开关 (True / False). 留 False 时只生成 URL, 自己复制到微信发.
USER_AUTO_SHARE = False
# B 账号 refreshToken (留空 = 不启用自动分享; 多个用英文逗号分隔)
USER_TOKEN_B = ""
# 分享文章 ID (默认热门 ID)
USER_SHARE_CONTENT_ID = "2072260486405246976"
# 企业微信机器人 webhook (留空不推送; 想配多个推送渠道就在下面再加)
USER_PUSH_WECOM_WEBHOOK = ""
# ==================== 配置结束 ====================

# 优先级: 命令行参数 > 环境变量 (LYNK_*) > 脚本顶部 USER_CONFIG

# 分享每日上限 (后端限频, 超过会报"今日已分享")
SHARE_DAILY_LIMIT = 1

# ==================== License 验证 (RSA 离线签名) ====================
def get_machine_fingerprint():
    """机器指纹 (license 设备绑定用), 跨机器不同, 同机器稳定"""
    import platform
    sources = []
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(p) as f:
                sources.append(f.read().strip())
                break
        except Exception:
            pass
    mac = uuid.getnode()
    if (mac >> 40) % 2 == 0:
        sources.append(str(mac))
    sources.append(platform.node())
    raw = "|".join(s for s in sources if s)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def find_license_file():
    """按优先级找 license.txt"""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "license.txt"),
        "/ql/data/lynk_license.txt",
        "/ql/.lynk_license.txt",
        os.path.expanduser("~/.lynk_license.txt"),
        "/tmp/lynk_license.txt",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def verify_license(verbose=True):
    """校验 license, 不可绕过, 失败 sys.exit(1).

    验证项: 文件存在 -> 解析 -> 过期 -> 设备绑定 -> RSA-SHA256 签名
    """
    lp = find_license_file()
    if not lp:
        print("=" * 60)
        print("⚠ 未找到 license.txt, 本脚本需要授权后才能运行")
        print("=" * 60)
        print("")
        print("获取 license 步骤:")
        print("  1. 跑: python3 ql_lynk.py --request-license")
        print("  2. 把输出的 request_code 发给作者 (附捐赠截图)")
        print("  3. 拿到 license.txt 后, 放到脚本同目录或 /ql/data/")
        print("")
        sys.exit(1)

    try:
        with open(lp, "r", encoding="utf-8") as f:
            raw_text = f.read().strip()
        try:
            decoded = base64.b64decode(raw_text).decode("utf-8")
            data = json.loads(decoded)
        except Exception:
            data = json.loads(raw_text)
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"X license 解析失败 ({lp}): {type(e).__name__}: {e}")

    exp = int(data.get("expires_at") or 0)
    now_ts = int(time.time())
    if exp and exp < now_ts:
        sys.exit(f"X license 已过期 (到期 {datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M')})")

    current_fp = get_machine_fingerprint()
    license_fp = data.get("device_hash", "")
    if not license_fp:
        sys.exit("X license 缺少 device_hash, 拒绝")
    if license_fp != current_fp:
        sys.exit(
            "X license 与当前设备不匹配 (此 license 绑定在另一台机器)"
            f"  本机: {current_fp[:24]}..."
            f"  license: {license_fp[:24]}..."
        )

    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        pub = serialization.load_pem_public_key(LICENSE_PUBLIC_KEY.encode())
        sig = base64.b64decode(data.get("sig") or "")
        sig_payload = {k: v for k, v in data.items() if k != "sig"}
        sig_str = json.dumps(sig_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        pub.verify(sig, sig_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    except ImportError:
        sys.exit("X 缺少 cryptography 库, 请先装: pip install cryptography")
    except Exception as e:
        sys.exit(f"X license 签名验证失败: {type(e).__name__}: {e}")

    user = data.get("user", "?")
    note = data.get("note", "")
    if exp:
        days_left = max(0, (exp - now_ts) // 86400)
        valid_str = f"剩 {days_left} 天 (到期 {datetime.fromtimestamp(exp).strftime('%Y-%m-%d')})"
    else:
        valid_str = "永久"
    if verbose:
        log("OK", f"license 有效: user={user}  {valid_str}  note={note}")
    return True, user


def anti_tamper_check():
    """防破解 / 反调试检测. 任何一项检测到直接 sys.exit(1).

    检测项:
      - 调试器 (sys.gettrace / pydevd / pdb)
      - sys.exit / os._exit 被 hook
      - cryptography 库是否被替换
      - requests 库是否被替换
    """
    # 1. 调试器
    try:
        if hasattr(sys, "gettrace") and sys.gettrace() is not None:
            sys.exit("X 检测到调试器 (sys.gettrace), 拒绝运行")
        if hasattr(sys, "settrace") and sys.settrace is not sys.__dict__.get("settrace"):
            sys.exit("X 检测到 sys.settrace 被 hook")
    except Exception:
        pass

    # 2. sys.exit / os._exit 完整性
    for name in ("exit", "_exit"):
        for owner in (sys, os):
            fn = getattr(owner, name, None)
            if fn is None:
                continue
            if hasattr(fn, "__wrapped__") or (getattr(fn, "__code__", None) and fn.__code__.co_filename not in ("<frozen>",)):
                # sys.exit 通常 code 来自 <frozen>, os._exit 也类似
                pass
    # 3. cryptography 库路径校验
    try:
        from cryptography.hazmat.primitives.asymmetric import padding as _padding
        # 真实 padding.PKCS1v15 来自 cryptography 库, 不允许被 monkey patch
        if _padding.PKCS1v15.__module__ != "cryptography.hazmat.primitives.asymmetric.padding":
            sys.exit("X 检测到 cryptography.padding.PKCS1v15 被替换, 拒绝运行")
    except Exception as e:
        sys.exit(f"X cryptography 校验失败: {e}")

    # 4. requests.Session.send 完整性
    try:
        if hasattr(requests.Session().send, "__wrapped__"):
            sys.exit("X 检测到 requests.Session.send 被 hook")
    except Exception:
        pass


def make_request_code(note=""):
    """生成 request_code (用户发给作者换 license)"""
    import platform
    fp = get_machine_fingerprint()
    payload = {
        "device_hash": fp,
        "device_raw": "|".join(platform.uname()),
        "hostname": platform.node(),
        "platform": platform.platform(),
        "requested_at": int(time.time()),
        "nonce": str(uuid.uuid4()),
        "note": note,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii"), payload


# ==================== accessToken 缓存 ====================
def get_cache_file():
    """青龙数据目录 /ql/data 优先, fallback 到 /tmp"""
    candidates = [
        "/ql/data/lynk_access_token.json",
        "/ql/.lynk_access_token.json",
        "/tmp/lynk_access_token.json",
        os.path.expanduser("~/.lynk_access_token.json"),
    ]
    for p in candidates:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            return p
        except Exception:
            continue
    return "/tmp/lynk_access_token.json"


def load_cached_at(rt, device_id):
    """读取缓存的 accessToken. 返回 dict 或 None.

    缓存有效条件:
      1. 文件存在
      2. refreshToken 仍匹配
      3. expireAt 离现在 > 60 秒
    """
    p = get_cache_file()
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("refresh_token") != rt:
            return None  # refresh token 已轮换, 缓存作废
        if data.get("device_id") != device_id:
            return None  # 设备变了, 不能复用
        exp_at = parse_expiry(data.get("expire_at"))
        if not exp_at:
            return None
        if datetime.now() >= exp_at - timedelta(seconds=60):
            return None  # 已过期或即将过期
        return data
    except Exception:
        return None


def save_cached_at(rt, device_id, access_token, expire_at, refresh_token=None, refresh_expire_at=None):
    """写入 accessToken 缓存"""
    p = get_cache_file()
    try:
        data = {
            "refresh_token": rt,
            "device_id": device_id,
            "access_token": access_token,
            "expire_at": expire_at.isoformat() if isinstance(expire_at, datetime) else expire_at,
            "saved_at": datetime.now().isoformat(),
        }
        if refresh_token:
            data["refresh_token_new"] = refresh_token
        if refresh_expire_at:
            data["refresh_expire_at"] = (
                refresh_expire_at.isoformat() if isinstance(refresh_expire_at, datetime) else refresh_expire_at
            )
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass
        return p
    except Exception as e:
        return None


def clear_cached_at():
    """清缓存 (refresh 失败或显式重置)"""
    p = get_cache_file()
    try:
        if os.path.isfile(p):
            os.remove(p)
    except Exception:
        pass


# ==================== HMAC 签名 ====================
def gen_nonce():
    return str(uuid.uuid4()).upper()


def build_sig(method, path, params=None):
    ts = str(int(time.time() * 1000))
    nonce = gen_nonce()
    sh = {
        "X-Ca-Key": CA_KEY,
        "X-Ca-Nonce": nonce,
        "X-Ca-Signature-Method": "HmacSHA256",
        "X-Ca-Timestamp": ts,
    }
    url = path
    if params:
        q = urlencode(sorted(params.items()))
        url = f"{path}?{q}" if q else path
    parts = [method.upper(), "*/*", "", "application/json", ""]
    for k, v in sh.items():
        parts.append(f"{k}:{v}")
    parts.append(url)
    sig = base64.b64encode(
        hmac.new(CA_SECRET.encode(), "\n".join(parts).encode(), hashlib.sha256).digest()
    ).decode()
    return {
        **sh,
        "X-Ca-Signature-Headers": SIG_HDRS,
        "X-Ca-Signature": sig,
        "Accept": "*/*",
    }


# ==================== 工具函数 ====================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(level, msg):
    icons = {"INFO": "[INFO]", "OK": "[OK]", "WARN": "[!]", "ERR": "[X]"}
    icon = icons.get(level, "[INFO]")
    print(f"[{now()}] {icon} {msg}", flush=True)


def parse_expiry(ts_val):
    if ts_val is None or ts_val == "":
        return None
    try:
        if isinstance(ts_val, (int, float)):
            ts = ts_val / 1000 if ts_val > 1e12 else ts_val
            return datetime.fromtimestamp(ts)
        if isinstance(ts_val, str):
            s = ts_val.strip().replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(s)
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
            return dt.astimezone().replace(tzinfo=None)
    except Exception:
        return None
    return None


# ==================== Token 管理 ====================
def refresh_lynk_token(rt, device_id):
    """refresh 接口换 accessToken + refreshToken"""
    headers = {
        "Authorization": f"APPCODE {APP_CODE}",
        "accept": "application/json",
        "content-type": "application/json; charset=UTF-8",
        "publicplatform": "iOS",
        "user-agent": "CA_iOS_SDK_2.0",
        "token": "",
        "gl_dev_id": device_id,
        "appversioncode": "4.2.0",
        "appversionname": "40200106",
        "gl_app_version": "4.2.0",
        "gl_app_build": "40200106",
        "x-ca-version": "1",
    }
    params = {
        "refreshToken": rt,
        "deviceId": device_id,
        "deviceType": "IOS",
        "appVersion": "4.2.0",
    }
    try:
        r = requests.get(REFRESH_URL, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "success":
            return None, f"refresh 失败: {data.get('message', data)}"
        dto = (data.get("data") or {}).get("centerTokenDto") or {}
        return {
            "accessToken": dto.get("token"),
            "refreshToken": dto.get("refreshToken"),
            "expireAt": dto.get("expireAt"),
            "refreshExpireAt": dto.get("refreshExpireAt"),
        }, None
    except Exception as e:
        return None, f"refresh 异常: {type(e).__name__}: {e}"


def get_access_token(rt_or_at, device_id, force=False):
    """智能获取 accessToken, 支持三种来源 (按优先级):

      1. 缓存命中 → 直接返回
      2. 当作 refreshToken 调 refresh 接口
      3. 降级: refresh 失败时, 试探它本身是否是有效 accessToken

    返回 (access_token, refresh_token_new_or_none, refresh_expire_at, source)
    source 取值:
      - "cache"        : 命中本地缓存
      - "refresh"      : 通过 refreshToken 拿到新的
      - "bare_access"  : refreshToken 接口拒绝, 把它当 accessToken 直接用 (短时可用)
      - "failed: <原因>" : 完全失败
    """
    if not force:
        cached = load_cached_at(rt_or_at, device_id)
        if cached:
            return cached["access_token"], cached.get("refresh_token_new"), None, "cache"

    # 第二步: 试 refresh 接口
    result, err = refresh_lynk_token(rt_or_at, device_id)
    if result and result.get("accessToken"):
        access_token = result["accessToken"]
        new_rt = result.get("refreshToken") or rt_or_at
        expire_at = parse_expiry(result.get("expireAt"))
        refresh_expire_at = parse_expiry(result.get("refreshExpireAt"))
        save_cached_at(rt_or_at, device_id, access_token, expire_at, new_rt, refresh_expire_at)
        return access_token, new_rt, refresh_expire_at, "refresh"

    # 第三步: refresh 拒绝 (例如 accessToken / 过期 token), 把它直接当 accessToken 试一次
    test = lynk_call("GET", EP_SIGN_INFO, rt_or_at)
    if str(test.get("code")) in ("200", "success"):
        # 是有效 accessToken, 直接缓存 8 分钟 (访问一次 QPS 检测)
        expire_short = datetime.now() + timedelta(minutes=8)
        save_cached_at(rt_or_at, device_id, rt_or_at, expire_short, refresh_expire_at=None)
        log("OK", f"  ⚠ 降级: token 不是 refreshToken, 当作 accessToken 直接用 (短时有效)")
        return rt_or_at, None, None, "bare_access"

    clear_cached_at()
    return None, None, None, f"refresh_err: {err}; bare_access 也无效: {test.get('code')} {test.get('message', '')}"


# ==================== 业务 API ====================
def lynk_call(method, path, token, body=None, params=None):
    # 每次业务请求前隐式再验 license (防 patch 绕过)
    if not LICENSE_USER or LICENSE_USER == "anonymous":
        sys.exit("X LICENSE_USER 未初始化, 拒绝业务请求")
    sig = build_sig(method, path, params)
    headers = {
        "token": token,
        "content-type": "application/json",
        **sig,
    }
    try:
        url = f"{API_BASE}{path}"
        if method == "GET":
            r = requests.get(url, headers=headers, params=params, timeout=20)
        else:
            r = requests.post(url, headers=headers, json=body or {}, timeout=20)
        try:
            return r.json()
        except Exception:
            return {"code": r.status_code, "raw": r.text[:500]}
    except Exception as e:
        return {"code": "EXCEPTION", "message": str(e)}


def lynk_sign_info(token):
    return lynk_call("GET", EP_SIGN_INFO, token)


def lynk_do_sign(token):
    return lynk_call("POST", EP_SIGN, token, body={})


def lynk_energy(token):
    return lynk_call("GET", EP_ENERGY, token)


def lynk_growth(token):
    return lynk_call("GET", EP_GROWTH, token)


def lynk_tasks(token):
    return lynk_call("GET", EP_TASKS, token)


def get_share_code(token):
    """主账号: 拿自己的 shareCode (H5 share-dialog 调法)"""
    return lynk_call("GET", EP_GET_SHARE_CODE, token)


def share_lookup(token, share_code):
    """点击账号: 反查分享人 userId"""
    return lynk_call("POST", EP_SHARE_LOOKUP, token, body={"shareCode": share_code})


def share_check(token, content_id, share_code):
    return lynk_call("POST", EP_SHARE_CHECK, token, body={"contentId": content_id, "shareCode": share_code})


def share_report(token, content_id, share_code):
    return lynk_call("POST", EP_SHARE_REPORT, token, body={"contentId": content_id, "shareCode": share_code})


def build_share_url(content_id, share_code):
    """根据 contentId + shareCode 构造 H5 分享链接 (微信 / QQ 等可直接点开)

    isShare 参数是 URL-encoded 的 lynkco:// 协议, 用于 app 唤起
    """
    is_share_raw = f"lynkco://wx/?routeUrl=/pages/exploration/article/index.js?id={content_id}"
    is_share_enc = quote(is_share_raw, safe="")
    return SHARE_URL_TEMPLATE.format(cid=content_id, is_share=is_share_enc, code=share_code)


# ==================== 青龙环境变量持久化 ====================
def update_ql_env(var_name, var_value):
    candidates = [
        os.environ.get("QL_ENV_FILE", "").strip(),
        "/ql/config/env.sh",
        "/ql/.env",
        "/ql/.env.local",
        "/data/config/env.sh",
    ]
    env_file = next((p for p in candidates if p and os.path.isfile(p)), None)
    if not env_file:
        return False, "未找到 env 文件 (设置 QL_ENV_FILE 或放 /ql/config/env.sh)"
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            content = f.read()
        pattern = re.compile(rf"^export\s+{re.escape(var_name)}=.*$", re.MULTILINE)
        new_line = f'export {var_name}="{var_value}"'
        if pattern.search(content):
            content = pattern.sub(new_line, content)
        else:
            if not content.endswith("\n"):
                content += "\n"
            content += new_line + "\n"
        with open(env_file, "w", encoding="utf-8") as f:
            f.write(content)
        return True, env_file
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ==================== 推送 (markdown 格式) ====================
def md_escape_tg(text):
    """Telegram MarkdownV2 需要转义的字符"""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", text)


def _md_to_html(text):
    """通用 markdown 转 HTML (兼容企业微信/钉钉/Server酱/飞书)

    企业微信 / 钉钉 / 飞书机器人 markdown 不支持 [text](url) 标准语法,
    要用 HTML <a href="URL">text</a>, 颜色也用 <font color="...">
    """
    # markdown [text](url) -> HTML <a href="url">text</a>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    return text


def _md_to_serverchan_html(text):
    """Server酱专用: 完整 markdown -> HTML (含粗体/标题/列表/链接)"""
    text = _md_to_html(text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<h3>\1</h3>", text, flags=re.MULTILINE)
    text = re.sub(r"^\*\s*(.+)$", r"• \1<br>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = text.replace("\n", "<br>")
    return text


def push_text(title, md_text):
    """多渠道推送, 返回结果汇总字符串.

    不同渠道语法差异:
      - 企业微信 / 钉钉 / Server酱 / 飞书: HTML <a href="URL">text</a>
      - PushPlus / Bark / Telegram: 标准 markdown [text](url)
    """
    results = []
    html_text = _md_to_html(md_text)
    sc_html = _md_to_serverchan_html(md_text)

    # 1. 企业微信 (msgtype=markdown, 不渲染 <a>, 用 [text](url) + 末尾附 raw URL 兜底)
    url = os.environ.get("PUSH_WECOM_WEBHOOK", "").strip()
    if url:
        try:
            # 末尾追加一行 raw URL (手机端部分版本不渲染 markdown 链接, 给个纯文本方便复制)
            raw_url = ""
            m = re.search(r'\[([^\]]+)\]\((https?://[^)]+)\)', md_text)
            if m:
                raw_url = f"\n\n📋 原始链接 (复制用): {m.group(2)}"
            payload = {"msgtype": "markdown", "markdown": {"content": f"# {title}\n\n{md_text}{raw_url}"}}
            r = requests.post(url, json=payload, timeout=10)
            results.append(f"企业微信: {'OK' if r.ok else 'X ' + r.text[:50]}")
        except Exception as e:
            results.append(f"企业微信: X {e}")

    # 2. 钉钉 (msgtype=markdown, 支持 HTML 链接)
    url = os.environ.get("PUSH_DINGTALK_WEBHOOK", "").strip()
    if url:
        try:
            payload = {"msgtype": "markdown", "markdown": {"title": title, "text": f"# {title}\n\n{html_text}"}}
            r = requests.post(url, json=payload, timeout=10)
            results.append(f"钉钉: {'OK' if r.ok else 'X'}")
        except Exception as e:
            results.append(f"钉钉: X {e}")

    # 3. 飞书 (post 富文本 + a 标签)
    url = os.environ.get("PUSH_FEISHU_WEBHOOK", "").strip()
    if url:
        try:
            # 飞书 post 消息支持 a 标签. 把 markdown 转成 [text](line) 形式分段渲染
            # 这里用 plain + a 标签组合
            plain_html = re.sub(r"\*\*(.+?)\*\*", r"\1", html_text)
            plain_html = re.sub(r"`(.+?)`", r"\1", plain_html)
            payload = {
                "msg_type": "post",
                "content": {
                    "post": {
                        "zh_cn": {
                            "title": title,
                            "content": [[{"tag": "text", "text": plain_html}]],
                        }
                    }
                },
            }
            r = requests.post(url, json=payload, timeout=10)
            results.append(f"飞书: {'OK' if r.ok else 'X'}")
        except Exception as e:
            results.append(f"飞书: X {e}")

    # 4. Telegram (MarkdownV2, 标准 markdown 语法)
    tg_token = os.environ.get("PUSH_TG_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("PUSH_TG_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        try:
            full = f"*{md_escape_tg(title)}*\n\n{md_escape_tg(md_text)}"
            r = requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={"chat_id": tg_chat, "text": full, "parse_mode": "MarkdownV2", "disable_web_page_preview": True},
                timeout=10,
            )
            results.append(f"Telegram: {'OK' if r.ok else 'X'}")
        except Exception as e:
            results.append(f"Telegram: X {e}")

    # 5. Server酱 (HTML 渲染)
    sc_key = os.environ.get("PUSH_SERVERCHAN_KEY", "").strip()
    if sc_key:
        try:
            r = requests.post(
                f"https://sctapi.ftqq.com/{sc_key}.send",
                data={"title": title, "desp": sc_html},
                timeout=10,
            )
            results.append(f"Server酱: {'OK' if r.ok else 'X'}")
        except Exception as e:
            results.append(f"Server酱: X {e}")

    # 6. PushPlus (markdown 模板, 用标准 [text](url))
    pp_token = os.environ.get("PUSH_PUSHPLUS_TOKEN", "").strip()
    if pp_token:
        try:
            r = requests.post(
                "https://www.pushplus.plus/send",
                json={"token": pp_token, "title": title, "content": md_text, "template": "markdown"},
                timeout=10,
            )
            results.append(f"PushPlus: {'OK' if r.ok else 'X'}")
        except Exception as e:
            results.append(f"PushPlus: X {e}")

    # 7. Bark (URL 拼接, body 支持 markdown)
    bark_url = os.environ.get("PUSH_BARK_URL", "").strip()
    if bark_url:
        try:
            sep = "&" if "?" in bark_url else "?"
            url = f"{bark_url}{sep}title={quote(title)}&body={quote(md_text)}&markdown=1"
            r = requests.get(url, timeout=10)
            results.append(f"Bark: {'OK' if r.ok else 'X'}")
        except Exception as e:
            results.append(f"Bark: X {e}")

    return " | ".join(results) if results else "(未配置推送渠道)"


# ==================== 分享任务 (合并到主流程) ====================
def do_share_task(share_client_token, content_id, share_code):
    """用 share_client_token (B 账号) 给 A 的 shareCode 点分享加分.

    返回 dict: { ok, lookup, check, report, energy_delta, msg }
    """
    result = {"ok": False, "lookup": "-", "check": "-", "report": "-", "energy_delta": 0, "msg": ""}

    # 拿点击前的能量体
    e0 = lynk_energy(share_client_token)
    energy_before = 0
    if isinstance(e0, dict) and str(e0.get("code")) == "success":
        energy_before = int((e0.get("data") or {}).get("point", 0) or 0)

    # 1. lookup 分享人 userId
    r1 = share_lookup(share_client_token, share_code)
    result["lookup"] = r1.get("code", "?")

    # 2. check 接口 (后端前置校验)
    r2 = share_check(share_client_token, content_id, share_code)
    result["check"] = r2.get("code", "?")
    check_msg = r2.get("message", "")

    # 3. report 接口 (真正加分)
    r3 = share_report(share_client_token, content_id, share_code)
    result["report"] = r3.get("code", "?")
    report_msg = r3.get("message", "")

    # 4. 看能量体变化
    time.sleep(2)
    e1 = lynk_energy(share_client_token)
    energy_after = 0
    if isinstance(e1, dict) and str(e1.get("code")) == "success":
        energy_after = int((e1.get("data") or {}).get("point", 0) or 0)
    result["energy_delta"] = energy_after - energy_before

    # 判断成功: report 200/success 且能量体增加 (后端规则: +5 能量体 / 次)
    if str(r3.get("code")) in ("200", "success") and result["energy_delta"] > 0:
        result["ok"] = True
        result["msg"] = f"+{result['energy_delta']} 能量体"
    elif "已分享" in report_msg or "已领取" in report_msg or "今日已" in report_msg or "已结束" in report_msg:
        result["ok"] = True  # 算成功 (后端拒收是预期行为)
        result["msg"] = f"今日已分享 ({report_msg})"
    elif str(r3.get("code")) in ("200", "success"):
        result["ok"] = True
        result["msg"] = f"report OK 但 Δ={result['energy_delta']} ({report_msg or check_msg})"
    else:
        result["msg"] = f"report failed: code={r3.get('code')} {report_msg}"

    return result


# ==================== 主流程 ====================
def run(rt, device_id, token_b_list=None, share_content_id=None, auto_share=False, quiet=False):
    """续 token -> 签到 -> 分享 -> 推送"""
    log("INFO", "=" * 60)
    log("INFO", "领克 APP 自动签到 - 青龙面板版 v2")
    log("INFO", "=" * 60)
    log("INFO", f"refreshToken: ...{rt[-12:]}")
    log("INFO", f"deviceId:     {device_id[:20]}...")

    md_lines = []  # 累积 markdown 输出, 最后统一推送

    # 1. 拿 accessToken (优先用缓存)
    log("INFO", "[1/5] 获取 accessToken (优先缓存)...")
    access_token, new_rt, refresh_expire_at, source = get_access_token(rt, device_id)

    if not access_token:
        msg = source if source.startswith("refresh_err") else "未知错误"
        log("ERR", f"获取 accessToken 失败: {msg}")
        push_text("领克签到失败", f"**❌ 续 token 失败**\n\n```\n{msg}\n```")
        return 2

    if source == "cache":
        log("OK", "accessToken: 缓存命中, 跳过 refresh")
    else:
        log("OK", f"accessToken:  refresh 调用成功")
    log("OK", f"accessToken:  ...{access_token[-12:]}")

    # refreshToken 轮换处理
    rt_left = None
    rt_expire_str = ""
    if new_rt and new_rt != rt:
        ok, info = update_ql_env("LYNK_REFRESH_TOKEN", new_rt)
        if ok:
            log("OK", f"refreshToken 已自动写回 env: {info}")
        else:
            log("WARN", "=" * 60)
            log("WARN", f"refreshToken 已轮换, 写回 env 失败: {info}")
            log("WARN", f"请手动更新 LYNK_REFRESH_TOKEN={new_rt}")
            log("WARN", "=" * 60)

    if refresh_expire_at:
        rt_left = (refresh_expire_at - datetime.now()).days
        rt_expire_str = refresh_expire_at.strftime("%Y-%m-%d %H:%M")
        log("OK", f"refreshToken: 剩 {rt_left} 天, 到期 {rt_expire_str}")

    # 2. 账户信息 + 签到状态
    log("INFO", "[2/5] 查询账户信息 + 签到状态...")

    # 2a. 签到状态 (必查, 后面要用)
    info = lynk_sign_info(access_token)
    if info.get("code") not in ("200", "success"):
        log("ERR", f"查询签到状态失败: code={info.get('code')}  message={info.get('message', '')}")
        if not quiet:
            log("INFO", f"  raw: {json.dumps(info, ensure_ascii=False)[:300]}")
        push_text("领克签到失败", f"**❌ 查询签到状态失败**\n\n```\n{json.dumps(info, ensure_ascii=False)[:200]}\n```")
        return 3

    data = info.get("data") or {}
    sign_status = data.get("signStatus") or data.get("todaySigned") or data.get("status")
    streak = data.get("continuousSignDays") or data.get("serialDays") or data.get("continueDays") or 0
    sign_card = data.get("signCardNumber") or 0
    log("OK", f"签到状态: signStatus={sign_status}  连续签到={streak}天  补签卡={sign_card}张")

    # 2b. 账户信息 (能量体 + 成长值)
    energy_point = "-"
    energy_income = "-"
    growth_name = "-"
    growth_value = "-"
    energy_resp = lynk_energy(access_token)
    if isinstance(energy_resp, dict) and str(energy_resp.get("code")) in ("200", "success"):
        ed = energy_resp.get("data") or {}
        energy_point = ed.get("point", "-")
        energy_income = ed.get("incomePoint", "-")
    growth_resp = lynk_growth(access_token)
    if isinstance(growth_resp, dict) and str(growth_resp.get("code")) in ("200", "success"):
        lv = (growth_resp.get("data") or {}).get("accountLevelVo") or {}
        growth_name = lv.get("name", "-")
        growth_value = lv.get("growth", "-")
    log("OK", f"账户: 能量体={energy_point}  累计获得={energy_income}  等级={growth_name}  成长值={growth_value}")

    # 2c. 签到任务进度 (连续7天/月度/季度/年度)
    # 接口只返回 taskProcess (已签天数), 总天数从任务名 "X天" 里正则提取
    task_progress = {}  # name -> "已签 X / 总 Y (奖励)"
    tasks_resp = lynk_tasks(access_token)
    if isinstance(tasks_resp, dict) and str(tasks_resp.get("code")) in ("200", "success"):
        for t in (tasks_resp.get("data") or []):
            tname = t.get("taskName", "?")
            tproc = t.get("taskProcess", "?")
            treward = ", ".join(t.get("rewardContent") or []) or "无奖励字段"
            m = re.search(r"(\d+)天", tname)
            total = m.group(1) if m else None
            if total:
                display = f"{int(total) - int(tproc)} / {total}"  # 剩余/总, 避免歧义
            else:
                display = str(tproc)
            task_progress[tname] = (display, treward)
        if task_progress:
            for tname, (display, reward) in task_progress.items():
                log("OK", f"任务: {tname}: {display}  ({reward})")

    # 3. 执行签到
    log("INFO", "[3/5] 执行签到...")
    if sign_status in (1, True, "1", "signed", "already"):
        log("OK", "今日已签到, 无需重复")
        sign_status_str = "✅ 已签到"
        reward = "无新增"
    else:
        sign_resp = lynk_do_sign(access_token)
        if sign_resp.get("code") in ("200", "success"):
            log("OK", "签到成功!")
            sign_status_str = "✅ 签到成功"
            d = sign_resp.get("data") or {}
            parts = []
            if d.get("rewardEnergyNumber"):    parts.append(f"+{d['rewardEnergyNumber']} 能量体")
            if d.get("rewardPointNumber"):    parts.append(f"+{d['rewardPointNumber']} Co积分")
            if d.get("rewardSignCardNumber"): parts.append(f"+{d['rewardSignCardNumber']} 补签卡")
            reward = ", ".join(parts) if parts else "无奖励字段"
            log("OK", f"奖励: {reward}")
        else:
            log("ERR", f"签到失败: code={sign_resp.get('code')}  message={sign_resp.get('message', '')}")
            push_text("领克签到失败", f"**❌ 签到接口失败**\n\n```\n{json.dumps(sign_resp, ensure_ascii=False)[:200]}\n```")
            return 4

    md_lines.append(f"**签到**: {sign_status_str}")
    md_lines.append(f"**奖励**: {reward}")
    md_lines.append(f"**连续**: {streak} 天  /  补签卡: {sign_card} 张")
    md_lines.append("")
    md_lines.append("**账户信息**:")
    md_lines.append(f"- 积分余额: **{energy_point}**  /  累计获得: **{energy_income}**")
    md_lines.append(f"- 成长等级: **{growth_name}**  /  成长值: **{growth_value}**")
    if task_progress:
        md_lines.append("")
        md_lines.append("**签到任务进度**:")
        for tname, (proc, reward_t) in task_progress.items():
            md_lines.append(f"- {tname}: `{proc}`  ({reward_t})")

    # 4. 拿 shareCode (无论 auto-share 与否都执行, 用于下面构造 URL)
    log("INFO", "[4/5] 拿 shareCode...")
    share_code = None
    sc_resp = get_share_code(access_token)
    if isinstance(sc_resp, dict) and str(sc_resp.get("code")) == "success":
        sc_data = sc_resp.get("data")
        if isinstance(sc_data, str) and re.match(r"^[A-Fa-f0-9]{32,}$", sc_data):
            share_code = sc_data
    if share_code:
        log("OK", f"shareCode:  ...{share_code[-12:]}  (完整 {len(share_code)} hex)")
    else:
        log("ERR", f"拿不到 shareCode: {sc_resp.get('code') if isinstance(sc_resp, dict) else '?'}  {sc_resp.get('message', '') if isinstance(sc_resp, dict) else ''}")

    # 5. auto-share: B 账号点击刷积分 (可选, 默认不启用)
    share_results = []
    if auto_share and token_b_list and share_code:
        log("INFO", "[5/5] auto-share: B 账号刷分享积分...")
        log("INFO", f"  contentId: {share_content_id}  clicker 数: {len(token_b_list)}")
        for i, tb in enumerate(token_b_list, 1):
            log("INFO", f"  --- B{i}: refresh & click ---")
            tb_token, _, _, tb_src = get_access_token(tb, device_id)
            if not tb_token:
                log("ERR", f"  B{i} 拿不到 accessToken  (source={tb_src})")
                log("ERR", f"     → 这个 B token 已失效/无效, 请在领克 app 里重新抓一个")
                log("ERR", f"     → 抓包域名 app-api-gw-toc.lynkco.com 的请求头 token 字段")
                share_results.append({"idx": i, "ok": False, "msg": f"❌ token 无效 ({tb_src})", "energy_delta": 0})
                continue
            log("OK", f"  B{i} accessToken ({tb_src})")
            res = do_share_task(tb_token, share_content_id, share_code)
            res["idx"] = i
            share_results.append(res)
            mark = "✅" if res["ok"] else "❌"
            log("OK" if res["ok"] else "ERR",
                f"  B{i} 分享 {mark}  {res['msg']}  (Δ能量体 {res['energy_delta']:+d})")

        md_lines.append("")
        md_lines.append(f"**auto-share** (contentId `{share_content_id[-12:]}`, {len(share_results)} 个 B 账号):")
        for r in share_results:
            mark = "✅" if r["ok"] else "❌"
            md_lines.append(f"- {mark} B{r['idx']}: {r['msg']}  (Δ能量体 `{r['energy_delta']:+d}`)")
    else:
        if auto_share and not token_b_list:
            log("INFO", "[5/5] auto-share 未启用 (未配 LYNK_TOKEN_B)")

    # 6. 构造可手工复制到微信的分享 URL (即便 auto-share 不启用也输出)
    share_url = ""
    if share_code:
        share_url = build_share_url(share_content_id, share_code)
        log("OK", "")
        log("OK", "━" * 60)
        log("OK", "📤 分享链接 (复制到微信/朋友圈, 别人点击你得 5 能量体):")
        log("OK", share_url)
        log("OK", "━" * 60)

    if share_url:
        md_lines.append("")
        md_lines.append(f"**📤 分享链接** (复制到微信发, 别人点击你 +5 能量体):")
        md_lines.append(f"")
        # markdown 链接语法 [文字](url), 企业微信/钉钉/飞书/PushPlus/Bark 都能识别为可点击链接
        md_lines.append(f"[👉 点击领取 +5 能量体]({share_url})")

    # 5. 构造 markdown 推送
    md_lines.insert(0, f"**时间**: `{now()}`")
    md_lines.insert(1, f"**用户标识**: `{LICENSE_USER}`")
    md_lines.insert(2, f"**accessToken**: {'缓存命中' if source == 'cache' else '本次 refresh'}")
    if rt_left is not None:
        md_lines.append("")
        md_lines.append(f"**refreshToken**: 剩 **{rt_left}** 天 (到期 `{rt_expire_str}`)")

    title = "领克签到成功" if "成功" in sign_status_str else "领克签到 (已签)"
    md_text = "\n".join(md_lines)

    log("INFO", f"[推送] {push_text(title, md_text)}")
    log("OK", "全部完成")
    return 0


def main():
    parser = argparse.ArgumentParser(description="领克 APP 自动签到 - 青龙面板专用版 v2")
    parser.add_argument("--token", help="LYNK refreshToken (主账号, 覆盖环境变量)")
    parser.add_argument("--device-id", help="LYNK deviceId (覆盖环境变量)")
    parser.add_argument("--token-b", help="LYNK refreshToken 列表 (B 账号, 逗号分隔, 用于刷分享积分)")
    parser.add_argument("--content-id", help="分享文章 ID (覆盖环境变量)")
    parser.add_argument("--auto-share", action="store_true", help="启用自动分享任务")
    parser.add_argument("--no-cache", action="store_true", help="忽略 accessToken 缓存, 强制 refresh")
    parser.add_argument("--clear-cache", action="store_true", help="清空 accessToken 缓存后退出")
    parser.add_argument("--quiet", action="store_true", help="静默模式, 不打印原始响应")
    parser.add_argument("--request-license", action="store_true", help="生成 request_code (发给作者换 license)")
    parser.add_argument("--request-note", default="", help="request_code 备注 (如 微信号:xxx)")

    args = parser.parse_args()

    # ==================== License 检查 (强制, 不可跳过) ====================
    anti_tamper_check()  # 防破解检测
    if args.request_license:
        encoded, payload = make_request_code(args.request_note)
        print("=" * 60)
        print("📨 request_code (把下面这一整段发给作者)")
        print("=" * 60)
        print(encoded)
        print("=" * 60)
        print()
        print("附带信息 (仅参考):")
        print(f"  本机 fingerprint: {payload['device_hash'][:32]}...")
        print(f"  hostname:          {payload['hostname']}")
        print(f"  platform:          {payload['platform']}")
        print(f"  note:              {payload['note']}")
        sys.exit(0)
    verify_license(verbose=True)

    if args.clear_cache:
        clear_cached_at()
        log("OK", f"accessToken 缓存已清空: {get_cache_file()}")
        return 0

    # 配置优先级: 命令行参数 > 环境变量 (LYNK_*) > 脚本顶部 USER_CONFIG
    rt = (args.token or os.environ.get("LYNK_REFRESH_TOKEN") or USER_REFRESH_TOKEN).strip()
    device_id = (args.device_id or os.environ.get("LYNK_DEVICE_ID") or USER_DEVICE_ID).strip()

    if not rt or rt == "bearer<your-refresh-token>":
        log("ERR", "未配置 refreshToken")
        log("ERR", "1) 编辑脚本顶部的 USER_REFRESH_TOKEN, 或")
        log("ERR", "2) 设置环境变量 LYNK_REFRESH_TOKEN, 或")
        log("ERR", "3) 传 --token 参数")
        sys.exit(1)

    # B 账号列表
    token_b_raw = args.token_b or os.environ.get("LYNK_TOKEN_B") or USER_TOKEN_B
    token_b_list = [t.strip() for t in token_b_raw.split(",") if t.strip()] if token_b_raw else []

    # 分享内容
    share_content_id = args.content_id or os.environ.get("LYNK_SHARE_CONTENT_ID") or USER_SHARE_CONTENT_ID

    # 自动分享开关
    auto_share_env = os.environ.get("LYNK_AUTO_SHARE", "").strip().lower() in ("1", "true", "yes", "on")
    auto_share = args.auto_share or auto_share_env or USER_AUTO_SHARE

    # 推送渠道: 命令行没暴露, 直接读 USER_CONFIG + 环境变量
    if not os.environ.get("PUSH_WECOM_WEBHOOK") and USER_PUSH_WECOM_WEBHOOK:
        os.environ["PUSH_WECOM_WEBHOOK"] = USER_PUSH_WECOM_WEBHOOK

    # 缓存开关 (--no-cache 强制 refresh)
    if args.no_cache:
        clear_cached_at()

    try:
        rc = run(
            rt,
            device_id,
            token_b_list=token_b_list,
            share_content_id=share_content_id,
            auto_share=auto_share,
            quiet=args.quiet,
        )
        sys.exit(rc)
    except Exception as e:
        log("ERR", f"未捕获异常: {e}")
        log("ERR", traceback.format_exc())
        push_text("领克签到异常", f"**❌ 脚本崩溃**\n\n```\n{traceback.format_exc()[:600]}\n```")
        sys.exit(99)


if __name__ == "__main__":
    main()
