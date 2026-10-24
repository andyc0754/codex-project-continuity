# Codex Project Continuity

让 Codex 在项目中主动维护进度、交接和可恢复的本地文件版本，减少换对话后的重复解释。

这是公开分享的独立 Skill 加可选全局规则、Hooks 工具包，无需邀请即可查看和下载。本仓库尚未指定开源许可证。包内不包含作者的项目记录、聊天内容、部署回执、私人快照或账号凭证。

## 能做什么

- 开始实施任务前登记目标和修改前基线。
- 在重要阶段记录结果、验证证据、决策和下一步。
- 新对话读取短摘要与相关交接，按需查历史。
- 保存内容去重的本地快照，支持指定文件回退与恢复后续节点。
- 恢复前预览；遇到未存档或已暂存的新改动时拒绝覆盖。
- 已信任且受支持的本地 Hooks 增强自动执行；不支持的路径由智能体主动调用 helper。

只讨论或只读分析时不创建项目档案。任务完成不代表用户已认可效果；工具记录验证事实，不冒充用户确认。

## 运行条件

- Python 3.9 或更新版本，日常运行只用标准库。
- macOS 或 Linux；原生 Windows 暂不支持（使用 POSIX 文件锁）。WSL 需在 WSL 内单独安装并测试，不会自动作用于 Windows 端 Codex。
- Git 可选，无 Git 的项目也可保存文件快照。
- 全自动增强需要支持该 Hooks 协议的本地 Codex 版本及用户首次信任审阅。云端、远程和其他设备不会自动同步。

## 推荐安装：Skill + 全局规则 + Hooks

从 GitHub 的 Code 菜单下载 ZIP 并解压，或直接在终端克隆（公开仓库无需为下载配置 GitHub 认证）：

```sh
git clone https://github.com/andyc0754/codex-project-continuity.git
cd codex-project-continuity
python3 -m unittest discover -s tests -v
python3 deploy.py install
```

请先审阅源码，再执行安装。安装程序：

1. 安装 `project-continuity` 到当前 Codex home 的 `skills/`。
2. 在 `AGENTS.md` 合并带标记的规则块，保留块外内容。
3. 在 `hooks.json` 合并六个钩子，保留其他命令。
4. 保存部署前文件、安装哈希及回执；不修改 `config.toml`，不代替你信任钩子。

默认尊重 `CODEX_HOME`，未设置时用 `~/.codex`。可显式指定：

```sh
python3 deploy.py install --codex-home /path/to/codex-home
```

安装程序把本次 Python 解释器和目标目录写入 hook 命令，路径含空格也可使用。请使用长期保留的 Python，不要从临时虚拟环境安装；搬移解释器或安装位置后重新安装并审阅钩子。

安装后在 Codex 的钩子审阅界面确认本技能的六个命令，或在支持的 CLI 中输入 `/hooks` 审阅，然后新开会话。不要使用跳过信任参数。再次安装若改变定义，可能需要重新审阅。若 `AGENTS.override.md` 或项目局部指令覆盖了全局规则，须先检查实际加载内容，不能仅凭文件存在就认定规则生效。

## 只安装 Skill

也可以将 `skills/project-continuity/` 复制到自己的 Codex `skills/` 下，或让 Codex 的 Skill Installer 从本仓库的 `skills/project-continuity` 路径安装。

仅安装 Skill 不会修改全局 AGENTS，也不会安装 Hooks。可使用 `$project-continuity` 明确调用；想获得本项目约定的跨项目自动工作流，应使用上面的完整安装。不要让朋友覆盖整个 `.codex` 目录。

## 平时如何使用

正常安排工作即可，例如“继续这个项目的导航调整”。在同一项目新开对话时，智能体应读 `.codex-continuity/STATE.md`，再读相关任务记录。多个任务含义不明确时仍可能需要你指定要继续哪一个。

效果不合适时可以说“把刚才修改的标题恢复到上个检查点，保留其他改动”。恢复需明确范围，并核验依赖；不是一键撤销所有外部操作。

文件快照和摘要是两回事：压缩前钩子可保存文件，不能自动替代对目标和决策的语义交接。不会主动新建、发送或关闭对话。

## 数据和恢复范围

项目内 `.codex-continuity/` 保存摘要、分任务记录及历史修订；实际版本内容保存在 Codex home 的 `continuity/projects/` 下。没有上传服务或后台云同步，也不自动 Git commit、push、reset 或修改暂存区。

默认排除常见凭证文件、数据库、缓存、Git 忽略文件、符号/硬链接及超过 20 MiB 的文件。文件名规则不能识别所有嵌入源码的秘密；发现敏感内容时应使用专门的保护方案。单次扫描有时间、文件数和总量上限，必须查看覆盖情况，不能当作整机备份或异地灾备。

临时节点保留最近 50 个或最近 30 天；固定节点、未完成任务以及起点和交接引用保留。仅明确完成任务时清理过期的无引用内容；节点元数据仍保留。多会话有独立任务绑定，但并行修改相同文件仍需协调。

详细范围、保留策略和恢复流程见 [operations.md](skills/project-continuity/references/operations.md)。

## 卸载或撤回本次安装

保留安装程序输出的 deployment 编号。先预览，再明确应用：

```sh
python3 deploy.py rollback --deployment YOUR_DEPLOYMENT_ID
python3 deploy.py rollback --deployment YOUR_DEPLOYMENT_ID --apply
```

自定义 Codex home 时加相同的 `--codex-home`。若安装后这些文件又被改过，程序会拒绝覆盖，需逐项比较。撤下的文件、原文件备份和已有项目快照仍保留。这里只撤回指定一次部署；多次更新应按部署顺序反向撤回。

## 验证与限制

测试在临时目录内覆盖局部回退/前进、冲突拒绝、快照完整性、Git 暂存区保护、只读行为、会话隔离、保留策略，以及合并安装、幂等和回退。

原工作流曾在本机真实新对话中验证：只提供普通任务，新对话主动读取交接、绑定任务并记录结果；PreToolUse 与 Stop 有执行证据。私人验收原文不随包分享。分享版另行验证安装路径及自定义 Codex home；不把本机结果当作所有平台、模型和未来版本的保证。

这是社区工作流，不是 OpenAI 官方内置功能。全局规则是智能体指令，不能替代运行时权限控制。相关设计参考及官方 Hooks 文档见操作说明的 Provenance 部分。
