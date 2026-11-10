# AnyMessage

用你熟悉的 AI，查清聊天里的决定。

通过 MCP，让 Codex、Claude 或 Cursor 只读访问 Mac 本地的微信聊天记录，或明确选择的 Telegram Desktop JSON 导出文件。
你可以问项目的规格改了什么、哪些方案已经确认、还有什么需要跟进。
AI 负责分析，AnyMessage 返回消息、来源 ID、查询范围和支持的附件内容。

[English](README.md) | [虚构项目演示](docs/demo.md) | [使用示例](docs/recipes.md)

![Atlas 虚构项目的 MCP 输出回放](.github/assets/demo.gif)

这是 0.4.0 原始 Atlas 演示的 MCP 输出回放，播放时间经过调整，不调用 AI，也未展示后续版本的上下文读取。
[观看 30 秒视频](.github/assets/demo.mp4)。

## 先试一个虚构项目

AnyMessage 0.8.0 支持无需账号密钥的 Telegram 文本、上下文和本地附件读取。安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 后，可以把[虚构 River 聊天](examples/telegram-fictional.json)和[规格文件](examples/river-spec-v2.txt)下载到同一文件夹，再启动 MCP：

```sh
curl --fail --location https://raw.githubusercontent.com/knchst/AnyMessage/main/examples/telegram-fictional.json --output telegram-fictional.json
curl --fail --location https://raw.githubusercontent.com/knchst/AnyMessage/main/examples/river-spec-v2.txt --output river-spec-v2.txt
uvx --from git+https://github.com/knchst/AnyMessage anymessage mcp --telegram-export "$PWD/telegram-fictional.json"
```

这会启动 stdio MCP 服务器，需要在 AI 客户端中使用[完整手动配置](docs/telegram-export.md#connect-your-ai)。
不需要安装微信或 SQLCipher，也不读取已保存的微信配置。
先列出会话，在 River 项目中搜索 `proposal`，再读取命中的上下文，就能看到相邻的批准消息。继续查看消息 `6` 的附件列表，用返回的 ID 读取实际规格文本。
自己的数据可在 Telegram Desktop 中选择导出聊天记录，格式选 JSON。
文件上限为 16 MiB，整个 JSON 在启动时载入内存，修改文件后需重启服务器。
经典导出的附件可读取受限的文档文本、原生图片或明确请求的原始字节。`rich_message` 正文仍不支持，导出也不保证完整云端历史。
附件反映当前本地文件；没有可信的 Telegram 内容摘要，因此成功读取后 `content_verified` 仍为 `false`。
Telegram 的 ID、游标与覆盖范围说明见[导出文档](docs/telegram-export.md)。

以下是原有的微信演示。

演示使用临时生成的虚构聊天和附件，通过真实的 MCP 工具读取。
需要 SQLCipher，不需要安装微信、获取账号密钥或订阅 AI 服务。
微信演示需要 AnyMessage 0.4.0 或更新版本。

安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 后运行：

```sh
brew install sqlcipher
uvx --from git+https://github.com/knchst/AnyMessage anymessage demo
```

安装 AnyMessage 0.4.0 或更新版本后，也可以直接运行 `anymessage demo` 或 `anymessage demo --mcp`。
终端演示只展示工具返回的证据，不调用 AI，也不自动生成分析结论。
虚构的 Atlas 项目里有已批准的电池变更、尚未确认的交期和无法读取的图纸。
AnyMessage 0.5.0 的终端演示还会读取指定 UTC 日期的消息，并同时搜索项目群和供应商群。
AnyMessage 0.6.0 会把实际搜索命中的提案传给 `get_message_context`，读取相邻的批准消息与规格附件。

| 事项 | 人工核对的结论 | 消息来源 |
| --- | --- | --- |
| 电池 | Maya 已批准从 400 mAh 改为 500 mAh。 | `message/message_0.db:3`，2026-11-09 09:03 UTC。 |
| 数量 | 试产数量仍为 50。 | 同一条批准消息和消息 `:4` 的规格附件。 |
| 交期 | 11 月 26 日只是提议，工厂尚未确认。 | Chen 的消息 `:7`，09:07 UTC，以及规格附件。 |
| 厚度 | 还需要图纸才能确认；消息 `:6` 的图纸缓存不存在。 | 消息 `:5`、`:6`、`:8`。 |

这张表是对虚构数据的人工核对示例，不是命令自动生成的答案。
所有简写 ID 的前缀都是 `message/message_0.db:`，会话 ID 为 `atlas-demo@chatroom`。

要让 AI 分析同一份虚构数据，请在本地 MCP 客户端中创建独立的 `anymessage-demo` 连接，使用以下命令作为服务器：

```sh
uvx --from git+https://github.com/knchst/AnyMessage anymessage demo --mcp
```

[演示文档](docs/demo.md#connect-your-ai)提供 JSON 和 Codex TOML 配置。
重新连接后，可以这样问：

> 只使用 anymessage-demo 工具，先检查 status。阅读 Atlas 试产项目的聊天和可读取的附件，分别列出已批准的变更、待批准的提议和未解决的问题。每个结论都写明发送者、UTC 时间和消息 ID。列出无法读取的附件，并核对 11 月 26 日交期是否已经批准。

## 连接自己的微信

```sh
brew install knchst/tap/anymessage
anymessage setup
```

安装会包含 Python 和 SQLCipher。
支持 MCPB 0.4 和 UV 的宿主可参考[分发说明](docs/distribution.md)。仍需 SQLCipher 和明确的账号设置；格式验证不代表已在某个桌面宿主完成安装验证。
设置会优先复用已有的有效密钥，默认注册 Codex；完成后需要重启或重新加载 MCP 连接。
没有密钥时，需要阅读[首次设置说明](docs/wechat-setup.md)，通过明确授权的临时微信副本获取。
该步骤可能需要手机确认或登录。
普通读取和 `doctor` 不会启动密钥获取，也不会修改微信应用。

```sh
anymessage mcp add claude-desktop
anymessage mcp add claude-code
anymessage mcp add cursor
anymessage doctor
```

完整的配置、环境变量、其他本地客户端和 HTTP 连接方式见[英文参考](README.md)。

## 当前支持范围

- 默认数据源为微信 4.x；已验证的环境是 Apple Silicon 和微信 4.1.15，其他版本需要验证。0.8.0 也支持明确选择的 Telegram Desktop JSON 导出及本地附件。
- 七个工具均为只读，支持会话列表、按时间范围分页读取消息、指定消息的上下文、在明确选择的 1–8 个会话内共享扫描额度的文本搜索，以及支持格式的本地附件读取。
- 微信附件包括 PDF、DOCX、XLSX、PPTX 和 Unicode 文本；支持部分原始图片及 V2 图片缓存。Telegram 经典导出支持同类文档文本、图片和明确请求的受限原始字节；不会自动下载文件，也不会认证媒体来源。
- 未实现发送、删除、云端聊天同步、全会话搜索、语义搜索、OCR、语音和视频解码。
- 本地不存在的附件不会自动下载；缩略图和原图分别标识。

先调用 `status`，再用 `list_conversations` 获取会话 ID。
以下数据库、游标及附件细节描述微信数据源。Telegram 使用相同的七个工具参数，但身份、快照和覆盖范围遵循[独立约定](docs/telegram-export.md#source-contract)。
按时间范围读取与多会话搜索需要 AnyMessage 0.5.0 或更新版本。
指定消息的上下文读取需要 AnyMessage 0.6.0 或更新版本。
`get_messages` 接受一个 `conversation_id`。
`search_messages` 必须且只能提供 `conversation_id` 或 `conversation_ids`，后者需要 1–8 个不重复的 ID，空列表不代表全部会话。
两者都接受可选的 `start_time` 和 `end_time`，使用严格的 64 位 UTC Unix 秒整数，范围包含起点、不包含终点。
同时提供两者时，起点必须小于终点。
搜索使用解码后的文本做不区分大小写的字面匹配，不搜索附件正文。
`limit` 为 1–200，`scan_limit` 为 1–10000，扫描额度在本次选择的全部会话间共享。
消息引用和去重必须同时使用 `(conversation_id, id)`，因为不同会话中的消息 ID 可能相同。
继续搜索时，保持原来的会话选择、日期范围和关键词，直接传回 `next_cursor`。
带日期或多个会话的游标还绑定当前本地账号；无日期的单会话游标保留旧格式，无法校验账号身份。
搜索结果如果有 `incomplete` 或继续游标，只代表已扫描的范围。
`scanned` 统计本次实际检查的记录，包含不支持的消息类型和解码失败，不包含额外预取的记录。
`conversation_ids`、`start_time` 和 `end_time` 返回本次范围；`snapshot_consistency="per_query"` 表示不同数据库分别读取已提交状态。
日期转换和继续查询示例见[使用示例](docs/recipes.md#search-selected-chats-within-a-date-range)。
把搜索命中的 `conversation_id` 和 `id` 分别传给 `get_message_context` 的 `conversation_id` 和 `message_id`。
`before`、`after` 均为严格整数，范围 0–50，默认各 3 条，包含不支持的类型和解码失败记录。
结果按时间从旧到新排列，最多 101 条；目标消息只出现一次，`anchor_index` 是从 0 开始的位置。
`has_more_before`、`has_more_after` 表示各侧是否还有本地记录，即使请求数量为 0 也会检查。
结果返回单个 `conversation_id` 和 `message_id`，不提供 `next_cursor`。
会话存在但目标缺失或不唯一时返回 `message_not_found`；所有分片都没有该会话表时返回 `conversation_not_found`。
消息 ID 保留原分片名称，本地 ID 必须是无前导零的正 ASCII 十进制整数，且不超过 `9223372036854775807`。
上下文会检查所有支持的消息分片；缺少密钥或无法读取时直接失败，不返回局部结果。
`coverage="local_message_shards"` 仅表示本地范围，`snapshot_consistency="per_query"` 表示每条 SQL 查询分别读取已提交状态，不保证整个窗口为同一快照。
具体调用见[读取搜索命中的上下文](docs/recipes.md#inspect-a-search-hit)。
未找到缓存不能单独证明附件已删除或已过期。

读取在本机进行，但 AI 客户端可能把返回的内容发送给模型服务商。
普通 MCP 连接默认使用 stdio；显式启动的 HTTP 服务仅监听 `127.0.0.1`，不提供应用级认证。
详见[安全说明](SECURITY.md)和[附件格式与限制](docs/attachments.md)。

## 参与开发

欢迎补充兼容性验证、使用示例和导入步骤的反馈。
[试用反馈](docs/pilot.md)可以记录首次使用结果，以及 14 天内是否在另一个问题上再次使用。
请使用虚构数据复现问题，不要上传真实消息、账号、密钥或数据库。
开发和验证步骤见[贡献指南](CONTRIBUTING.md)。

AnyMessage 使用 [MIT 许可证](LICENSE)。
