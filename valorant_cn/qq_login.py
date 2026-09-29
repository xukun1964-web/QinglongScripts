"""QQ HTTP QR protocol adapted from GuJi08233/astrbot_plugin_val_shop.

Upstream commit: 55bb649585bc39a3d775a95da01e1a099d7dd499 (v3.2.6).
Copyright GuJi08233 and contributors. See NOTICE.md and LICENSE.
Modifications: standalone stdlib implementation, bounded redirects, no credential logs,
in-memory QR display, direct GitHub Secret upload. SPDX-License-Identifier: AGPL-3.0-only
"""
import argparse
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import Http, SafeError, trusted_url
from .store import exchange_qq, query_store

XUI = "https://xui.ptlogin2.qq.com"
AID, THIRD_AID, DAID = "716027609", "102061775", "381"
CALLBACK = "http://connect.qq.com"  # Protocol parameter only; never sent over plaintext HTTP.
UA = ("Mozilla/5.0 (Linux; Android 12; 23117RK66C Build/V417IR; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
      "Chrome/101.0.4951.61 Mobile Safari/537.36 tencent_game_emulator")
HOSTS = {"xui.ptlogin2.qq.com", "ssl.ptlogin2.qq.com", "ptlogin2.qq.com",
         "ptlogin4.openmobile.qq.com", "openmobile.qq.com", "connect.qq.com", "imgcache.qq.com"}
KEYS = ("redirect_uri_key", "keystr", "key", "uikey", "superkey", "supertoken")


def qr_token(qrsig):
    token = 0
    for c in qrsig:
        token = (token * 33 + ord(c)) & 0x7FFFFFFF
    return token


def normalize(text):
    return html.unescape(text.replace("\\/", "/").replace("\\x26", "&")).strip()


def parameters(url):
    """Parse fragment/query and known nested URLs without evaluating JavaScript."""
    queue, seen, result = [normalize(url)], set(), {}
    nested = {"u1", "url", "jump_url", "redirect_uri", "redirect_url", "target_url",
              "s_url", "f_url", "qtarget", "jump", "ru"}
    while queue and len(seen) < 24:
        candidate = queue.pop(0)
        if candidate in seen:
            continue
        seen.add(candidate)
        p = urllib.parse.urlsplit(candidate)
        parts = [p.query, p.fragment]
        if not p.query and not p.fragment and "=" in candidate and "://" not in candidate:
            parts = [candidate]
        for part in parts:
            values = urllib.parse.parse_qs(part.lstrip("&"), keep_blank_values=True)
            for k, v in values.items():
                if v and v[0]:
                    result.setdefault(k, v[0])
                    if k in nested:
                        queue.append(normalize(v[0]))
        if not any(parts):
            decoded = urllib.parse.unquote(candidate)
            if decoded != candidate:
                queue.append(decoded)
    return result


def callback(text):
    m = re.search(r"ptuiCB\('([^']*)','([^']*)','([^']*)','([^']*)','([^']*)'", text)
    if not m:
        raise SafeError("login")
    return m.group(1), normalize(m.group(3))


def body_url(body):
    text = normalize(body)
    m = re.search(r"_Callback\s*\(\s*(\{.*?\})\s*\)\s*;?\s*$", text, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            value = data.get("url", "")
            if isinstance(value, str):
                return normalize(value)
        except (ValueError, AttributeError):
            pass
    for pattern in (
        r"ptui(?:_auth_)?CB\('[^']*','[^']*','([^']+)'",
        r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
        r"location\.replace\(\s*['\"]([^'\"]+)['\"]\s*\)",
        r"(auth://tauth\.qq\.com/[^\s\"'<>]+)",
        r"(https?://imgcache\.qq\.com/[^\s\"'<>]+)",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return ""


class QQLogin:
    def __init__(self):
        self.http = Http(HOSTS)
        self.login_params = {
            "pt_enable_pwd": "0", "appid": AID, "pt_3rd_aid": THIRD_AID, "daid": DAID,
            "pt_skey_valid": "0", "style": "35", "force_qr": "1", "autorefresh": "1",
            "s_url": CALLBACK, "refer_cgi": "m_authorize", "ucheck": "1", "fall_to_wv": "1",
            "status_os": "12", "redirect_uri": "auth://tauth.qq.com/", "client_id": THIRD_AID,
            "pf": "openmobile_android", "response_type": "token", "scope": "all", "sdkp": "a",
            "sdkv": "3.5.17.lite", "sign": "a6479455d3e49b597350f13f776a6288",
            "status_machine": "MjMxMTdSSzY2Qw==", "switch": "1", "time": "1763280194",
            "show_download_ui": "true", "h5sig": "trobryxo8IPM0GaSQH12mowKG-CY65brFzkK7_-9EW4",
            "loginty": "6",
        }
        self.login_url = XUI + "/cgi-bin/xlogin?" + urllib.parse.urlencode(self.login_params)
        self.headers = {"User-Agent": UA, "Referer": self.login_url,
                        "X-Requested-With": "com.tencent.apps.valorant"}

    def create(self):
        status, _, raw = self.http.request(self.login_url, headers={
            **self.headers, "Referer": "https://openmobile.qq.com/",
            "Cookie": "accountType=5; clientType=9", "Accept-Language": "zh-CN,zh;q=0.9"})
        if status != 200:
            raise SafeError("login")
        page = raw.decode("utf-8", "replace")
        match = re.search(r'g_login_sig=encodeURIComponent\("([^\"]+)"\)', page)
        self.login_sig = match.group(1) if match else self.http.cookie("pt_login_sig")
        version = re.search(r"/monorepo/([0-9A-Za-z]+)/ptlogin/js/", page)
        self.jsver = version.group(1) if version else "28d22679"
        status, _, png = self.http.request(XUI + "/ssl/ptqrshow", headers=self.headers,
            params={"s": "8", "e": "0", "appid": AID, "type": "0", "t": str(time.time()),
                    "u1": CALLBACK, "daid": DAID, "pt_3rd_aid": THIRD_AID})
        sig = self.http.cookie("qrsig")
        if status != 200 or not sig or not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise SafeError("login")
        self.token = qr_token(sig)
        return png

    def openlogin_data(self):
        q = self.login_params
        values = [("which", ""), ("refer_cgi", "m_authorize"), ("response_type", "token"),
                  ("client_id", THIRD_AID), ("state", ""), ("display", ""), ("openapi", "1011"),
                  ("switch", "1"), ("src", "1"), ("sdkv", q["sdkv"]), ("sdkp", "a"),
                  ("tid", self.http.cookie("idt") or str(int(time.time()))), ("pf", q["pf"]),
                  ("need_pay", "0"), ("browser", "0"), ("browser_error", ""), ("serial", ""),
                  ("token_key", ""), ("redirect_uri", q["redirect_uri"]), ("sign", q["sign"]),
                  ("time", q["time"]), ("status_version", ""), ("status_os", q["status_os"]),
                  ("status_machine", q["status_machine"]), ("page_type", "1"), ("has_auth", "1"),
                  ("update_auth", "1"), ("auth_time", str(int(time.time() * 1000))),
                  ("loginfrom", ""), ("h5sig", q["h5sig"]), ("loginty", "6")]
        return urllib.parse.urlencode(values)

    def poll(self, timeout=120):
        deadline = time.monotonic() + timeout
        opened = self.openlogin_data()
        aegis = self.http.cookie("__aegis_uid")
        server, client = self.http.cookie("pt_serverip"), self.http.cookie("pt_clientip")
        if not aegis and server and client:
            aegis = f"{server}-{client}-4458"
        while time.monotonic() < deadline:
            params = {"u1": CALLBACK, "from_ui": "1", "type": "1", "ptlang": "2052",
                      "ptqrtoken": str(self.token), "daid": DAID, "aid": AID, "pt_3rd_aid": THIRD_AID,
                      "pt_openlogin_data": opened, "device": "2", "ptopt": "1", "pt_uistyle": "35",
                      "jsver": self.jsver, "r": str(time.time()), "login_sig": self.login_sig}
            if aegis:
                params["aegis_uid"] = aegis
            status, _, raw = self.http.request(XUI + "/ssl/ptqrlogin", params=params, headers=self.headers)
            if status != 200:
                raise SafeError("login")
            code, url = callback(raw.decode("utf-8", "replace"))
            if code == "0":
                return self.resolve(url)
            if code == "65":
                raise SafeError("expired")
            if code not in {"66", "67"}:
                raise SafeError("login")
            time.sleep(2)
        raise SafeError("expired")

    def resolve(self, url):
        found, visited, tried_keys = {}, set(), set()
        for _ in range(12):
            if url:
                url = normalize(url)
                for key, value in parameters(url).items():
                    found.setdefault(key, value)
            if found.get("openid") and found.get("access_token"):
                return {"openid": found["openid"], "access_token": found["access_token"]}
            keys = [found.get(k, "") for k in KEYS] + [self.http.cookie(k) for k in KEYS]
            for key in keys:
                if not key or key in tried_keys:
                    continue
                tried_keys.add(key)
                status, _, raw = self.http.request("https://openmobile.qq.com/oauth2.0/m_get_redirect_url",
                    params={"keystr": key}, headers={"User-Agent": UA, "Referer": "https://imgcache.qq.com/"})
                if status == 200:
                    auth = parameters(body_url(raw.decode("utf-8", "replace")))
                    found.update(auth)
                    if found.get("openid") and found.get("access_token"):
                        return {"openid": found["openid"], "access_token": found["access_token"]}
            if not url or url in visited or url.startswith("auth://"):
                break
            visited.add(url)
            # An upstream HTTP callback is upgraded to HTTPS, with an exact host allowlist.
            if url.startswith("http://"):
                url = "https://" + url[7:]
            trusted_url(url, HOSTS)
            status, headers, raw = self.http.request(url, headers={"User-Agent": UA,
                                                                 "Referer": "https://openmobile.qq.com/"})
            if status in (301, 302, 303, 307, 308):
                url = urllib.parse.urljoin(url, headers.get("Location", ""))
            elif status == 200:
                url = body_url(raw.decode("utf-8", "replace"))
            else:
                raise SafeError("login")
        raise SafeError("login")


class QRPage:
    """Serve only the QR and fixed status locally. No credentials, cookies or tokens."""
    def __init__(self, png):
        self.png, self.status = png, "请用手机 QQ 扫码，并确认是掌上无畏契约授权。"
        self.path = "/" + secrets.token_urlsafe(24)
        page = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                expected = f"127.0.0.1:{self.server.server_port}"
                if self.headers.get("Host") != expected:
                    self.send_error(403)
                    return
                if self.path == page.path + "/qr.png":
                    content, mime = page.png, "image/png"
                elif self.path == page.path:
                    content = ("<!doctype html><meta charset='utf-8'><meta http-equiv='refresh' content='3'>"
                               "<title>掌瓦 QQ 扫码授权</title><h1>掌瓦 QQ 扫码授权</h1><p>不需要输入 QQ 密码。</p>"
                               f"<p>{html.escape(page.status)}</p>"
                               + (f"<img width='280' src='{page.path}/qr.png'>" if page.png else "")
                               ).encode("utf-8")
                    mime = "text/html; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; img-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(content)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}{self.path}"

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.png = b""
        self.server.shutdown()
        self.server.server_close()


def gh_call(executable, args, value=None):
    try:
        result = subprocess.run([executable, *args], input=value, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        raise SafeError("github") from None
    if result.returncode:
        raise SafeError("github")
    return result.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立 QQ 扫码，验证商店后安全上传 Repository Secret")
    parser.add_argument("--repo", help="你的 owner/repository")
    parser.add_argument("--gh", default="gh", help="已登录 GitHub CLI 的路径")
    parser.add_argument("--probe", action="store_true", help="仅测试二维码接口，不登录、不保存图片")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开本地二维码页面")
    args = parser.parse_args(argv)
    page = None
    try:
        if os.environ.get("GITHUB_ACTIONS"):
            raise SafeError("config")  # A QR login must never run in public Actions.
        if not args.probe:
            if not args.repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
                raise SafeError("github")
            if not shutil.which(args.gh):
                raise SafeError("github")
            gh_call(args.gh, ["auth", "status", "--hostname", "github.com"])
            gh_call(args.gh, ["api", f"repos/{args.repo}/actions/secrets/public-key"])
        login = QQLogin()
        png = login.create()
        if args.probe:
            print("QQ HTTPS 二维码接口可用；未登录、未保存或输出二维码/凭证。")
            return 0
        page = QRPage(png)
        page.start()
        print("仅本机可访问的扫码页面：" + page.url, flush=True)
        if not args.no_browser:
            webbrowser.open(page.url)
        credentials = exchange_qq(login.poll())
        page.png = b""
        page.status = "QQ 授权已完成，正在验证每日商店并写入 GitHub Secret。"
        query_store(credentials)
        gh_call(args.gh, ["secret", "set", "VALORANT_AUTH_JSON", "--repo", args.repo],
                json.dumps(credentials, ensure_ascii=True).encode("utf-8"))
        page.status = "已成功验证每日四款皮肤，并安全写入 GitHub Secret。可以关闭此页面。"
        print("成功：已验证每日四款皮肤并写入 VALORANT_AUTH_JSON；凭证未落盘、未打印。", flush=True)
        time.sleep(4)
        return 0
    except SafeError as e:
        print(f"失败 [{e.code}]：{e}", flush=True)
        return 1
    except KeyboardInterrupt:
        print("已取消授权。", flush=True)
        return 1
    except Exception:
        print(str(SafeError("internal")), flush=True)
        return 1
    finally:
        if page:
            page.close()


if __name__ == "__main__":
    raise SystemExit(main())
