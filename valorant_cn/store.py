"""Own-account daily store and PushPlus delivery. SPDX-License-Identifier: AGPL-3.0-only."""
import html
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from .core import Http, SafeError, check_result, safe_value

BASE = "https://app.mval.qq.com"
UA = ("mval/2.6.0.10062 Channel/5 Mozilla/5.0 (Linux; Android 16; wv) "
      "AppleWebKit/537.36 Mobile Safari/537.36")
BEIJING = timezone(timedelta(hours=8))


def load_auth(raw):
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError
        fields = ("userId", "tid", "openid", "access_token", "uin")
        auth = {k: safe_value(data[k]) for k in fields if k in data}
        if not auth.get("userId") or not auth.get("tid"):
            raise ValueError
        if bool(auth.get("openid")) != bool(auth.get("access_token")):
            raise ValueError
        if "uin" in auth and not auth["uin"].isdigit():
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise SafeError("config") from None
    return {"version": 1, **auth}


def cookie_header(auth):
    values = {"clientType": "9", "appid": "102061775", "acctype": "qc",
              "accountType": "5", "userId": auth["userId"], "tid": auth["tid"],
              "uin": "o" + auth.get("uin", "0"), "openid": auth.get("openid", "null"),
              "access_token": auth.get("access_token", "null")}
    return "; ".join(f"{k}={safe_value(v)}" for k, v in values.items())


def exchange_qq(login, http=None):
    http = http or Http({"app.mval.qq.com"})
    openid, token = safe_value(login["openid"]), safe_value(login["access_token"])
    result = http.json(
        BASE + "/go/auth/login_by_qq?source_game_zone=agame&game_zone=agame",
        headers={"User-Agent": UA, "Cookie": "clientType=9; openid=null; access_token=null;"},
        body={"clienttype": 9, "config_params": {"client_dev_name": "23117RK66C", "lang_type": 0},
              "login_info": {"appid": 102061775, "openid": openid, "qq_info_type": 5,
                             "sig": token, "uin": 0},
              "mappid": 10200, "mcode": "132f0a77d34402abc8463d60100011d19b0e",
              "source_game_zone": "agame", "game_zone": "agame"})
    check_result(result)
    data = result.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("login_info"), dict):
        raise SafeError("protocol")
    info = data["login_info"]
    try:
        auth = {"version": 1, "userId": info["user_id"], "tid": info["wt"],
                "uin": str(info.get("uin", "0")), "openid": openid, "access_token": token}
        return load_auth(json.dumps(auth))
    except (KeyError, TypeError, AttributeError):
        raise SafeError("protocol") from None


def parse_store(result):
    check_result(result)
    data = result.get("data")
    if isinstance(data, dict):
        sections = [data]
    elif isinstance(data, list):
        sections = data
    else:
        raise SafeError("store")
    daily = [s for s in sections if isinstance(s, dict) and s.get("key") == "dailystore"]
    # Older versions return a single unlabelled object. Never select a labelled bundle/night market.
    if not daily and len(sections) == 1 and isinstance(sections[0], dict) and not sections[0].get("key"):
        daily = sections
    if len(daily) != 1 or not isinstance(daily[0].get("list"), list) or len(daily[0]["list"]) != 4:
        raise SafeError("store")
    items = []
    for goods in daily[0]["list"]:
        if not isinstance(goods, dict) or not isinstance(goods.get("goods_name"), str) or not goods["goods_name"].strip():
            raise SafeError("store")
        # rmb_price is already the upstream display price; never divide by 100 or invent VP.
        price = goods.get("rmb_price")
        if (not isinstance(price, (str, int, float)) or isinstance(price, bool)
                or not str(price).strip() or len(str(price)) > 40):
            raise SafeError("store")
        items.append({"name": goods["goods_name"][:200], "price": str(price),
                      "image": image_url(goods.get("goods_pic", ""))})
    return items


def image_url(value):
    """Only public Tencent static images; omit query strings that could carry credentials."""
    if not isinstance(value, str):
        return ""
    try:
        p = urlsplit(value)
        host = p.hostname or ""
        if (p.scheme not in ("http", "https") or p.username or p.password or p.query or p.fragment
                or p.port not in (None, 443) or not any(host == d or host.endswith("." + d)
                                                     for d in ("gtimg.com", "qpic.cn", "game.gtimg.cn"))):
            return ""
        return "https://" + host + p.path
    except ValueError:
        return ""


def query_store(auth, http=None):
    http = http or Http({"app.mval.qq.com"})

    def query(credentials):
        result = http.json(BASE + "/go/mlol_store/agame/user_store",
                           headers={"User-Agent": UA, "Cookie": cookie_header(credentials),
                                    "GH-HEADER": "1-2-105-160-0", "Accept-Language": "zh-CN"},
                           body={"_t": int(time.time()), "scene": "", "source_game_zone": "agame", "game_zone": "agame"},
                           retries=3)
        return parse_store(result)
    try:
        return query(auth)
    except SafeError as e:
        if e.code != "auth" or not auth.get("openid") or not auth.get("access_token"):
            raise
    # Stateless Actions cannot retain rotating CT safely. Re-exchange the QQ grant only when needed.
    renewed = exchange_qq(auth, http)
    if renewed["userId"] != auth["userId"]:
        raise SafeError("auth")
    return query(renewed)


def render(items):
    date = datetime.now(BEIJING).strftime("%Y-%m-%d")
    parts = [f"<h2>无畏契约国服每日商店 · {date}</h2>"]
    for i, item in enumerate(items, 1):
        price = item["price"]
        try:
            number = Decimal(price)
            if number.is_finite() and number >= 0:
                price = "¥" + price
        except InvalidOperation:
            pass
        parts.append(f"<h3>{i}. {html.escape(item['name'])}</h3><p>{html.escape(price)}</p>")
        if item["image"]:
            parts.append(f'<img src="{html.escape(item["image"], quote=True)}" width="300" alt="皮肤图片">')
    parts.append("<p>价格按掌瓦接口原样展示，以游戏内商店为准。图片加载失败不影响文字。</p>")
    return f"无畏契约每日商店 {date}", "".join(parts)


def push(token, title, content, http=None):
    token = safe_value(token)
    http = http or Http({"www.pushplus.plus"})
    try:
        result = http.json("https://www.pushplus.plus/send", body={"token": token, "title": title,
                           "content": content, "template": "html", "channel": "wechat"})
        if str(result.get("code")) != "200":
            raise SafeError("push", service_code=result.get("code"))
    except SafeError as e:
        # Sending is not idempotent: do not blindly retry and spam on ambiguous timeouts.
        raise SafeError("push", http_status=e.http_status, service_code=e.service_code) from None
    return result.get("data")  # Acceptance is asynchronous, not proof of WeChat delivery.
