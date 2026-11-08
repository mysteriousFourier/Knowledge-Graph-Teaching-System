#!/usr/bin/env python3
"""Inspect actual PDF pages, build an overlay-aware text/visual index, and render contact sheets.

Requirements: pip install pymupdf pillow
Usage: python slide_index.py lecture.pdf --out work/page_index.csv --contact-dir work/contact_sheets
"""
from __future__ import annotations

import argparse
import csv
import difflib
from pathlib import Path

try:
    import fitz
    from PIL import Image, ImageChops, ImageDraw, ImageFont
except ImportError as exc:
    raise SystemExit("需要 PyMuPDF 和 Pillow：pip install pymupdf pillow") from exc


def slide_image(page, max_width=450):
    """Always render the actual PDF page; no OCR or text-only approximation."""
    zoom = max_width / float(page.rect.width)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)


def changed_ratio(a, b, threshold=25):
    if a is None:
        return None
    size = (128, 72)
    a = a.convert('L').resize(size)
    b = b.convert('L').resize(size)
    diff = ImageChops.difference(a, b)
    hist = diff.histogram()
    return sum(hist[threshold:]) / (size[0]*size[1])


def title_from(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[0] if lines else '[图像/无可提取文本]')[:125]


def new_excerpt(previous, current):
    old_lines = {s.strip() for s in previous.splitlines() if s.strip()}
    added = [s.strip() for s in current.splitlines() if s.strip() and s.strip() not in old_lines]
    return ' / '.join(added)[:360]


def render_contact_sheet(records, dest, cols=4, rows=4, width=390):
    """Build ordered preview sheets for human/model verification of overlays."""
    dest.mkdir(parents=True, exist_ok=True)
    gap, label_h = 14, 31
    page_h = max(img.height for _n, img, _t in records)
    from math import ceil
    per_sheet = cols * rows
    paths = []
    for begin in range(0, len(records), per_sheet):
        chunk = records[begin:begin+per_sheet]
        sheet_w = cols * width + (cols+1)*gap
        sheet_h = rows * (page_h+label_h) + (rows+1)*gap
        sheet = Image.new('RGB', (sheet_w, sheet_h), 'white')
        d = ImageDraw.Draw(sheet)
        for k, (n, img, title) in enumerate(chunk):
            row, col = divmod(k, cols)
            left = gap + col*(width+gap)
            top = gap + row*(page_h+label_h+gap)
            im = img.copy()
            im.thumbnail((width,page_h))
            sheet.paste(im, (left, top+label_h))
            d.rectangle((left,top+label_h,left+width-1,top+label_h+page_h-1), outline='#777777', width=1)
            d.text((left+5,top+5), f'PDF PAGE {n:03d}', fill='black')
        dest_path = dest / f'contact_{begin//per_sheet+1:03d}.jpg'
        sheet.save(dest_path, quality=87)
        paths.append(dest_path)
    return paths


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('pdf', type=Path)
    p.add_argument('--out', type=Path, default=Path('page_index.csv'))
    p.add_argument('--contact-dir', type=Path, default=None)
    p.add_argument('--columns', type=int, default=4)
    p.add_argument('--rows', type=int, default=4)
    p.add_argument('--thumb-width', type=int, default=380)
    a = p.parse_args()
    if not a.pdf.is_file():
        p.error(f'PDF不存在：{a.pdf}')
    if a.columns < 1 or a.rows < 1 or a.thumb_width < 100:
        p.error('columns/rows >= 1 且 thumb-width >= 100')

    doc = fitz.open(a.pdf)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    table, thumbnails = [], []
    prev_text, prev_img = '', None
    for n, page in enumerate(doc, 1):
        text = page.get_text('text', sort=True)
        img = slide_image(page, a.thumb_width)
        similarity = difflib.SequenceMatcher(None, prev_text, text, autojunk=True).ratio() if n>1 else None
        vis_change = changed_ratio(prev_img, img)
        table.append({
            'page': n,
            'pdf_title': title_from(text),
            'extracted_text_chars': len(text),
            'previous_page_text_similarity': '' if similarity is None else f'{similarity:.3f}',
            'previous_page_visual_changed_ratio': '' if vis_change is None else f'{vis_change:.3f}',
            'new_text_excerpt': new_excerpt(prev_text,text),
            'recording_time': '',
            'origin': '',
            'notes': ''
        })
        thumbnails.append((n,img,title_from(text)))
        prev_text,prev_img=text,img
    with a.out.open('w', encoding='utf-8-sig', newline='') as f:
        w=csv.DictWriter(f, fieldnames=list(table[0].keys())) if table else None
        if w:
            w.writeheader()
            w.writerows(table)
    print(f'PDF实际总页数：{len(doc)}，已生成页面索引：{a.out}')
    if a.contact_dir:
        paths=render_contact_sheet(thumbnails,a.contact_dir,a.columns,a.rows,a.thumb_width)
        print(f'联览图：{len(paths)} 张，目录：{a.contact_dir}')
    print('注意：页面新元素可能是图片/动画，必须对照缩略图；相似度仅用于定位，不是自动语义对齐。')


if __name__ == '__main__':
    main()
