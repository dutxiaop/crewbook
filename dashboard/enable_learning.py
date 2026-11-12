import os
import tempfile
import tomllib

from dashboard_data import ROSTER
from learning import PROFILE, ROOT


HEADING = "## 看板学习回执"


def instrumentation(role):
    logger = (ROOT / "learning.py").as_posix()
    return (
        f"{HEADING}\n\n"
        "对当前任务的临时纠正，先执行纠正并在本任务回报。用户明确要求以后沿用时，在改个人岗位文件之前记录 accepted。"
        "如果你从证据矛盾或重复偏差中主动发现可迁移的规则，先记录 autonomous 的 proposed，写明观察、拟议规则和反例，保持用户对长期规则的现有确认边界。"
        "用户采用该提议后用同一个编号记录 accepted。若搁置则记录 rejected。\n\n"
        f'记录命令为 `python -B -X utf8 "{logger}" --role {role} --id <本次唯一编号> --stage <阶段> --origin <user或autonomous> --title <简短标题> --note <本阶段事实>`。'
        "阶段沿用同一个编号和来源。岗位配置实际改动且版本递增后才记录 applied，并提供 --version。"
        "独立回放实际完成后才记录 verified，提供 --version 和 --evidence（实际证据文件或 Codex 会话链接）。"
        "简要记录适用条件、边界和结果，避免引用敏感原文。需要明确原任务时传 --task。"
        "回报时注明编号、版本及已完成的阶段，任何未成功写入或未复验的阶段按事实报告。\n"
    )


def replace(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name, suffix=".tmp", delete=False) as file:
        file.write(data)
        name = file.name
    try:
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    changed = []
    plan = {}
    for role, *_ in ROSTER:
        path = PROFILE / "agents" / f"{role}.toml"
        original = path.read_bytes()
        text = original.decode("utf-8")
        old = tomllib.loads(text)
        if HEADING in old["developer_instructions"]:
            if (ROOT / "learning.py").as_posix() in old["developer_instructions"]:
                continue
            raise ValueError(f"{role} 已有不同的看板路径")
        newline = "\r\n" if "\r\n" in text else "\n"
        anchor = '\n"""\n\n[features]'.replace("\n", newline)
        if text.count(anchor) != 1:
            raise ValueError(f"{role} 的岗位配置格式需要手动检查")
        extra = ("\n\n" + instrumentation(role)).replace("\n", newline)
        result = text.replace(anchor, extra + anchor, 1)
        check = tomllib.loads(result)
        assert check["name"] == role
        assert check["developer_instructions"].startswith(old["developer_instructions"].rstrip())
        assert {k: v for k, v in check.items() if k != "developer_instructions"} == {k: v for k, v in old.items() if k != "developer_instructions"}
        plan[path] = (original, result.encode("utf-8"))
    try:
        for path, (original, updated) in plan.items():
            if path.read_bytes() != original:
                raise ValueError(f"{path.stem} 已有同时修改")
            replace(path, updated)
            changed.append(path)
            if path.read_bytes() != updated:
                raise OSError(f"{path.stem} 更新校验失败")
    except (OSError, ValueError):
        for path in reversed(changed):
            original, updated = plan[path]
            if path.read_bytes() == updated:
                replace(path, original)
        raise
    print("已接入学习回执：" + "、".join(path.stem for path in changed) if changed else "八位员工已接入学习回执")


if __name__ == "__main__":
    main()
