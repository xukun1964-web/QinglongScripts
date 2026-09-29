# 来源与许可

本独立程序采用 GNU AGPL-3.0-only；完整许可证见本目录 LICENSE。
根目录原有 MIT 许可与已有脚本的版权声明保持不变。

QQ 协议实现改编、重写自 GuJi08233 及贡献者的
[astrbot_plugin_val_shop](https://github.com/GuJi08233/astrbot_plugin_val_shop)，
研究时版本 v3.2.6，提交 `55bb649585bc39a3d775a95da01e1a099d7dd499`。
上游 README 称 MIT，但实际 LICENSE 为 AGPL-3.0，本实现遵循实际 LICENSE。
变更包括：移除 AstrBot/aiohttp/Pillow 依赖；改为标准库 HTTP 客户端；
去除个人账号常量及敏感日志；限制重定向；只在本机内存展示二维码；使用 stdin 上传 GitHub Secret。
协议中的 QQ appid、SDK sign、h5sig 与设备类型是上游协议参数，不是本人的登录凭证。
这些协议参数可能随上游接口调整而失效，扫码后的完整链路必须以真实验证为准。

商店分区选择、票据含义同时参考本仓库 MIT 许可的 ValorantStore.py（CN-Grace）及
[nonebot-plugin-varolant](https://github.com/luoye520ww/nonebot-plugin-varolant) 的当前实现。
后者包含持续轮换 client ticket 的方案，本程序未复制其机器人/数据库代码，
也没有将 client ticket 保活数据库放入公开缓存或 artifact。

PushPlus 使用[官方消息接口](https://www.pushplus.plus/doc/guide/api.html)。
GitHub 调度限制参考[官方 schedule 文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。
