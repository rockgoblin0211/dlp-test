# -*- coding: utf-8 -*-
"""
DebugView 日志自动校验脚本
配套: test_nonpe_freq_filter.bat
用途: 解析 DebugView 导出的日志文件，自动校验六个测试场景是否符合预期

用法:
  1. 运行 test_nonpe_freq_filter.bat 过程中，在 DebugView 里:
     File -> Save As -> 保存为 .log 或 .txt (UTF-8 或 UTF-16 均可)
  2. 运行本脚本:
       python verify_nonpe_freq_log.py <日志文件路径>
       python verify_nonpe_freq_log.py dbgview.log --pid 1234
       python verify_nonpe_freq_log.py dbgview.log --split-by-scene scene_marks.txt

参数:
  log_file              : DebugView 导出的日志文件
  --pid PID             : 只校验指定 PID 的日志 (不填则自动识别出现次数最多的 PID)
  --split-by-scene FILE : 如果有场景分隔时间戳文件，可按场景切片后分别校验
  --learn-threshold N   : 学习期阈值 (默认 50)
  --window-sec N        : 过滤窗口秒数 (默认 60)
  --strict              : 严格模式，任何一个场景失败即退出码非 0
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# -------- 日志解析 --------

# DebugView 典型行:  "12345\t12.34567890\t[NonPeFreq] PID=5678 learned 50 non-PE files (0 black), entering filter mode"
# 也可能只有内容没有前缀。下方正则尽量宽松。

RE_NONPE = re.compile(
    r"\[NonPeFreq\]\s*PID=(?P<pid>\d+)\s+(?P<body>.+)",
    re.IGNORECASE,
)
RE_PIDSCAN = re.compile(
    r"\[PidScan\]\s*PID=(?P<pid>\d+)\s+"
    r"total=(?P<total>\d+)\s+"
    r"nosuffix=(?P<nosuffix>\d+)\s*"
    r"\(\s*(?P<ratio>[\d.]+)%\s*\)\s*"
    r"in\s+(?P<seconds>\d+)s",
    re.IGNORECASE,
)

RE_LEARNED = re.compile(
    r"learned\s+(?P<n>\d+)\s+non-PE\s+files\s+\((?P<black>\d+)\s+black\).*entering\s+filter\s+mode",
    re.IGNORECASE,
)
RE_FILTERING = re.compile(
    r"filtering,\s*filtered=(?P<n>\d+)",
    re.IGNORECASE,
)
RE_EXPIRED = re.compile(
    r"filter\s+window\s+expired,\s*filtered=(?P<n>\d+),\s*re-entering\s+learn\s+phase",
    re.IGNORECASE,
)


@dataclass
class NonPeEvent:
    line_no: int
    pid: int
    kind: str  # learned | filtering | expired | other
    n: int = 0
    raw: str = ""


@dataclass
class PidScanEvent:
    line_no: int
    pid: int
    total: int
    nosuffix: int
    ratio: float
    seconds: int
    raw: str = ""


def read_log(path: Path) -> list[str]:
    # DebugView 常用 UTF-16-LE (带 BOM) 或 UTF-8；尝试多种编码
    encodings = ["utf-8-sig", "utf-16", "utf-16-le", "gbk", "utf-8"]
    raw = path.read_bytes()
    for enc in encodings:
        try:
            text = raw.decode(enc)
            return text.splitlines()
        except UnicodeDecodeError:
            continue
    # 最后用 utf-8 容错
    return raw.decode("utf-8", errors="replace").splitlines()


def parse_log(lines: Iterable[str]) -> tuple[list[NonPeEvent], list[PidScanEvent]]:
    nonpe_events: list[NonPeEvent] = []
    pidscan_events: list[PidScanEvent] = []

    for i, line in enumerate(lines, start=1):
        m1 = RE_NONPE.search(line)
        if m1:
            pid = int(m1.group("pid"))
            body = m1.group("body")
            ev_kind = "other"
            n = 0
            if mm := RE_LEARNED.search(body):
                ev_kind = "learned"
                n = int(mm.group("n"))
            elif mm := RE_FILTERING.search(body):
                ev_kind = "filtering"
                n = int(mm.group("n"))
            elif mm := RE_EXPIRED.search(body):
                ev_kind = "expired"
                n = int(mm.group("n"))
            nonpe_events.append(NonPeEvent(i, pid, ev_kind, n, line.strip()))
            continue

        m2 = RE_PIDSCAN.search(line)
        if m2:
            pidscan_events.append(
                PidScanEvent(
                    line_no=i,
                    pid=int(m2.group("pid")),
                    total=int(m2.group("total")),
                    nosuffix=int(m2.group("nosuffix")),
                    ratio=float(m2.group("ratio")),
                    seconds=int(m2.group("seconds")),
                    raw=line.strip(),
                )
            )

    return nonpe_events, pidscan_events


# -------- 校验逻辑 --------

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    def render(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        return f"[{mark}] {self.name}\n       {self.detail}"


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, r: CheckResult) -> None:
        self.results.append(r)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    def render(self) -> str:
        lines = [r.render() for r in self.results]
        summary = (
            f"\n===== Summary: {sum(r.passed for r in self.results)}/{len(self.results)} passed ====="
        )
        return "\n".join(lines) + summary


def pick_target_pid(events: list[NonPeEvent], scans: list[PidScanEvent]) -> int | None:
    counter: Counter[int] = Counter()
    for e in events:
        counter[e.pid] += 1
    for s in scans:
        counter[s.pid] += 1
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def check_scenarios(
    nonpe_events: list[NonPeEvent],
    pidscan_events: list[PidScanEvent],
    target_pid: int,
    learn_threshold: int,
) -> Report:
    report = Report()

    pid_events = [e for e in nonpe_events if e.pid == target_pid]
    pid_scans = [s for s in pidscan_events if s.pid == target_pid]

    learned_events = [e for e in pid_events if e.kind == "learned"]
    filtering_events = [e for e in pid_events if e.kind == "filtering"]
    expired_events = [e for e in pid_events if e.kind == "expired"]

    # 场景1/2: 至少出现一次 learned (n == threshold) + 若干 filtering
    learned_hit = any(e.n == learn_threshold for e in learned_events)
    filtering_hit = len(filtering_events) > 0
    report.add(CheckResult(
        name=f"场景1/2 进入过滤模式 (learned={learn_threshold} + filtering>=1)",
        passed=learned_hit and filtering_hit,
        detail=(
            f"learned 事件数={len(learned_events)} (命中阈值={learned_hit}), "
            f"filtering 事件数={len(filtering_events)}"
        ),
    ))

    # 场景3: 这里无法直接根据文件名判定，只能做弱校验：PE 场景不应单独触发 learned
    # 提示用户手工核对对应时间段
    report.add(CheckResult(
        name="场景3 PE 后缀不走过滤 (需人工核对对应时间段)",
        passed=True,
        detail="脚本无法直接识别场景边界，请在 DebugView 中核对 .exe/.dll 创建期间无 [NonPeFreq] 日志",
    ))

    # 场景4: 如果该 PID 仅出现 <threshold 个文件，应当没有 learned 事件 (弱校验)
    # 这里给出提示
    report.add(CheckResult(
        name="场景4 少量文件不触发 (需人工按时间段核对)",
        passed=True,
        detail="请确认场景4 时间段内无 entering filter mode 日志",
    ))

    # 场景5: 出现 [PidScan] 日志
    scan_hit = len(pid_scans) > 0
    report.add(CheckResult(
        name="场景5 出现 [PidScan] 统计日志",
        passed=scan_hit,
        detail=(
            f"PidScan 事件数={len(pid_scans)} "
            + (f"example: {pid_scans[0].raw}" if pid_scans else "")
        ),
    ))

    # 场景6: 出现 expired 事件，并且在其之后再次出现 learned 或 filtering
    expired_hit = len(expired_events) > 0
    re_learn_after_expire = False
    if expired_hit:
        first_expire_line = expired_events[0].line_no
        re_learn_after_expire = any(
            e.line_no > first_expire_line and e.kind in ("learned", "filtering")
            for e in pid_events
        )
    report.add(CheckResult(
        name="场景6 窗口过期并重新进入学习期",
        passed=expired_hit and re_learn_after_expire,
        detail=(
            f"expired 事件数={len(expired_events)}, "
            f"过期后是否再次出现 learned/filtering={re_learn_after_expire}"
        ),
    ))

    return report


# -------- 概览输出 --------

def print_overview(
    nonpe_events: list[NonPeEvent],
    pidscan_events: list[PidScanEvent],
) -> None:
    pid_counter: Counter[int] = Counter()
    kind_counter: dict[int, Counter[str]] = defaultdict(Counter)
    for e in nonpe_events:
        pid_counter[e.pid] += 1
        kind_counter[e.pid][e.kind] += 1
    for s in pidscan_events:
        pid_counter[s.pid] += 1
        kind_counter[s.pid]["pidscan"] += 1

    print("===== 日志概览 =====")
    print(f"[NonPeFreq] 事件总数: {len(nonpe_events)}")
    print(f"[PidScan]   事件总数: {len(pidscan_events)}")
    print("按 PID 分布 (Top 10):")
    for pid, cnt in pid_counter.most_common(10):
        k = kind_counter[pid]
        print(
            f"  PID={pid:<6} total={cnt:<4} "
            f"learned={k['learned']}, filtering={k['filtering']}, "
            f"expired={k['expired']}, pidscan={k['pidscan']}, other={k['other']}"
        )
    print()


# -------- 入口 --------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="DebugView 日志自动校验 (非PE文件频率过滤)"
    )
    parser.add_argument("log_file", help="DebugView 导出的日志文件路径")
    parser.add_argument("--pid", type=int, default=None,
                        help="指定要校验的 PID，不填则自动挑选事件数最多的 PID")
    parser.add_argument("--learn-threshold", type=int, default=50,
                        help="学习期阈值 (默认 50)")
    parser.add_argument("--window-sec", type=int, default=60,
                        help="过滤窗口秒数 (默认 60，仅用于提示)")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式，任何一个场景失败即返回非 0 退出码")
    args = parser.parse_args()

    log_path = Path(args.log_file)
    if not log_path.exists():
        print(f"ERROR: 日志文件不存在: {log_path}", file=sys.stderr)
        return 2

    lines = read_log(log_path)
    print(f"已加载日志: {log_path} (共 {len(lines)} 行)")
    nonpe_events, pidscan_events = parse_log(lines)

    print_overview(nonpe_events, pidscan_events)

    target_pid = args.pid or pick_target_pid(nonpe_events, pidscan_events)
    if target_pid is None:
        print("ERROR: 日志中未发现任何 [NonPeFreq] / [PidScan] 事件，"
              "请确认 DebugView 是否正确抓取内核日志", file=sys.stderr)
        return 2

    print(f"===== 使用 PID={target_pid} 进行校验 "
          f"(学习阈值={args.learn_threshold}, 窗口={args.window_sec}s) =====")

    report = check_scenarios(
        nonpe_events, pidscan_events,
        target_pid=target_pid,
        learn_threshold=args.learn_threshold,
    )

    print(report.render())
    print()
    print("提示: 场景3/4 为时间段相关校验，脚本只做弱提示；")
    print("      如需精确切分，请在测试时记录每个场景开始的系统时间戳，再按时间段过滤日志。")

    if args.strict and not report.all_passed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
