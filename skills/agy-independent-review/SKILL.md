---
name: agy-independent-review
description: 用官方 Antigravity CLI（agy）和已授权的 Vault 凭据，对固定版本的公开 GitHub PR 做只读、独立的代码审查。适用于 Vault 登录、让 agy 按需读取本地源码和 diff、在同一会话补查并收取报告；不负责发布、合并或长期自动化。
---

# Antigravity 独立审查

## 1. 从 Vault 领取凭据并设置临时登录目录

- 在自己控制的终端直接使用官方 CLI，只审查已授权且可提交给 Antigravity 的公开源码。使用你有权使用的登录凭据。该流程不是第三方 agent 或 MCP 的调用入口，不用于绕过账号或订阅限制。若无合适的凭据交付方式，使用官方交互登录，而不是导出别人的会话。
- 使用与你的 Vault 配套、经验证的加密凭据交付流程。选用自己授权的条目（例如占位符 `<VAULT_AGY_RECORD>`），本地生成每次操作的密钥对，只提交公钥，领取密文后在本机解密，并仅向本次获准的子进程注入 `AGY_OAUTH_JSON`。不要在终端历史、聊天、命令参数或日志中输出明文；不要把登录状态转交第三方，不拆取或输出 OAuth 字段。
- 在权限为 `0700` 的临时目录下创建独立 `HOME`，把 Vault 交付的完整原始 OAuth JSON 写入 **该临时 HOME** 的 `~/.gemini/antigravity-cli/antigravity-oauth-token`，文件权限 `0600`。保持原始内容，不拆分、不重新编码为其他凭据格式；这是已验证的本地登录文件路径，不是 Google 承诺稳定的导入接口。不要覆盖现有账户的登录配置。
- 在 Vault helper 启动的子进程中，可用下面的 Python 片段写文件；后续启动 agy 时不要再传 `AGY_OAUTH_JSON`，只传必要的运行环境，不传 GitHub 或其他账户凭据。

```python
import json
import os
from pathlib import Path

# HOME must already point to an owner-only, disposable directory.
raw = os.environ.pop("AGY_OAUTH_JSON")
if not isinstance(json.loads(raw), dict):
    raise ValueError("Expected an OAuth JSON object")
directory = Path.home() / ".gemini" / "antigravity-cli"
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
token_file = directory / "antigravity-oauth-token"
fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "wb") as f:
    f.write(raw.encode("utf-8"))
del raw
```

## 2. 准备固定版本的只读审查工作区

- 核实 PR 的仓库、当前 head 和目标分支 base，计算两者的 merge base。将该 head 的公开 Git 源码导出到独立的工作目录，另外放入从 merge base 到 head 的完整 unified diff 和变更文件概览。把准确的 SHA 和文件相对路径交给 agy。不要把 diff 正文粘贴进提示或切成多次独立审查。
- 登录目录必须与审查目录分离。只给 agy 工作区内的文件读取、列目录和搜索权限；禁止写文件、命令、URL/浏览器和 MCP 工具，明确禁止读取临时登录目录。使用 `plan` 模式和 `request-review` 权限，不使用自动放行。只导出公开源码与审查材料，不带 Git 凭据、已有代理的指令或配置、外指符号链接。

- 在临时 HOME 的 `~/.gemini/antigravity-cli/settings.json` 设置以下权限。用实际绝对路径替换示例；不要放行意外的工作区外访问。这是 CLI 层面的权限设置，不是操作系统级文件隔离。只在隔离环境中运行，不挂载其他私人文件；它不能代替经过验证的沙箱。

```json
{
  "agentMode": "plan",
  "toolPermission": "request-review",
  "permissions": {
    "allow": ["read_file(/absolute/review-workspace)"],
    "ask": [],
    "deny": [
      "read_file(/absolute/temporary-home)",
      "write_file(*)", "command(*)", "read_url(*)", "execute_url(*)", "mcp(*)"
    ]
  }
}
```

## 3. 开新会话并收取报告

- 从账户实际可用的模型中选最新、适合复杂度的档位。实质性 PR review 用较强模型和更高推理强度；`gemini-4-neon-xhigh` 是这次跑通的选型，不是固定要求。
- 从审查工作目录启动官方 CLI。可用 `agy --input-format stream-json --output-format stream-json --mode plan --model MODEL --effort EFFORT --disable-slash-commands`；已核对模型支持所选档位。保持同一个进程和会话，不设固定 `--print-timeout`。
- 首轮一次发齐 PR、head/merge base、工作区和 diff 路径、任务与权限范围、交付要求。让 agy 自己决定阅读顺序和审查策略。不分步喂任务，不给它看 CodeRabbit、其他审查者或自己的初步发现。
- 把完整提示作为一条 JSON 行写入 stdin：`{"event":"user","message":{"content":"<完整审查提示>"}}`。提示要求只报 PR 引入、能用证据核实的缺陷，每项给严重度、精确文件和变更行、触发条件、实际影响、证据与修法；报告检查范围及遗漏，不把静态审查看成测试通过。不发布、不修改任何内容。
- 从 `init` 或 `result` 记下 `conversation_id`。若报告缺证据、缺覆盖或没有正文，只在同一个仍运行的会话发送必要的补问，核对是同一 ID；不预设审查轮次，不把别的审查结论作为提示。
- 只在收到匹配会话的 `result`、状态为 `SUCCESS` 且有非空审查正文时收取报告；空结果不算完成。保留报告、提交 SHA、验证范围和限制。结束后关闭 CLI 并清理临时凭据和分离的 HOME。到此为止，不自动发表到 GitHub。

参考：[官方登录](https://antigravity.google/docs/install?tab=cli) · [Antigravity FAQ](https://antigravity.google/docs/faq) · [agy 流式输入和输出](https://antigravity.google/docs/cli/headless/) · [agy 工具权限](https://antigravity.google/docs/permissions)。
