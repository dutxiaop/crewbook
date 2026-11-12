import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path


AGENTS = Path(__file__).resolve().parent / "agents"
METHODS_SUFFIX = ".methods.json"
ROLE_ID = re.compile(r"[a-z0-9][a-z0-9_-]*")
VERSION = r"[0-9]+(?:\.[0-9]+){1,3}"


def check_role(role):
    config_path = AGENTS / f"{role}.toml"
    methods_path = AGENTS / f"{role}{METHODS_SUFFIX}"
    config = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    methods = json.loads(methods_path.read_text(encoding="utf-8-sig"))
    if config.get("name") != role:
        raise ValueError(f"{config_path.name} 的 name 应为 {role}")
    if not isinstance(config.get("description"), str) or not config["description"].strip():
        raise ValueError(f"{config_path.name} 缺少 description")

    instructions = config.get("developer_instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError(f"{config_path.name} 缺少 developer_instructions")
    versions = set(re.findall(rf"\b{re.escape(role)}/({VERSION})(?![\w.])", instructions))
    if len(versions) != 1:
        raise ValueError(f"{config_path.name} 应声明一个明确的 {role}/版本号")
    version = versions.pop()

    if not isinstance(methods, dict) or methods.get("agent") != role:
        raise ValueError(f"{methods_path.name} 的 agent 应为 {role}")
    if methods.get("version") != version:
        raise ValueError(f"{methods_path.name} 的 version 应与 TOML 一致（{version}）")
    if not isinstance(methods.get("rules"), list):
        raise ValueError(f"{methods_path.name} 缺少 rules 数组")
    return version


def main():
    parser = argparse.ArgumentParser(description="校验数字员工配置及岗位版本")
    parser.add_argument("--live", action="store_true", help="同时检查 Codex 配置入口是否指向当前仓库")
    args = parser.parse_args()

    configs = {path.stem for path in AGENTS.glob("*.toml")}
    methods = {path.name.removesuffix(METHODS_SUFFIX) for path in AGENTS.glob(f"*{METHODS_SUFFIX}")}
    if not configs and not methods:
        print(f"找不到岗位配置：{AGENTS}", file=sys.stderr)
        return 1

    errors = []
    for role in sorted(configs | methods):
        try:
            if not ROLE_ID.fullmatch(role):
                raise ValueError(f"岗位 ID 无效：{role}")
            if role not in configs or role not in methods:
                missing = ".toml" if role not in configs else METHODS_SUFFIX
                raise ValueError(f"缺少配对文件：{role}{missing}")
            print(f"{role}/{check_role(role)}")
        except (OSError, ValueError) as exc:
            errors.append(f"{role}：{exc}")

    if args.live:
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        live = codex_home / "agents"
        if live.resolve() == AGENTS.resolve():
            print(f"Codex 配置入口：{live} → {AGENTS}")
        else:
            errors.append(f"Codex 配置入口 {live} 未指向 {AGENTS}")

    for error in errors:
        print(error, file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
