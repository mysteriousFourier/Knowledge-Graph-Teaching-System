#!/usr/bin/env python3
"""Mechanically audit a Chinese PDF-aligned read-aloud lecture manuscript.

Checks: exact PDF page sequence, content presence, minimum Han-character count,
raw math/TeX residue, generic AI outline openers, excessive blank lines, repeated
adjacent page scripts, and optional provenance ledger coverage.

This is NOT a semantic verifier: visual PDF matching, factual correctness and
recording-to-page alignment require source inspection.
"""
from __future__ import annotations
import argparse
import csv
import difflib
import re
import statistics
from collections import Counter
from pathlib import Path

HEADER = re.compile(r'^##\s*第\s*(\d+)\s*页\s*$', re.M)
ALL_HEADINGS = re.compile(r'^##\s+.+$', re.M)
MATH = [
    ('美元数学分隔符', re.compile(r'(?<!\\)\$')),
    ('TeX分隔符', re.compile(r'\\[\[\]\(\)]')),
    ('TeX数学命令', re.compile(r'\\(?:frac|dfrac|tfrac|mathrm|mathbf|mathbb|mathcal|sigma|chi|sum|prod|sqrt|left|right|begin|end|text|alpha|beta|theta|gamma|Delta|epsilon|operatorname|overline|hat|widehat|infty|cdot|times|sim|approx|displaystyle)\b')),
    ('数学源码符号', re.compile(r'(?<!\w)(?:[A-Za-z]+_[A-Za-z0-9{}]+|[A-Za-z]+\^[A-Za-z0-9{}]+)|[∑∏√∫]')),
]
BANNED_OPENERS = ('这一页讲的是', '下面进入', '核心结论是', '本页重点是', '假设条件：', '检验统计量：')
PLACEHOLDERS = ('+++', 'TODO', 'TBD', '待补写', '此处待补', '待完善', '[补充讲稿]')
ORIGINS = {'recorded','adapted','simulated','transition'}
CONFIDENCE = {'high','medium','low',''}


def cjklen(s):
    return sum(('\u3400' <= c <= '\u4dbf') or ('\u4e00' <= c <= '\u9fff') for c in s)


def get_pages(s):
    matches=list(HEADER.finditer(s))
    pages=[]
    for k, m in enumerate(matches):
        end=matches[k+1].start() if k+1<len(matches) else len(s)
        pages.append((int(m.group(1)),s[m.end():end].strip()))
    return pages


def pdf_count(pdf):
    try:
        import fitz
    except ImportError as e:
        raise RuntimeError('需要PyMuPDF来验证PDF实际页数：pip install pymupdf') from e
    with fitz.open(pdf) as doc:
        return len(doc)


def audit_coverage(path,expected,errors,warnings):
    try:
        with path.open(encoding='utf-8-sig', newline='') as f:
            records=list(csv.DictReader(f))
    except Exception as e:
        errors.append(f'对齐台账读取失败：{e}')
        return
    seen=[]
    for r in records:
        try:
            n=int((r.get('page') or '').strip())
            seen.append(n)
        except ValueError:
            errors.append('对齐台账存在不合法页码');continue
        origin=(r.get('origin') or '').strip()
        if origin not in ORIGINS:
            warnings.append(f'第{n}页来源标注错误或缺失：{origin!r}')
        confidence=(r.get('confidence') or '').strip()
        if confidence not in CONFIDENCE:
            warnings.append(f'第{n}页置信度不规范：{confidence!r}')
        if origin=='recorded' and not (r.get('transcript_source') or '').strip():
            warnings.append(f'第{n}页标为录音覆盖，但未填写录音来源')
    if expected is not None:
        missing=sorted(set(range(1,expected+1))-set(seen))
        extra=sorted(set(seen)-set(range(1,expected+1)))
        if missing: errors.append(f'台账缺页：{missing[:35]}'+('…' if len(missing)>35 else ''))
        if extra: errors.append(f'台账多出页：{extra[:35]}')
    duplicates=sorted(n for n,count in Counter(seen).items() if count>1)
    if duplicates: errors.append(f'台账重复页：{duplicates[:30]}')
    recorded=sum((r.get('origin') or '').strip()=='recorded' for r in records)
    simulated=sum((r.get('origin') or '').strip()=='simulated' for r in records)
    return f'记录 {len(records)} 页（录音直接覆盖 {recorded} 页，模拟补讲 {simulated} 页）'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pdf',type=Path, help='原始课件PDF，用于精确核验页数')
    p.add_argument('--script',type=Path,required=True, help='最终讲稿Markdown')
    p.add_argument('--coverage',type=Path, help='独立的逐页来源与对齐台账CSV')
    p.add_argument('--min-hanzi',type=int,default=32000, help='全文最低汉字数（默认32000）')
    p.add_argument('--report',type=Path,default=Path('验收报告.md'))
    a=p.parse_args()
    if not a.script.is_file():p.error('讲稿文件不存在')
    if a.pdf and not a.pdf.is_file():p.error('PDF文件不存在')
    if a.coverage and not a.coverage.is_file():p.error('对齐台账文件不存在')
    if a.min_hanzi<0:p.error('min-hanzi须为非负数')
    s=a.script.read_text(encoding='utf-8-sig')
    pages=get_pages(s)
    got=[n for n,_ in pages]
    counts=[cjklen(t) for _,t in pages]
    total=sum(counts)
    errors,warnings=[],[]
    expected=pdf_count(a.pdf) if a.pdf else None
    if not pages:errors.append('没有任何符合 `## 第X页` 的标准页标题')
    if expected is None:
        expected=max(got) if got else 0
        warnings.append('没有提供PDF，无法验证实际总帧数；仅根据讲稿最大页码检查连续性')
    if got!=list(range(1,expected+1)):
        missing=sorted(set(range(1,expected+1))-set(got))
        repeats=sorted(n for n,c in Counter(got).items() if c>1)
        unexpected=sorted(set(got)-set(range(1,expected+1)))
        errors.append(f'页码不连续或顺序错误；缺页={missing[:40]}；重复页={repeats[:40]}；超范围={unexpected[:40]}')
    if len(ALL_HEADINGS.findall(s))!=len(pages):
        warnings.append('存在不是 `## 第X页` 的二级标题，可能导致正文结构歧义')
    prefix=s[:HEADER.search(s).start()] if HEADER.search(s) else s
    if cjklen(prefix)>100:
        warnings.append('第1页标题之前有较长正文，可能未归入任何PDF页')
    if total<a.min_hanzi:
        errors.append(f'汉字数不足：{total} / 最低目标 {a.min_hanzi}；应从实际课件补足而非重复或虚构')
    for n,body in pages:
        if cjklen(body)==0:errors.append(f'第{n}页正文为空')
        elif cjklen(body)<25:warnings.append(f'第{n}页极短（{cjklen(body)}汉字）；请核对PDF是否只有少量新内容')
        if re.search(r'\n\s*\n\s*\n',body):
            warnings.append(f'第{n}页出现多余空白行')
        for pattern_name,pat in MATH:
            if pat.search(body):
                warnings.append(f'第{n}页疑似残留{pattern_name}，需改成自然中文口播')
        if body.lstrip().startswith(BANNED_OPENERS):
            warnings.append(f'第{n}页存在模板化开头')
        for mark in PLACEHOLDERS:
            if mark in body:
                warnings.append(f'第{n}页出现占位/续写标记：{mark}')
        if re.search(r'^\s*(?:---|\*\*\*)\s*$',body,re.M):
            warnings.append(f'第{n}页存在横线分隔符，不符合紧凑版式')
    for j in range(1,len(pages)):
        na,a_text=pages[j-1]; nb,b_text=pages[j]
        if cjklen(a_text)>80 and cjklen(b_text)>80:
            score=difflib.SequenceMatcher(None,a_text[:1800],b_text[:1800],autojunk=True).ratio()
            if score>0.88:
                warnings.append(f'第{na}和{nb}页讲稿高度相似({score:.2%})：检查overlay机械重复')
    # Repeated long sentences in nonadjacent pages merit review, not automatic rejection.
    phrases={}
    for n,body in pages:
        for phrase in re.split(r'[。！？!?\n]+',body):
            phrase=re.sub(r'\s+','',phrase)
            if cjklen(phrase)>=55:
                phrases.setdefault(phrase,[]).append(n)
    shown=0
    for phrase,ns in phrases.items():
        ns=sorted(set(ns))
        if len(ns)>=2 and shown<12:
            warnings.append(f'相同长句在多页重复：{ns}（{phrase[:22]}…）')
            shown+=1
    ledger='未提供独立台账'
    if a.coverage:
        ledger=audit_coverage(a.coverage,expected,errors,warnings) or '已读取台账'
    lines=[
        '# 逐页课堂讲稿验收报告',
        '',
        f'- 主文件：`{a.script.name}`',
        f'- PDF实际页数：{expected if a.pdf else "未核验"}',
        f'- 讲稿标题数量：{len(pages)}',
        f'- 正文汉字数：**{total}**（目标不少于 {a.min_hanzi}）',
        f'- 最短/中位数/最长页：{min(counts) if counts else 0} / {round(statistics.median(counts)) if counts else 0} / {max(counts) if counts else 0} 汉字',
        f'- 对齐记录：{ledger}',
        f'- 机械校验：**{"通过" if not errors else "未通过"}**，错误{len(errors)}处，警告{len(warnings)}处',
        '',
        '## 错误（必须处理）',
        *([f'- {x}' for x in errors] or ['无']),
        '',
        '## 警告（请结合PDF/录音复核）',
        *([f'- {x}' for x in warnings[:70]] or ['无']),
        *([f'- 其余 {len(warnings)-70} 条警告未展开。'] if len(warnings)>70 else []),
        '',
        '## 此脚本不能替代的检查',
        '必须按PDF视觉页面核对讲稿是否错页、遗漏图表/箭头/overlay、误解公式；同时依据录音检查是否改写老师讲法，逐页标明模拟补讲而不冒称录音原话。',
        ''
    ]
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text('\n'.join(lines),encoding='utf-8')
    print(f'PDF页数={expected}, 讲稿页数={len(pages)}, 汉字数={total}, 错误={len(errors)}, 警告={len(warnings)}')
    print(f'报告：{a.report}')
    raise SystemExit(1 if errors else 0)


if __name__=='__main__':
    main()
