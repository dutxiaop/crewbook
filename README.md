# Crewbook

Crewbook 是一套 Codex 自定义岗位配置，覆盖产品定义、交互设计、架构、开发集成、技术诊断、独立验收、构建发布和交付统筹。

## 使用

将需要的 `agents/*.toml` 放到 `~/.codex/agents/`，或放到目标项目的 `.codex/agents/`。在 Codex 中用岗位 ID 指定角色，例如「请 `independent_acceptance` 复核这个候选版本」。交付负责人 `delivery_lead` 可以协调其余岗位。 [Codex 自定义岗位文档](https://learn.chatgpt.com/docs/agent-configuration/subagents)

同名 `*.methods.json` 记录岗位的工作方法，便于查看和维护。

## 校验

在仓库根目录用 Python 3.11 或更新版本运行 `python -X utf8 check.py`，校验 TOML 和方法 JSON 的配对与版本。

## 开源协议

[MIT License](LICENSE)
