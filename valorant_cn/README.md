# 掌瓦国服每日商店 → 微信

独立 Python 3.11+ 程序，无 AstrBot、青龙、QQ 密码、浏览器自动化或第三方 Python 依赖。
浏览器只用于在本机显示二维码；登录网络请求全部由 Python 通过 HTTPS 完成。
每日任务在 GitHub 托管 runner 执行，本人电脑和手机无需持续在线。

## 已提供的文件

- `python -m valorant_cn.qq_login`：QQ 扫码、换取掌瓦凭证、查询本人商店验证、直接写 Secret。
- `python -m valorant_cn`：查询每日四款皮肤，通过 PushPlus 微信渠道发送名称、价格和可用图片。
- `.github/workflows/valorant-daily.yml`：每天北京时间 **08:17** 与手动运行。
- `.github/workflows/valorant-tests.yml`：无 Secrets 的离线回归测试。
- `valorant_cn/requirements.txt`：明确无第三方依赖，不影响原仓库其他脚本。

## 首次授权

所有命令从仓库根目录执行。你只需授权 GitHub 并用手机 QQ 扫码，程序自动创建 Secret。

1. 本机安装 Python 3.11+ 与官方 GitHub CLI，完成 GitHub 登录：

   ```sh
   gh auth login --hostname github.com --git-protocol https --web --scopes workflow
   ```

   `workflow` 用于发布 Actions 文件。已有登录且只更新 Secret 时无需重复登录。
   也可使用只限定本仓库的细粒度令牌；上传 Secret 需 Secrets 写权限，
   发布代码另需 Contents 与 Workflows 写权限。不要将 GitHub 令牌放入聊天或源码。

2. 启动独立扫码工具：

   ```sh
   python -m valorant_cn.qq_login --repo xukun1964-web/QinglongScripts
   ```

   本机浏览器出现二维码后，用 **登录游戏账号的手机 QQ** 扫码并确认掌上无畏契约授权。
   工具会验证获取到四款每日皮肤，再通过 `gh secret set` 的标准输入上传 `VALORANT_AUTH_JSON`。
   不把凭证放到命令行参数、终端、文件、二维码网页或 Git。
   工具默认约两分钟超时；腾讯可能提前使二维码失效，重新运行即可。
   没有 QQ 密码输入界面。GitHub CLI 路径可用 `--gh PATH` 指定。

3. 保留已存在的 Repository Secret **`PUSH_PLUS_TOKEN`**，不要重建或公开它。
   在 Actions 中启用 fork 的工作流，然后选择 **Valorant CN Daily Store → Run workflow → daily**。
   `check` 只验证商店，`push-test` 只发送一条通道测试通知；默认 `daily` 查询并推送。

## Secrets 与安全边界

| Secret | 用途 |
| --- | --- |
| `VALORANT_AUTH_JSON` | 扫码工具自动生成，含版本、userId、tid、openid、QQ access_token 与 uin |
| `PUSH_PLUS_TOKEN` | 已有的 PushPlus 推送令牌 |

只保存查询本人商店所需的授权。完整 QQ Cookie、登录二维码和扫码会话不持久化。
扫码页面仅监听 `127.0.0.1`，路径随机，禁缓存，无第三方脚本；QR 图片只存在内存。
不要分享扫码页面或二维码。程序拒绝在 GitHub Actions 中运行扫码登录。
账户数据只发给腾讯官方固定 HTTPS 主机；皮肤内容发给你配置的 PushPlus。
图片只允许腾讯静态资源域名，带查询参数的图片会省略，避免把签名或凭证转给 PushPlus。

云端 Secrets 只注入最后的查询步骤，不传给依赖安装与离线测试；GitHub token 只有 contents:read。
使用固定 SHA 的官方 Actions；禁止在非默认分支运行带 Secrets 的查询。
不输出服务器响应、异常 traceback、Cookie、登录地址或商店内容；不上传日志/凭证 artifact，
不缓存授权，不导入原有 notifier.py，也不执行原有其他签到脚本。
Pull request 测试从不读取 Secrets；没有 pull_request_target 或外部回调。

原仓库中的 ValorantStore.py 仍供原青龙用户使用；新的工作流只运行本目录的独立程序。
以上日志和凭证保护描述仅适用于新增程序，不能当作整个旧仓库所有脚本的安全保证。

## 登录时效和限制

`tid` 失效时，用 Secret 中的 QQ 授权尝试再次调用 `login_by_qq` 换取新票据，并核对仍是同一用户。
新票据只保留在当前 runner 内存，下次仍可尝试换票。QQ 授权本身的有效期、撤销、
风控和异地登录限制由腾讯决定，无法保证一次扫码永久有效，也没有经过长期无人值守验证。
此版本不轮换 client ticket，不需另给云端 Secrets 写入 PAT；不使用公开缓存存储轮换凭证。
若重新换票失败，会标记任务失败并通过 PushPlus 提醒再次在本机扫码更新 Secret。

程序只选 `dailystore` 分区；兼容旧接口的单一无标签对象。返回不足四款、价格缺失、
分区缺失或格式改变均作为失败，不把套装/夜市当每日商店，不把未知价格当 0 元。
价格使用 `rmb_price` 原显示值，不除以 100、不凭空换算 VP。

查询有超时和有限退避重试；PushPlus 的发送不自动重试，避免超时后重复推送。
PushPlus HTTP/业务失败会使任务失败。`code=200` 仅表示**接受异步投递**，
不等于微信已送达；投递失败需在 PushPlus 后台查看。本程序没有配置公共回调服务。
PushPlus 也不可用时，故障提醒无法送达；可查看 Actions 红色失败记录。

GitHub schedule 不是精确计时服务，繁忙时会延迟甚至丢弃；工作流须位于默认分支。
公开仓库长期无活动（GitHub 文档当前规定 60 天）可能自动停用定时工作流，
届时需在 Actions 中重新启用。首次 fork 也可能需本人启用 Actions。
本项目不会为了保持活跃自动伪造提交。

## 安全测试

```sh
python -m pip install -r valorant_cn/requirements.txt
python -m unittest discover -s tests/valorant_cn -v
python -m valorant_cn.qq_login --probe
```

离线测试使用合成凭证和模拟接口，覆盖分区、格式异常、过期换票、身份一致、
HTML 转义、域名白名单、敏感错误脱敏、PushPlus 失败和 stdin 上传。
`--probe` 只向腾讯请求未登录二维码，检查协议响应，不保存或显示二维码，不会登录。
测试通过不能替代本人 QQ 扫码后的真实商店与微信端到端验收。

来源、版权及 AGPL-3.0 许可说明见 [NOTICE.md](NOTICE.md) 与 [LICENSE](LICENSE)。
