# -*- coding: utf-8 -*-
"""校验辩论备战包是否达到交付红线。

用法：
    python scripts/check_pack.py runs/20261107-辩题/备战包.md
    python scripts/check_pack.py runs/20261107-辩题/一辩稿.md --partial
    python scripts/check_pack.py 备战包.md --json

退出码：0 通过（可有提醒），1 有失败项，2 文件读不到。

硬红线之外，另查五类风格问题：AI 套话、句子节奏、段落均长、人称、具体度。
只有硬禁套话记为 FAIL，其余风格项都是 WARN，只提醒、不改硬红线。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_CHAPTERS = [
    ("定义与背景", ("定义与背景",)),
    ("判标", ("判标", "判准")),
    ("论点", ("论点",)),
    ("攻防", ("攻防",)),
    ("环节稿", ("环节稿", "SOP")),
    ("红队自检", ("红队",)),
    ("核实清单", ("核实",)),
]

ORDINALS = re.compile(r"第[一二三四五六七八九十]+[，,、]|第一刀|第二刀")
OPPONENT = re.compile(r"对方可能会说|即便对方")
TAG = re.compile(r"【(数据类|机理类|案例类)】")
TARGET = re.compile(r"目标\s*(\d+)\s*字")
COLLOQUIAL = re.compile(r"所以你看|说白了|那我问你|反过来|你想想|说到底|你看")

# 与 SKILL.md 第五节、references/14-人味与去AI味.md 第八节逐字一致。
AI_BLACKLIST = (
    "总而言之", "由此可见", "众所周知", "不难发现",
    "值得注意的是", "毋庸置疑", "不可否认", "赋能", "闭环",
    "范式", "底层逻辑", "颗粒度", "抓手",
)
AI_SOFT = (
    "这意味着", "也就是说", "换句话说", "某种程度上", "某种意义上",
    "进一步地", "系统性地", "多维度", "本质上",
)


def read_lines(path):
    return Path(path).read_text(encoding="utf-8-sig").splitlines()


def heading(line):
    m = re.match(r"^(#{1,6})\s+(.*)$", line)
    return (len(m.group(1)), m.group(2).strip()) if m else None


def chapter(lines, keywords):
    """返回第一个匹配的二级标题范围 [start, end)。"""
    for i, line in enumerate(lines):
        h = heading(line)
        if h and h[0] == 2 and any(k in h[1] for k in keywords):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                h2 = heading(lines[j])
                if h2 and h2[0] <= 2:
                    end = j
                    break
            return i, end, h[1]
    return None, None, None


def subchapter(lines, start, end, keywords):
    """返回某个二级章节内第一个匹配的三级标题范围 [start, end)。"""
    for i in range(start + 1, end):
        h = heading(lines[i])
        if h and h[0] == 3 and any(k in h[1] for k in keywords):
            stop = end
            for j in range(i + 1, end):
                h2 = heading(lines[j])
                if h2 and h2[0] <= 3:
                    stop = j
                    break
            return i, stop, h[1]
    return None, None, None


def count_chars(lines):
    total = 0
    for line in lines:
        text = line.strip()
        if not text or text.startswith(">") or text.startswith("<!--"):
            continue
        total += len(re.sub(r"\s+", "", text))
    return total


def sentences_of(lines, start, end):
    """取一节的整句集合，只收十二字以上的句子。"""
    text = "\n".join(lines[start + 1:end])
    out = set()
    for part in re.split(r"[。！？；]", text):
        part = re.sub(r"\s+", "", part)
        if len(part) >= 12:
            out.add(part)
    return out


def scan_section_rules(lines, start, end, title, add, partial=False):
    """逐节检查首质、盘问、申论、小结、结辩的成稿红线。"""
    body = []
    for idx in range(start + 1, end):
        text = lines[idx].strip()
        if not text or text.startswith(">") or text.startswith("<!--") or text.startswith("#"):
            continue
        body.append((idx, text))
    if not body:
        return
    joined = "\n".join(t for _, t in body)
    total = len(re.sub(r"\s+", "", joined))

    is_first = "首质" in title
    is_pan = "盘问" in title and "小结" not in title

    if is_first or is_pan:
        limit_warn, limit_fail = (20, 30) if is_first else (25, 35)
        for idx, text in body:
            for seg in re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?", text):
                if not seg.endswith(("？", "?")):
                    continue
                q = re.sub(r"^[\s\-*·]*(?:问|追问|反问)?[：:]\s*", "", seg)
                q = q.rstrip("？?").strip()
                q = re.sub(r"[\s，,、—…“”\"'（）()]", "", q)
                if not re.search(r"[\u4e00-\u9fff]", q):
                    continue
                if len(q) > limit_fail:
                    add("FAIL", "问句",
                        f"「{title}」一问 {len(q)} 字，超过 {limit_fail} 字：{q[:16]}…", idx + 1)
                elif len(q) > limit_warn:
                    add("WARN", "问句",
                        f"「{title}」一问 {len(q)} 字，超过 {limit_warn} 字：{q[:16]}…", idx + 1)
        if "→" in joined:
            add("FAIL", "箭头", f"「{title}」出现「→」，箭头是提纲，不是成稿", start + 1)
        if total < 450:
            add("WARN", "太短",
                f"「{title}」只有 {total} 字，像提纲；首质/盘问至少写够 500 字", start + 1)
        marks = joined.count("？") + joined.count("?")
        if marks < 3:
            add("WARN", "问句", f"「{title}」只有 {marks} 个问句，不是质询", start + 1)
        if not re.search(r"[你您]", joined):
            add("WARN", "人称", f"「{title}」没有第二人称，像论文", start + 1)
    elif any(k in title for k in ("申论", "小结", "结辩")):
        if total >= 400 and not COLLOQUIAL.search(joined):
            add("WARN", "口语", f"「{title}」没有口语连接，像论文/播音", start + 1)
        parts = [re.sub(r"[\s，,、—…“”\"'（）()]", "", p)
                 for p in re.split(r"[。！？；!?;\n]", joined)]
        parts = [p for p in parts if p and re.search(r"[\u4e00-\u9fff]", p)]
        if len(parts) >= 10:
            ratio = sum(1 for p in parts if len(p) >= 50) / len(parts)
            if ratio >= 0.5:
                add("WARN", "句式", f"「{title}」长句占 {ratio:.0%}，像论文/播音；拆短句", start + 1)

    if any(k in title for k in ("首质", "盘问", "申论", "小结", "结辩")):
        concrete = len(re.findall(r"\d+(?:\.\d+)?%?", joined))
        concrete += len(re.findall(r"[“「『][^”」』]{2,60}[”」』]", joined))
        if total >= 200 and concrete < 3 and (partial or total < 600):
            add("WARN", "具体度", f"「{title}」只有 {concrete} 处具体数字或原话，至少补到 3 处", start + 1)

    if any(k in title for k in ("首质", "盘问", "申论", "小结", "结辩")):
        paras = [re.sub(r"^[-*·]\s*", "", t) for _, t in body]
        paras = [re.sub(r"[*_`#>\-—…“”\"'（）()\s]", "", p) for p in paras]
        paras = [p for p in paras if len(p) >= 20]
        if len(paras) >= 4:
            heads = [p[:6] for p in paras]
            run = 1
            max_run = 1
            for a, b in zip(heads, heads[1:]):
                run = run + 1 if a == b else 1
                max_run = max(max_run, run)
            if max_run >= 4:
                add("WARN", "段落", f"「{title}」连续 {max_run} 段同一句式开头，改掉", start + 1)


def check(path):
    lines = read_lines(path)
    issues = []

    def add(level, code, message, line_no=None):
        issues.append({"level": level, "code": code,
                       "message": message, "line": line_no})

    # 1. 章节齐全
    found = {}
    for name, keys in REQUIRED_CHAPTERS:
        start, end, title = chapter(lines, keys)
        found[name] = (start, end, title)
        if start is None:
            add("FAIL", "章节", f"缺少「{name}」一章")
        elif count_chars(lines[start + 1:end]) < 80:
            add("FAIL", "章节", f"「{name}」一章内容过少", start + 1)

    # 2. 总览与表格
    for i, line in enumerate(lines):
        h = heading(line)
        if h and h[0] == 2 and "总览" in h[1]:
            add("FAIL", "总览", "不要写总览章，直接从定义与背景开始", i + 1)
        if line.strip().startswith("|"):
            add("FAIL", "表格", "正文出现表格行", i + 1)

    # 3. 序号词只在环节稿的一辩稿里允许
    stage = found.get("环节稿") or (None, None, None)
    first = (None, None, None)
    if stage[0] is not None:
        first = subchapter(lines, stage[0], stage[1], ("一辩稿",))
    allowed = range(first[0], first[1]) if first[0] is not None else range(0)
    header_end = len(lines)
    for i, line in enumerate(lines):
        h = heading(line)
        if h and h[0] == 2:
            header_end = i
            break
    for i, line in enumerate(lines):
        if i in allowed or i < header_end or line.lstrip().startswith(">"):
            continue
        if ORDINALS.search(line):
            add("FAIL", "序号", "除一辩稿外出现「第一、第二」或「第一刀、第二刀」", i + 1)

    # 3b. 小结、结辩与一辩稿整句重复
    if first[0] is not None:
        one = sentences_of(lines, first[0], first[1])
        for keys in (("小结",), ("结辩",)):
            sec = subchapter(lines, stage[0], stage[1], keys)
            if sec[0] is None:
                continue
            dup = one & sentences_of(lines, sec[0], sec[1])
            for s in sorted(dup):
                if any(k in s for k in ("判准", "证成", "尺子", "如果论证到")):
                    continue
                add("FAIL", "重复", f"「{sec[2]}」与一辩稿整句重复：{s[:30]}…", sec[0] + 1)

    # 4. 预设攻防只能在攻防与红队两章
    allowed_ranges = []
    for name in ("攻防", "红队自检"):
        item = found.get(name)
        if item and item[0] is not None:
            allowed_ranges.append(range(item[0], item[1]))
    for i, line in enumerate(lines):
        if i < header_end or line.lstrip().startswith(">"):
            continue
        if any(i in r for r in allowed_ranges):
            continue
        if OPPONENT.search(line):
            add("FAIL", "攻防越界", "「对方可能会说」「即便对方」只能出现在攻防或红队章", i + 1)

    # 5. 攻防与环节稿齐全；环节稿不套固定公式、不写交接话术
    atk = found.get("攻防")
    if atk and atk[0] is not None:
        for name, keys in (("对方最强版本", ("最强版本",)),
                           ("攻防矩阵", ("攻防矩阵",)),
                           ("自由辩弹药", ("自由辩",))):
            start, end, title = subchapter(lines, atk[0], atk[1], keys)
            if start is None:
                add("FAIL", "攻防", f"攻防章缺少「{name}」")

    if stage[0] is not None:
        required_stages = [
            ("一辩稿", ("一辩稿",)),
            ("接质口径", ("接质",)),
            ("首质", ("首质",)),
            ("申论", ("申论",)),
            ("盘问与后续质询", ("盘问与", "盘问（")),
            ("盘问小结", ("小结",)),
            ("结辩", ("结辩",)),
        ]
        for name, keys in required_stages:
            start, end, title = subchapter(lines, stage[0], stage[1], keys)
            if start is None:
                add("FAIL", "环节稿", f"缺少「{name}」这一篇")
                continue
            # 组数、根问、刀法、必打都不再按固定格式检查；结构由内容决定。

        # 5b. 环节稿不能出现备赛表格标签
        formula_label = re.compile(
            r"打什么[：:]|刀法[：:]|本组收束[：:]|固定稿[：:]|积木稿[：:]|积木\s*[0-9一二三四五六七八九十]+｜"
        )
        for i in range(stage[0] + 1, stage[1]):
            if formula_label.search(lines[i]):
                add("WARN", "公式", "环节稿出现备赛表格标签，改成能直接念出的话", i + 1)

        # 5c. 环节稿不能出现交接话术
        handoff = re.compile(
            r"我方一辩(说过|讲过|已经|曾|所言|提到|指出|论证过)|"
            r"正如(我方)?一辩(所说|所言)|"
            r"前面(的)?一辩|刚才(的)?一辩|"
            r"剩下的账|我方[二三四]辩来(算|说|讲)|"
            r"(交给|有请|请听|轮到)(我方)?[二三四]辩|"
            r"下面(由|请|交给|有请)(我方)?[二三四]辩|"
            r"我方[二三四]辩(已经|刚才)(证明|说|讲|论证)"
        )
        for i in range(stage[0] + 1, stage[1]):
            if handoff.search(lines[i]):
                add("FAIL", "交接话术", "环节稿出现环节交接话术，改成直接引内容", i + 1)

    # 5d. 申论不复述一辩稿；小结按申论写，不写问答流水账
    if stage[0] is not None:
        first = subchapter(lines, stage[0], stage[1], ("一辩稿",))
        speech2 = subchapter(lines, stage[0], stage[1], ("申论",))
        if first[0] is not None and speech2[0] is not None:
            def sentences(start, end):
                text = "\n".join(lines[start + 1:end])
                parts = re.split(r"[。！？；]", text)
                out = set()
                for part in parts:
                    part = re.sub(r"\s+", "", part)
                    if len(part) >= 12:
                        out.add(part)
                return out

            one = sentences(first[0], first[1])
            two = sentences(speech2[0], speech2[1])
            for sentence in sorted(one & two):
                if any(k in sentence for k in ("判准", "证成", "尺子", "如果论证到")):
                    continue
                add("FAIL", "重复", f"申论与一辩稿重复：{sentence[:30]}…")
            for i in range(speech2[0] + 1, speech2[1]):
                if re.search(r"刚才一辩|前面一辩|一辩已经讲", lines[i]):
                    add("WARN", "重复", "申论在复述一辩，改成直接给判断", i + 1)

        summary = subchapter(lines, stage[0], stage[1], ("小结",))
        if summary[0] is not None:
            bad = re.compile(
                r"这一轮盘问|这一轮质询|我方问|我问了|对方答了|"
                r"第[一二三四五六七八九十]+组|对方认了[两三四五]?件|对方躲了[一两]?个"
            )
            for i in range(summary[0] + 1, summary[1]):
                if bad.search(lines[i]):
                    add("FAIL", "小结", "盘问小结写成了问答流水账，改成按申论推证", i + 1)

    # 6. 各环节字数与逐篇红线
    if stage[0] is not None:
        for i in range(stage[0] + 1, stage[1]):
            h = heading(lines[i])
            if not h or h[0] != 3:
                continue
            stop = stage[1]
            for j in range(i + 1, stage[1]):
                h2 = heading(lines[j])
                if h2 and h2[0] <= 3:
                    stop = j
                    break
            scan_section_rules(lines, i, stop, h[1], add)
            m = TARGET.search(h[1])
            if not m:
                if any(k in h[1] for k in ("一辩稿", "申论", "小结", "结辩", "首质", "盘问", "驳论")):
                    add("WARN", "字数", f"「{h[1]}」没标目标字数", i + 1)
                continue
            target = int(m.group(1))
            actual = count_chars(lines[i + 1:stop])
            ratio = actual / target if target else 0
            if ratio < 0.92 or ratio > 1.08:
                add("FAIL", "字数",
                    f"「{h[1]}」目标 {target} 字，实际 {actual} 字（{ratio:.0%}）", i + 1)

    # 7. 论据标注与待核实
    counts = {k: 0 for k in ("数据类", "机理类", "案例类")}
    for line in lines:
        for m in TAG.finditer(line):
            counts[m.group(1)] += 1
    arg = found.get("论点")
    if arg and arg[0] is not None:
        local = {k: 0 for k in ("数据类", "机理类", "案例类")}
        for line in lines[arg[0] + 1:arg[1]]:
            for m in TAG.finditer(line):
                local[m.group(1)] += 1
        for key, value in local.items():
            if value == 0:
                add("FAIL", "论据", f"论点章缺少【{key}】标注")
    for key, value in counts.items():
        if value == 0:
            add("FAIL", "论据", f"全篇没有【{key}】标注")
    if sum(counts.values()) < 9:
        add("WARN", "论据", f"三类标注共 {sum(counts.values())} 条，偏少")
    for i, line in enumerate(lines):
        if "待核实" in line and not re.search(r"待核实（缺[：:]", line):
            add("WARN", "核实", "「待核实」后面没写缺什么", i + 1)

    # 8. 判准可操作
    crit = found.get("判标")
    if crit and crit[0] is not None:
        body = "\n".join(lines[crit[0] + 1:crit[1]])
        if "如果" not in body or not ("证成" in body or "我方赢" in body):
            add("WARN", "判准", "判准没写成「如果论证到 X，就说明我方证成」")

    # 9. 环节稿的风格与 AI 味
    if stage[0] is not None:
        for i in range(stage[0] + 1, stage[1]):
            h = heading(lines[i])
            if not h or h[0] != 3:
                continue
            stop = stage[1]
            for j in range(i + 1, stage[1]):
                h2 = heading(lines[j])
                if h2 and h2[0] <= 3:
                    stop = j
                    break

            body = []
            for idx in range(i + 1, stop):
                text = lines[idx].strip()
                if not text or text.startswith(">") or text.startswith("<!--"):
                    continue
                body.append((idx, text))
            if not body:
                continue
            joined = "\n".join(t for _, t in body)
            total = len(re.sub(r"\s+", "", joined))

            # 9a 硬禁套话：出现即 FAIL
            for idx, text in body:
                for term in AI_BLACKLIST:
                    if term in text:
                        add("FAIL", "AI套话",
                            f"「{h[1]}」出现 AI 套话「{term}」，换成具体的人话", idx + 1)

            # 9b 慎用词密度：单篇四次以上提醒
            soft_count = sum(joined.count(term) for term in AI_SOFT)
            if soft_count >= 4:
                add("WARN", "AI套话",
                    f"「{h[1]}」慎用词出现 {soft_count} 次，删到三次以内", i + 1)

            # 9c 句子节奏：中句占比过高、短句几乎为零，就是播报腔
            parts = re.split(r"[。！？；!?;\n]", joined)
            parts = [re.sub(r"[\s，,、—…“”\"'（）()]", "", p) for p in parts]
            parts = [p for p in parts if p and re.search(r"[\u4e00-\u9fff]", p)]
            if len(parts) >= 12:
                mid = sum(1 for p in parts if 16 <= len(p) <= 32) / len(parts)
                short = sum(1 for p in parts if len(p) <= 10) / len(parts)
                if mid >= 0.7 and short < 0.08:
                    add("WARN", "句式",
                        f"「{h[1]}」句子太齐，像播报；把长句拆短，每五句留一句十字以内", i + 1)

            # 9d 段落均长：四段以上且长度标准差过小，结构像模板
            # 首质每块规定 100–150 字，块长天然接近，跳过这一项，避免误报。
            paras = []
            for _, text in body:
                if text.startswith("#"):
                    continue
                p = re.sub(r"\s+", "", re.sub(r"^[-*·]\s*", "", text))
                if p:
                    paras.append(p)
            if len(paras) >= 4 and "首质" not in h[1]:
                lens = [len(p) for p in paras]
                mean = sum(lens) / len(lens)
                if mean > 0:
                    std = (sum((x - mean) ** 2 for x in lens) / len(lens)) ** 0.5
                    if std / mean < 0.25:
                        add("WARN", "段落",
                            f"「{h[1]}」段落等长，结构像模板；让长短段错开", i + 1)

            # 9e 人称缺失：通篇没有我/你/您/咱
            if total >= 200 and not re.search(r"[我你您咱]", joined):
                add("WARN", "人称",
                    f"「{h[1]}」通篇没有「我/你/您/咱」，不像人在说话", i + 1)

            # 9f 具体度：数字与原话太少
            if total >= 600:
                concrete = len(re.findall(r"\d+(?:\.\d+)?%?", joined))
                concrete += len(re.findall(r"[“「『][^”」』]{2,60}[”」』]", joined))
                if concrete < 3:
                    add("WARN", "具体度",
                        f"「{h[1]}」只有 {concrete} 处具体数字或原话，至少补到三处", i + 1)

            # 9g 申论、小结、结辩整篇没有一个问句
            if (total >= 600 and any(k in h[1] for k in ("申论", "小结", "结辩"))
                    and not re.search(r"[？?]", joined)):
                add("WARN", "对话",
                    f"「{h[1]}」整篇没有一个问句，加一处设问或对话", i + 1)

    return issues


def any_section(lines, keywords):
    """按任意标题层级找第一个匹配段，返回 (start, end, title)。"""
    for i, line in enumerate(lines):
        h = heading(line)
        if h and any(k in h[1] for k in keywords):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                h2 = heading(lines[j])
                if h2 and h2[0] <= h[0]:
                    end = j
                    break
            return i, end, h[1]
    return None, None, None


def check_partial(path):
    """校验单篇或片段：不要求七章，只守本篇红线。"""
    lines = read_lines(path)
    issues = []

    def add(level, code, message, line_no=None):
        issues.append({"level": level, "code": code,
                       "message": message, "line": line_no})

    for i, line in enumerate(lines):
        h = heading(line)
        if h and h[0] <= 2 and "总览" in h[1]:
            add("FAIL", "总览", "单篇稿不要写总览章", i + 1)
        if line.strip().startswith("|"):
            add("FAIL", "表格", "正文出现表格行", i + 1)

    first = any_section(lines, ("一辩稿",))
    allowed = range(first[0], first[1]) if first[0] is not None else range(0)
    for i, line in enumerate(lines):
        if i in allowed or line.lstrip().startswith(">"):
            continue
        if ORDINALS.search(line):
            add("FAIL", "序号", "除一辩稿外出现「第一、第二」或「第一刀、第二刀」", i + 1)

    atk = any_section(lines, ("攻防", "红队"))
    allowed_atk = range(atk[0], atk[1]) if atk[0] is not None else range(0)
    for i, line in enumerate(lines):
        if i in allowed_atk:
            continue
        if OPPONENT.search(line):
            add("FAIL", "攻防越界", "「对方可能会说」「即便对方」只能出现在攻防段", i + 1)

    handoff = re.compile(
        r"我方一辩(说过|讲过|已经|曾|所言|提到|指出|论证过)|"
        r"正如(我方)?一辩(所说|所言)|"
        r"前面(的)?一辩|刚才(的)?一辩|"
        r"剩下的账|我方[二三四]辩来(算|说|讲)|"
        r"(交给|有请|请听|轮到)(我方)?[二三四]辩|"
        r"下面(由|请|交给|有请)(我方)?[二三四]辩|"
        r"我方[二三四]辩(已经|刚才)(证明|说|讲|论证)"
    )
    formula_label = re.compile(
        r"打什么[：:]|刀法[：:]|本组收束[：:]|固定稿[：:]|积木稿[：:]|积木\s*[0-9一二三四五六七八九十]+｜"
    )
    for i, line in enumerate(lines):
        if handoff.search(line):
            add("FAIL", "交接话术", "出现环节交接话术，改成直接引内容", i + 1)
        if formula_label.search(line):
            add("WARN", "公式", "出现备赛表格标签，改成能直接念出的话", i + 1)

    summary = any_section(lines, ("小结",))
    if summary[0] is not None:
        bad = re.compile(
            r"这一轮盘问|这一轮质询|我方问|我问了|对方答了|"
            r"第[一二三四五六七八九十]+组|对方认了[两三四五]?件|对方躲了[一两]?个"
        )
        for i in range(summary[0] + 1, summary[1]):
            if bad.search(lines[i]):
                add("FAIL", "小结", "盘问小结写成了问答流水账，改成按申论推证", i + 1)

    for i, line in enumerate(lines):
        h = heading(line)
        if not h or h[0] > 3:
            continue
        stop = len(lines)
        for j in range(i + 1, len(lines)):
            h2 = heading(lines[j])
            if h2 and h2[0] <= h[0]:
                stop = j
                break
        scan_section_rules(lines, i, stop, h[1], add, partial=True)
        m = TARGET.search(h[1])
        if not m:
            if any(k in h[1] for k in ("首质", "盘问", "申论", "小结", "结辩")):
                add("WARN", "字数", f"「{h[1]}」没标目标字数", i + 1)
            continue
        target = int(m.group(1))
        actual = count_chars(lines[i + 1:stop])
        ratio = actual / target if target else 0
        if ratio < 0.92 or ratio > 1.08:
            add("FAIL", "字数",
                f"「{h[1]}」目标 {target} 字，实际 {actual} 字（{ratio:.0%}）", i + 1)

    for i, line in enumerate(lines):
        if "待核实" in line and not re.search(r"待核实（缺[：:]", line):
            add("WARN", "核实", "「待核实」后面没写缺什么", i + 1)
        for term in AI_BLACKLIST:
            if term in line:
                add("FAIL", "AI套话", f"出现 AI 套话「{term}」，换成具体的人话", i + 1)

    joined = "\n".join(lines)
    soft_count = sum(joined.count(term) for term in AI_SOFT)
    if soft_count >= 4:
        add("WARN", "AI套话", f"慎用词出现 {soft_count} 次，删到三次以内")

    return issues


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    parser = argparse.ArgumentParser(description="校验辩论备战包")
    parser.add_argument("path", help="备战包 Markdown 路径")
    parser.add_argument("--partial", action="store_true", help="单篇或片段校验，不要求七章齐全")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"找不到文件：{path}")
        return 2

    issues = check_partial(path) if args.partial else check(path)
    fails = [x for x in issues if x["level"] == "FAIL"]
    warns = [x for x in issues if x["level"] == "WARN"]

    if args.json:
        print(json.dumps({"file": str(path), "fails": fails, "warns": warns},
                         ensure_ascii=False, indent=2))
    else:
        print(f"校验：{path}")
        for item in fails:
            where = f"第 {item['line']} 行 " if item["line"] else ""
            print(f"[FAIL] {where}{item['message']}")
        for item in warns:
            where = f"第 {item['line']} 行 " if item["line"] else ""
            print(f"[WARN] {where}{item['message']}")
        print(f"结果：{len(fails)} 项失败，{len(warns)} 项提醒")

    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
