"""Small HTTPS client and safe errors; never log upstream response bodies.

SPDX-License-Identifier: AGPL-3.0-only
"""
import gzip
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request


MESSAGES = {
    "config": "缺少或无效的 VALORANT_AUTH_JSON / PUSH_PLUS_TOKEN，请检查 Repository Secrets。",
    "network": "接口网络连接失败或限流；已完成有限重试，请稍后重试。",
    "protocol": "接口响应格式改变或返回异常；请检查程序更新。",
    "auth": "掌瓦登录凭证已失效，请在本地重新 QQ 扫码并更新 VALORANT_AUTH_JSON。",
    "login": "QQ 登录未完成或授权接口已改变，请重新扫码。",
    "expired": "二维码已过期或登录已取消，请重新运行扫码程序。",
    "redirect": "接口返回了不受信任的跳转，已停止以保护凭证。",
    "store": "每日商店数据缺失或不是四款皮肤，请稍后重试或检查接口。",
    "push": "PushPlus 未接受消息，请检查令牌、额度或服务状态。",
    "delivery": "PushPlus 已接受消息，但未能确认投递完成，请检查微信和 PushPlus 后台。",
    "github": "无法写入 GitHub Secret，请先完成 gh 登录并确认仓库 Secrets 写权限。",
    "internal": "程序遇到未预期错误，已隐藏诊断内容以保护凭证。",
}


class SafeError(Exception):
    def __init__(self, code, *, http_status=None, service_code=None):
        self.code = code if code in MESSAGES else "internal"
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.service_code = None
        if type(service_code) in (str, int) and re.fullmatch(r"[0-9]{1,6}", str(service_code)):
            self.service_code = int(service_code)
        detail = ""
        if self.http_status is not None:
            detail += f" HTTP={self.http_status}"
        if self.service_code is not None:
            detail += f" PushPlus={self.service_code}"
        super().__init__(MESSAGES[self.code] + detail)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def trusted_url(url, hosts):
    try:
        p = urllib.parse.urlsplit(url)
        valid = (p.scheme == "https" and p.hostname in hosts and
                 p.port in (None, 443) and not p.username and not p.password)
    except ValueError:
        valid = False
    if not valid:
        raise SafeError("redirect")
    return url


class Http:
    def __init__(self, hosts):
        self.hosts = frozenset(hosts)
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPCookieProcessor(self.cookies))

    def cookie(self, name):
        return next((c.value for c in self.cookies if c.name == name), "")

    def request(self, url, *, params=None, body=None, headers=None, retries=1):
        trusted_url(url, self.hosts)
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        raw = None if body is None else json.dumps(body).encode("utf-8")
        request_headers = {"Accept-Encoding": "gzip", **(headers or {})}
        if raw is not None:
            request_headers["Content-Type"] = "application/json"
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, data=raw, headers=request_headers)
                try:
                    response = self.opener.open(req, timeout=20)
                except urllib.error.HTTPError as e:
                    response = e
                with response:
                    status, response_headers = response.code, response.headers
                    payload = response.read(2_000_001)
                if len(payload) > 2_000_000:
                    raise SafeError("protocol")
                if response_headers.get("Content-Encoding", "").lower() == "gzip":
                    # Bound decompressed size too (avoid decompression bombs).
                    import io
                    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
                        payload = stream.read(2_000_001)
                    if len(payload) > 2_000_000:
                        raise SafeError("protocol")
                if status == 429 or status >= 500:
                    raise SafeError("network", http_status=status)
                return status, response_headers, payload
            except (urllib.error.URLError, TimeoutError, OSError):
                error = SafeError("network")
            except SafeError as e:
                if e.code != "network":
                    raise
                error = e
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
        raise error from None

    def json(self, url, **kwargs):
        status, _, raw = self.request(url, **kwargs)
        if 300 <= status < 400:
            raise SafeError("redirect", http_status=status)
        if status in (401, 403):
            raise SafeError("auth", http_status=status)
        if status != 200:
            raise SafeError("protocol", http_status=status)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            raise SafeError("protocol") from None
        if not isinstance(data, dict):
            raise SafeError("protocol")
        return data


def check_result(data):
    code = str(data.get("result", "missing"))
    message = str(data.get("errMsg") or data.get("msg") or "").lower()
    if code in {"1001", "1003", "999999"} or any(
        text in message for text in ("ticket expire", "auth web ticket fail", "登录失效")
    ):
        raise SafeError("auth")
    if code != "0":
        raise SafeError("protocol")


def safe_value(value):
    """Cookie components must not permit header or cookie injection."""
    value = str(value)
    if not value or len(value) > 8192 or any(ord(c) < 33 or ord(c) > 126 or c in ';,' for c in value):
        raise SafeError("config")
    return value
