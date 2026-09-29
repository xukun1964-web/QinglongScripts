"""Cloud entry point; no sensitive values ever written to stdout/stderr.
SPDX-License-Identifier: AGPL-3.0-only
"""
import argparse
import os
from .core import SafeError, safe_value
from .store import load_auth, push, query_store, render


def main(argv=None):
    parser = argparse.ArgumentParser(description="国服每日商店 → PushPlus")
    parser.add_argument("--check-only", action="store_true", help="查询并验证四款皮肤，不推送、不打印商店内容")
    parser.add_argument("--push-test", action="store_true", help="发送一条不含账号信息的 PushPlus 测试消息")
    args = parser.parse_args(argv)
    token = os.environ.get("PUSH_PLUS_TOKEN", "").strip()
    try:
        if not args.check_only:
            safe_value(token)
        if args.push_test:
            if args.check_only:
                raise SafeError("config")
            push(token, "掌瓦推送通道测试", "<p>GitHub Actions → PushPlus 通道测试消息。</p>")
        else:
            auth = load_auth(os.environ.get("VALORANT_AUTH_JSON", ""))
            items = query_store(auth)
            if args.check_only:
                print("验证通过：已获取每日四款皮肤；未推送、未输出私人商店数据。")
                return 0
            push(token, *render(items))
        print("PushPlus 已接受消息（异步投递，请以微信实际收到为准）。")
        return 0
    except SafeError as e:
        print(f"失败 [{e.code}]：{e}")
        if token and not args.check_only and not args.push_test and e.code not in {"push", "delivery"}:
            try:
                push(token, "掌瓦每日商店运行失败", f"<p>{e}</p>")
            except Exception:
                print("故障提醒也未能送达 PushPlus，请检查 GitHub Actions 状态。")
        return 1
    except Exception:
        # Never print traceback/exception repr: network exceptions can include signed URLs.
        print(str(SafeError("internal")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
