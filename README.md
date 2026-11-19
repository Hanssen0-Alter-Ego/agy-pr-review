# agy-pr-review

在自己的 VPS 上，按需对一个已允许的 GitHub PR 做独立、只读的代码质量审查。操作者在 SSH 终端启动任务，VPS 直接用官方 Antigravity CLI（`agy -p`）生成报告。可选的 MCP 服务只供查看**已有**报告，不收提示词、不启动审查、不访问 GitHub，也不对 PR 发评论或审批。

## 使用边界

Google 的 [Antigravity FAQ](https://antigravity.google/docs/faq) 将 `agy -p` 定位为直接脚本和 CI 用途；个人方案不支持在第三方 coding agent / 应用内包一层调用，也不支持自动换号。这里刻意不提供从聊天、MCP 或外部 webhook 触发 `agy` 的能力：先在自己控制的机器上直接生成报告，再由独立的只读服务交付结果。需要让第三方应用直接调用模型时，请走 Google 官方的 Gemini API / 企业路线，不要复用 Antigravity 登录会话。

Google 的 [Remote Agents](https://antigravity.google/docs/remote-agents#antigravity-cli) 则是另一条路：`agy --remote-agent` 在 Google 托管环境运行会话，可通过远程项目连接 GitHub 并附加仓库。[Google 的支持列表](https://antigravity.google/docs/remote-agents/#features-not-available-in-remote-agent-sessions) 明确说明远程会话不支持 `--print` / `-p`，也不会继承本地 MCP 或配置。本项目不使用或依赖 Remote Agents。

审查范围是功能正确性、回归、兼容性、测试覆盖和可维护性；不会安装 PR 的依赖、执行 PR 代码、运行测试或改动远端。结论需要人工核查，不是合并授权，也不代表测试通过。

## 部署与首次验收

1. 在有 Python 3、Docker Engine 和 Compose v2 的 Linux VPS 上放置本项目，用自己的 SSH 终端部署。复制 `.env.example` 为 `.env`，按注释保留非敏感设置；在 `config.json` 中显式允许要审查的仓库。初次测试优先选公开仓库，例如 `ckb-devrel/ccc`。
2. 构建镜像（CLI 安装来源见 [Google 官方安装说明](https://antigravity.google/docs/cli/tutorial)），启动专用 runner，然后在 SSH 终端初始化或解锁其独立的 GNOME Keyring，再运行交互登录。按照 [Google 的 Remote SSH OAuth 流程](https://antigravity.google/docs/install?tab=cli)，用本机浏览器打开终端给出的网址，再仅在发起登录的远端终端输入对应的授权码。不要把授权网址、授权码、会话文件、密钥环内容或 GitHub PAT 贴到聊天里。
3. 登录与正式审查复用同一个专用、持久化的 CLI 用户目录。退出交互 CLI（不要执行 `/logout`），再用同一 runner 的新进程跑一次公开 PR；只有这一步成功，才说明这个 VPS 的会话缓存、密钥环和无交互运行都可用。也建议重启 runner 后重新解锁，再跑一个未审查的公开 PR，确认登录凭据和缓存不是只在初次进程中有效。官方文档说明有效的已保存会话可静默登录，因此这一路径**不要求 Gemini API key**，但不保证每个容器环境都能保存或解锁凭据。

```bash
cp .env.example .env
cp config.example.json config.json
docker compose up -d --build runner
docker compose --profile operator build fetcher
docker compose exec runner agy --version
docker compose exec runner python3 /app/unlock_keyring.py
docker compose exec -e SSH_CONNECTION runner agy

# 从 agy 退出后，在 VPS 的 SSH 终端运行：
python3 review.py review ckb-devrel/ccc 348
```

公开测试可选 [ckb-devrel/ccc#348](https://github.com/ckb-devrel/ccc/pull/348)；如果它不再开放或不符合当前限制，请换成一个变更很小、还开放的公开 PR。抓取使用 GitHub API 固定 head、base 与 merge-base 读取变更的文本原件，不在 runner 中执行 Git checkout；仅接受 1–40 个普通 UTF-8 文本文件、总快照不超过 90 KB。超限、二进制、符号链接或子模块改动需要人工检查，不会悄悄截断。核对本地报告的仓库、PR 编号、完整 head SHA、base 与 merge-base；用 GitHub PR 的提交页确认 head 相符。报告保存在 `state/reports/<id>.json`。抓取中或分析开始前发现 PR 改变会停止；如果分析完成后的 head/base 已改变，报告会标为 `stale-at-completion`，而无法重新核实则标为 `unknown-at-completion`。两种情况都不要把它当成当前版本的结论。

## 凭据与数据走向

- **公开仓库默认匿名**，无需 GitHub Token。只有源仓库确实需要认证或匿名额度不足时，才单独配置仓库所有者授权的 **fine-grained PAT**：只选择目标仓库，权限为 **Contents: Read**、**Pull requests: Read**，设合理的有效期。参考 [GitHub Get a pull request 权限说明](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request)。不要使用 classic PAT、浏览器 Cookie、会话令牌或 GitHub 写权限。
- GitHub PAT 只交给 fetcher，runner 和 MCP 报告服务不应收到它。不要写进 `.env`、镜像、Git 历史或审查任务。审查 runner 则使用单独的 Antigravity 登录状态，不与 GitHub 或 MCP 共用凭据。
- 经配置允许抓取的私有代码仍可能随审查上下文传到 Google。只有你有权把相应内容交给所用 Google 账户及方案时，才能开启私有仓库；GitHub 的读取权限不能代替这份授权。
- PR 内容不是给 runner 的指令。不要给 runner 配置第三方 MCP、GitHub 凭据、Docker socket 或生产环境访问，不要挂载个人主目录，也不要开启无条件自动批准。报告按数据处理，逻辑和结论需复核。

确实需要 GitHub PAT 时，可在 VPS 上临时读取。下面的写法不把值留在 shell 命令历史；不要开启 shell 跟踪（`set -x`）：

```bash
read -rsp 'GitHub fine-grained PAT: ' GH_TOKEN; printf '\n'
GH_TOKEN="$GH_TOKEN" python3 review.py review OWNER/REPO PR_NUMBER
unset GH_TOKEN
```

## 可选：读取报告的 MCP

不集成 MCP 时可以直接查看 `state/reports/`，无需部署任何对外服务。报告服务只暴露 `list_reports` 和 `get_report`，无 GitHub、Google 会话或运行容器的权限；用单独的随机 bearer token 认证。将报告视为敏感数据：所有拿到该 bearer token 的客户端可读该实例内的所有报告，不要把不同授权范围的私有仓库共用一个报告目录和 token。

先完成一次审查以生成报告目录。首次开启报告服务时，配置 `.env` 的本机 UID/GID，再在 VPS 建立单独的密钥文件（已有密钥时不要覆盖）：

```bash
id -u
id -g
mkdir -p secrets
(umask 077; set -C; openssl rand -hex 32 > secrets/report_token)
docker compose --profile reports up -d --build reports
```

本机地址为 `http://127.0.0.1:8080/mcp`，使用支持自定义 HTTP header 的 MCP 客户端，把密钥文件里的值作为 `Authorization: Bearer …`。项目没有验证某个具体客户端的接入；不支持该认证方式时仍可直接查看本地报告。默认仅监听 VPS 回环地址。可通过 SSH 隧道在己方客户端使用；若必须给远程 MCP 客户端接入，则另行配置受控的 HTTPS 反代，保留认证并限制主机和网络访问。不要让反代记录 Authorization header，不要把 token 放在 URL 中。远程客户端如果无法添加独立的 Authorization header，就保持服务本地，不要为了接入取消认证。

## 登录、锁与重试

首次登录需要操作员在 SSH 终端参与，且应与审查串行进行。Linux 上 CLI 还要求可访问且已解锁的 Secret Service / D-Bus；单有 Docker volume 不等于能静默登录。遇到密钥环报错见 [Google CLI 故障排查](https://antigravity.google/docs/cli/troubleshooting/#authorize-keyring-permissions)。不要同时让多个任务写入同一个 CLI profile，也不要把登录目录复制到别处来绕过凭据锁。若 CLI 要求重新登录、密钥环无法解锁、报告为空或状态非 SUCCESS、账户受限或额度用完，就停止该次并由操作员排查；不要循环快速重试、轮换 Google 账户或自动提交 OAuth。若常规 SSH OAuth 在该 VPS 环境无法持久化，就不要把这条路径投入无人值守运行。Google 的 [认证文档](https://antigravity.google/docs/install?tab=cli) 也说明了另行配置 Gemini API key 的方式，但本项目默认不使用或存储它。

定时运行时，把命令放在由操作者维护的 VPS timer/cron 中，只审已允许的仓库和明确选定的 PR；不要让 MCP 触发任务。只在上一次退出后再启动下一次，给单次运行设超时；遇到配额/限速遵守服务给出的重试时间。无新 head 时优先复用旧报告，认证错误需要人工处理。先手工完成一次端到端验收，再开启调度。

## 结果怎么用

先读报告里的复核范围与限制，再逐条检查文件、行号和证据。在 GitHub 上互动前再核对 PR 的当前 head；新提交需要新的报告。不要直接复用模型给出的命令或把报告当作审批。对于尚未在真实 VPS 上使用正式 Google 会话验证的部署，不把本地单元测试、模拟 CLI 或 Docker 配置检查视为登录已验收。
