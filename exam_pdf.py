#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exam_pdf.py — 학원 시험지 수준 PDF 생성기 v3.0
두 컬럼 레이아웃 + 전문적인 헤더/푸터
학생용 문제지 + 교사용 답지를 하나의 PDF로 출력
"""

import re
import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether, NextPageTemplate
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 한글 폰트 등록 ─────────────────────────────────────
def _register_fonts():
    font_candidates = [
        ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", "KR"),
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc", "KR"),
        ("/Library/Fonts/NanumGothic.ttf", "KR"),
    ]
    bold_candidates = [
        ("/Library/Fonts/NanumGothicBold.ttf", "KRB"),
    ]
    registered = {}
    for path, name in font_candidates:
        if Path(path).exists() and name not in registered:
            try:
                if path.endswith(".ttc"):
                    pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
                else:
                    pdfmetrics.registerFont(TTFont(name, path))
                registered[name] = path
                break
            except Exception:
                pass

    for path, name in bold_candidates:
        if Path(path).exists() and name not in registered:
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                registered[name] = path
            except Exception:
                pass

    if "KR" in registered and "KRB" not in registered:
        pdfmetrics.registerFont(TTFont("KRB", list(registered.values())[0]))

    return "KR" if "KR" in registered else "Helvetica"


FONT = _register_fonts()
FONT_BOLD = "KRB" if FONT == "KR" else "Helvetica-Bold"

# ── 색상 상수 ─────────────────────────────────────────
NAVY   = colors.HexColor("#1a3a6e")
BLUE   = colors.HexColor("#2563eb")
GREEN  = colors.HexColor("#16a34a")
ORANGE = colors.HexColor("#d97706")
RED    = colors.HexColor("#dc2626")
PBG    = colors.HexColor("#f7f8fa")   # passage background
C_LINE = colors.HexColor("#dddddd")
C_GRAY = colors.HexColor("#666666")
C_DARK = colors.HexColor("#1a1a1a")
C_LGRAY= colors.HexColor("#cccccc")

# ── 레이아웃 상수 ─────────────────────────────────────
W, H = A4               # 595.2pt x 841.9pt
ML = MR = 13 * mm
MB = 15 * mm
HEADER_H = 31 * mm      # header drawn in onPage callback
COL_GAP = 7 * mm
COL_W = (W - ML - MR - COL_GAP) / 2   # ~88.5mm per column
FPL = FPR = 3 * mm      # frame inner padding left/right
FPT = FPB = 2 * mm      # frame inner padding top/bottom
INNER_W = COL_W - FPL - FPR   # ~82.5mm usable table width
FRAME_H = H - HEADER_H - MB - 5 * mm
FRAME_TOP_Y = MB

ANS_HEADER = 20 * mm    # answer page header height


# ── 데이터 클래스 ─────────────────────────────────────
@dataclass
class Question:
    qtype:       str
    num:         int
    body:        str
    choices:     List[str] = field(default_factory=list)
    answer:      str = ""
    explanation: str = ""
    conditions:  List[str] = field(default_factory=list)
    model_ans:   str = ""
    rubric:      List[str] = field(default_factory=list)


# ── 유틸리티 ──────────────────────────────────────────
def esc(text: str) -> str:
    """XML 특수문자 이스케이프 (Paragraph 안전 입력용)"""
    if not text:
        return ""
    text = str(text)
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _split_body(body: str):
    """
    문제 본문에서 줄기(stem)와 지문(passage)을 분리.
    Returns: (stem, passage) — passage가 없으면 ('', )
    """
    if not body:
        return body, ""
    m = re.match(r'^(.{10,120}?[?？。.])\s+(.{40,})$', body, re.DOTALL)
    if m:
        stem = m.group(1).strip()
        rest = m.group(2).strip()
        ascii_ratio = sum(1 for c in rest if ord(c) < 128) / max(len(rest), 1)
        if ascii_ratio > 0.35:
            return stem, rest
    return body, ""


# ── 문제 파서 ─────────────────────────────────────────
def _parse_answers(answer_md: str) -> Dict:
    """답지 마크다운에서 {번호: {answer, explanation, model_ans, rubric}} 추출"""
    result = {}
    num = 0
    cur = {}
    state = None
    rubric_buf = []

    def flush():
        nonlocal cur, rubric_buf
        if num > 0:
            if rubric_buf:
                cur["rubric"] = rubric_buf[:]
            result[num] = dict(cur)
        cur = {}
        rubric_buf = []

    for line in answer_md.split("\n"):
        s = line.strip()

        if s.startswith("★"):
            flush()
            num += 1
            state = None
            continue

        if s.startswith("정답:"):
            cur["answer"] = s[3:].strip()
            state = None
        elif s.startswith("해설:"):
            cur["explanation"] = s[3:].strip()
            state = "expl"
        elif state == "expl" and s and not s.startswith(("모범", "채점", "[조건]", "- 조건", "★")):
            cur["explanation"] = cur.get("explanation", "") + " " + s
        elif s.startswith("모범 답안:"):
            cur["model_ans"] = s[6:].strip()
            state = "model"
        elif state == "model" and s and not s.startswith(("채점", "[조건]", "★")):
            cur["model_ans"] = cur.get("model_ans", "") + " " + s
        elif s.startswith("채점 기준:"):
            state = "rubric"
        elif state == "rubric" and s.startswith("- "):
            rubric_buf.append(s[2:].strip())

    flush()
    return result


def parse_questions(exam_md: str, answer_md: str) -> List[Question]:
    """
    exam_md  : 문제지 마크다운
    answer_md: 답지 마크다운
    → Question 리스트 반환
    """
    ans_data = _parse_answers(answer_md)
    questions = []
    global_num = 0

    blocks = re.split(r'\n(?=★)', exam_md)
    for block in blocks:
        block = block.strip()
        if not block.startswith("★"):
            continue

        if re.search(r'객관식', block):
            qtype = "mc"
        elif re.search(r'주관식', block):
            qtype = "sa"
        elif re.search(r'서술형', block):
            qtype = "es"
        else:
            continue

        global_num += 1
        lines = block.split("\n")

        body = ""
        choices = []
        conditions = []
        in_body = False

        i = 1
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("문제:"):
                body = line[3:].strip()
                in_body = True
            elif in_body and line and not line.startswith(
                    ("①", "②", "③", "④", "⑤", "정답:", "해설:", "답:", "모범", "채점", "[조건]", "- 조건")):
                if not re.match(r'^[①②③④⑤]', line):
                    body += " " + line
            elif re.match(r'^[①②③④⑤]', line):
                choices.append(line)
                in_body = False
            elif line.startswith("- 조건") or (line.startswith("- ") and "[조건]" in block[:block.find(line)]):
                conditions.append(line[2:].strip())
            i += 1

        body = body.strip()
        ans_info = ans_data.get(global_num, {})

        questions.append(Question(
            qtype=qtype,
            num=global_num,
            body=body,
            choices=choices,
            answer=ans_info.get("answer", ""),
            explanation=ans_info.get("explanation", ""),
            conditions=conditions,
            model_ans=ans_info.get("model_ans", ""),
            rubric=ans_info.get("rubric", []),
        ))

    return questions


# ── 스타일 ────────────────────────────────────────────
def make_styles(inner_w_mm: float = None):
    """ParagraphStyle 딕셔너리 반환"""
    s = {}

    def ps(name, **kw):
        defaults = dict(fontName=FONT, fontSize=9, leading=13,
                        textColor=C_DARK, spaceAfter=0, spaceBefore=0)
        defaults.update(kw)
        return ParagraphStyle(name, **defaults)

    s["q_num"]    = ps("q_num",   fontName=FONT_BOLD, fontSize=9.5, leading=14, textColor=C_DARK)
    s["q_type"]   = ps("q_type",  fontName=FONT_BOLD, fontSize=7.5, leading=11, textColor=colors.white, alignment=TA_RIGHT)
    s["q_stem"]   = ps("q_stem",  fontSize=9, leading=13, textColor=C_DARK)
    s["choice"]   = ps("choice",  fontSize=8.5, leading=13, textColor=C_DARK, leftIndent=4)
    s["passage"]  = ps("passage", fontSize=8, leading=12, textColor=C_DARK)
    s["ans_blank"]= ps("ans_blank", fontName=FONT_BOLD, fontSize=8.5, leading=12,
                        textColor=C_DARK, alignment=TA_RIGHT)
    s["section"]  = ps("section", fontName=FONT_BOLD, fontSize=9.5, leading=14,
                        textColor=colors.white)
    s["section_d"]= ps("section_d", fontSize=8, leading=12, textColor=colors.white, alignment=TA_RIGHT)
    s["footer"]   = ps("footer",  fontSize=7.5, textColor=C_GRAY, alignment=TA_CENTER)
    s["normal"]   = ps("normal",  fontSize=9, leading=13)
    s["warn"]     = ps("warn",    fontName=FONT_BOLD, fontSize=9, leading=13,
                        textColor=RED, alignment=TA_CENTER)
    s["ans_head"] = ps("ans_head", fontName=FONT_BOLD, fontSize=9.5, leading=14,
                        textColor=NAVY)
    s["ans_num"]  = ps("ans_num", fontName=FONT_BOLD, fontSize=9, leading=13, textColor=NAVY)
    s["ans_body"] = ps("ans_body", fontSize=8.5, leading=13, textColor=C_DARK, leftIndent=6)
    s["tbl_ans"]  = ps("tbl_ans", fontName=FONT_BOLD, fontSize=9, leading=13,
                        textColor=C_DARK, alignment=TA_CENTER)
    s["tbl_num"]  = ps("tbl_num", fontSize=8.5, leading=12,
                        textColor=C_GRAY, alignment=TA_CENTER)
    return s


# ── onPage 콜백: 문제지 ───────────────────────────────
def _exam_page_cb(canvas, doc, academy, subject, grade, scope, questions):
    canvas.saveState()

    # 파란 헤더 바 (top)
    bar_y = H - 12 * mm
    bar_h = 11 * mm
    canvas.setFillColor(NAVY)
    canvas.rect(0, bar_y, W, bar_h, fill=1, stroke=0)

    # 헤더 텍스트 (흰색)
    canvas.setFillColor(colors.white)
    canvas.setFont(FONT_BOLD, 11)
    scope_str = f" [{scope}]" if scope else ""
    title_text = f"{academy}  {grade} {subject} 유사변형 문제지{scope_str}"
    canvas.drawCentredString(W / 2, bar_y + 3.5 * mm, title_text)

    # 학생 정보 행 (이름 / 날짜 / 점수)
    info_y = H - 20 * mm
    canvas.setFillColor(C_DARK)
    canvas.setFont(FONT_BOLD, 8.5)
    canvas.drawString(ML, info_y, "이  름:")
    canvas.setLineWidth(0.5)
    canvas.setStrokeColor(C_DARK)
    canvas.line(ML + 16 * mm, info_y - 1, ML + 55 * mm, info_y - 1)

    canvas.drawString(ML + 57 * mm, info_y, "날  짜:")
    canvas.line(ML + 73 * mm, info_y - 1, ML + 105 * mm, info_y - 1)

    canvas.drawString(ML + 107 * mm, info_y, "점  수:")
    canvas.line(ML + 123 * mm, info_y - 1, W - MR, info_y - 1)

    # 문항 수 정보
    count_y = H - 25 * mm
    mc_cnt = sum(1 for q in questions if q.qtype == "mc")
    sa_cnt = sum(1 for q in questions if q.qtype == "sa")
    es_cnt = sum(1 for q in questions if q.qtype == "es")
    total = len(questions)
    count_text = f"총 {total}문항  (객관식 {mc_cnt}문항  주관식 {sa_cnt}문항  서술형 {es_cnt}문항)"
    canvas.setFont(FONT, 8)
    canvas.setFillColor(C_GRAY)
    canvas.drawString(ML, count_y, count_text)

    now_str = datetime.now().strftime("%Y.%m.%d")
    canvas.drawRightString(W - MR, count_y, f"출제일: {now_str}")

    # 헤더 하단 구분선
    sep_y = H - HEADER_H
    canvas.setStrokeColor(C_LINE)
    canvas.setLineWidth(0.8)
    canvas.line(ML, sep_y, W - MR, sep_y)

    # 세로 컬럼 구분선
    div_x = ML + COL_W + COL_GAP / 2
    canvas.setStrokeColor(C_LGRAY)
    canvas.setLineWidth(0.5)
    canvas.line(div_x, MB + 3 * mm, div_x, sep_y - 2 * mm)

    # 푸터: 페이지 번호
    canvas.setFont(FONT, 7.5)
    canvas.setFillColor(C_GRAY)
    canvas.drawCentredString(W / 2, MB / 2, f"- {doc.page} -")

    canvas.restoreState()


# ── onPage 콜백: 답지 ─────────────────────────────────
def _ans_page_cb(canvas, doc, academy, subject, grade, scope):
    canvas.saveState()

    # 헤더 바
    bar_y = H - 12 * mm
    bar_h = 11 * mm
    canvas.setFillColor(NAVY)
    canvas.rect(0, bar_y, W, bar_h, fill=1, stroke=0)

    canvas.setFillColor(colors.white)
    canvas.setFont(FONT_BOLD, 11)
    scope_str = f" [{scope}]" if scope else ""
    title_text = f"{academy}  {grade} {subject}  정답 및 해설지{scope_str}"
    canvas.drawCentredString(W / 2, bar_y + 3.5 * mm, title_text)

    # 경고 문구
    warn_y = H - 17 * mm
    canvas.setFillColor(RED)
    canvas.setFont(FONT_BOLD, 8.5)
    canvas.drawCentredString(W / 2, warn_y, "교사용 자료입니다. 학생에게 배포하지 마세요.")

    # 구분선
    sep_y = H - ANS_HEADER
    canvas.setStrokeColor(C_LINE)
    canvas.setLineWidth(0.8)
    canvas.line(ML, sep_y, W - MR, sep_y)

    # 푸터
    canvas.setFont(FONT, 7.5)
    canvas.setFillColor(C_GRAY)
    canvas.drawCentredString(W / 2, MB / 2, f"- {doc.page} -")

    canvas.restoreState()


# ── 섹션 헤더 바 ──────────────────────────────────────
def _section_bar(title: str, desc: str, inner_w_pt: float, color) -> list:
    """섹션 헤더 테이블 (컬러 배경)"""
    tbl = Table(
        [[
            Paragraph(title, ParagraphStyle("sh", fontName=FONT_BOLD, fontSize=9.5,
                                            textColor=colors.white, leading=14)),
            Paragraph(desc, ParagraphStyle("sd", fontName=FONT, fontSize=7.5,
                                           textColor=colors.white, leading=12, alignment=TA_RIGHT)),
        ]],
        colWidths=[inner_w_pt * 0.4, inner_w_pt * 0.6],
        rowHeights=[7 * mm],
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (0, -1), 5),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 5),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    return [tbl, Spacer(1, 2 * mm)]


# ── 객관식 렌더링 ─────────────────────────────────────
def _render_mc(q: Question, inner_w_pt: float, ST: dict) -> list:
    items = []
    stem, passage = _split_body(q.body)

    # 헤더 행: 번호(왼쪽) + 유형태그(오른쪽)
    tag_bg = BLUE
    tag_cell = Table(
        [[Paragraph("객관식", ParagraphStyle("tag", fontName=FONT_BOLD, fontSize=6.5,
                                            textColor=colors.white, leading=10, alignment=TA_CENTER))]],
        colWidths=[14 * mm], rowHeights=[5 * mm],
    )
    tag_cell.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), tag_bg),
        ("ROUNDEDCORNERS", (0, 0), (0, 0), [2, 2, 2, 2]),
        ("TOPPADDING",    (0, 0), (0, 0), 0),
        ("BOTTOMPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING",   (0, 0), (0, 0), 2),
        ("RIGHTPADDING",  (0, 0), (0, 0), 2),
    ]))

    num_para = Paragraph(f"<b>{q.num}.</b>", ST["q_num"])
    hdr_tbl = Table(
        [[num_para, tag_cell]],
        colWidths=[inner_w_pt - 16 * mm, 16 * mm],
    )
    hdr_tbl.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    items.append(hdr_tbl)

    # 문제 줄기
    if stem:
        items.append(Paragraph(esc(stem), ST["q_stem"]))

    # 지문 박스
    if passage:
        items.append(Spacer(1, 2 * mm))
        passage_para = Paragraph(esc(passage), ST["passage"])
        p_box = Table([[passage_para]], colWidths=[inner_w_pt])
        p_box.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (0, 0), PBG),
            ("BOX",          (0, 0), (0, 0), 0.4, colors.HexColor("#cccccc")),
            ("TOPPADDING",   (0, 0), (0, 0), 5),
            ("BOTTOMPADDING",(0, 0), (0, 0), 5),
            ("LEFTPADDING",  (0, 0), (0, 0), 6),
            ("RIGHTPADDING", (0, 0), (0, 0), 6),
        ]))
        items.append(p_box)
        items.append(Spacer(1, 2 * mm))

    # 선지 — 한 줄에 하나씩
    for ch in q.choices:
        items.append(Paragraph(esc(ch), ST["choice"]))

    # 정답 빈칸 (오른쪽 정렬)
    items.append(Spacer(1, 1 * mm))
    ans_tbl = Table(
        [[Paragraph("정답: (       )", ST["ans_blank"])]],
        colWidths=[inner_w_pt],
    )
    ans_tbl.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (0, 0), 0),
        ("BOTTOMPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING",   (0, 0), (0, 0), 0),
        ("RIGHTPADDING",  (0, 0), (0, 0), 0),
    ]))
    items.append(ans_tbl)
    items.append(Spacer(1, 7 * mm))

    return [KeepTogether(items)]


# ── 주관식 렌더링 ─────────────────────────────────────
def _render_sa(q: Question, inner_w_pt: float, ST: dict) -> list:
    items = []
    stem, passage = _split_body(q.body)

    # 헤더 행
    tag_cell = Table(
        [[Paragraph("주관식", ParagraphStyle("tag2", fontName=FONT_BOLD, fontSize=6.5,
                                            textColor=colors.white, leading=10, alignment=TA_CENTER))]],
        colWidths=[14 * mm], rowHeights=[5 * mm],
    )
    tag_cell.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), GREEN),
        ("ROUNDEDCORNERS", (0, 0), (0, 0), [2, 2, 2, 2]),
        ("TOPPADDING",    (0, 0), (0, 0), 0),
        ("BOTTOMPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING",   (0, 0), (0, 0), 2),
        ("RIGHTPADDING",  (0, 0), (0, 0), 2),
    ]))

    num_para = Paragraph(f"<b>{q.num}.</b>", ST["q_num"])
    hdr_tbl = Table(
        [[num_para, tag_cell]],
        colWidths=[inner_w_pt - 16 * mm, 16 * mm],
    )
    hdr_tbl.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    items.append(hdr_tbl)

    if stem:
        items.append(Paragraph(esc(stem), ST["q_stem"]))

    if passage:
        items.append(Spacer(1, 2 * mm))
        passage_para = Paragraph(esc(passage), ST["passage"])
        p_box = Table([[passage_para]], colWidths=[inner_w_pt])
        p_box.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (0, 0), PBG),
            ("BOX",          (0, 0), (0, 0), 0.4, colors.HexColor("#cccccc")),
            ("TOPPADDING",   (0, 0), (0, 0), 5),
            ("BOTTOMPADDING",(0, 0), (0, 0), 5),
            ("LEFTPADDING",  (0, 0), (0, 0), 6),
            ("RIGHTPADDING", (0, 0), (0, 0), 6),
        ]))
        items.append(p_box)
        items.append(Spacer(1, 2 * mm))

    # 답 쓰는 칸
    items.append(Spacer(1, 1 * mm))
    ans_line_style = ParagraphStyle("al", fontName=FONT, fontSize=8.5, leading=12, textColor=C_DARK)
    write_area = Table(
        [[Paragraph("답:", ans_line_style), ""]],
        colWidths=[8 * mm, inner_w_pt - 8 * mm],
        rowHeights=[9 * mm],
    )
    write_area.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 0.5, C_LGRAY),
        ("LINEAFTER",     (0, 0), (0, 0),   0.5, C_LGRAY),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("FONTNAME",      (0, 0), (-1, -1), FONT),
        ("FONTSIZE",      (0, 0), (-1, -1), 8.5),
    ]))
    items.append(write_area)
    items.append(Spacer(1, 7 * mm))

    return [KeepTogether(items)]


# ── 서술형 렌더링 ─────────────────────────────────────
def _render_es(q: Question, inner_w_pt: float, ST: dict) -> list:
    items = []
    stem, passage = _split_body(q.body)

    # 헤더 행
    tag_cell = Table(
        [[Paragraph("서술형", ParagraphStyle("tag3", fontName=FONT_BOLD, fontSize=6.5,
                                            textColor=colors.white, leading=10, alignment=TA_CENTER))]],
        colWidths=[14 * mm], rowHeights=[5 * mm],
    )
    tag_cell.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), ORANGE),
        ("ROUNDEDCORNERS", (0, 0), (0, 0), [2, 2, 2, 2]),
        ("TOPPADDING",    (0, 0), (0, 0), 0),
        ("BOTTOMPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING",   (0, 0), (0, 0), 2),
        ("RIGHTPADDING",  (0, 0), (0, 0), 2),
    ]))

    num_para = Paragraph(f"<b>{q.num}.</b>", ST["q_num"])
    hdr_tbl = Table(
        [[num_para, tag_cell]],
        colWidths=[inner_w_pt - 16 * mm, 16 * mm],
    )
    hdr_tbl.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    items.append(hdr_tbl)

    if stem:
        items.append(Paragraph(esc(stem), ST["q_stem"]))

    if passage:
        items.append(Spacer(1, 2 * mm))
        passage_para = Paragraph(esc(passage), ST["passage"])
        p_box = Table([[passage_para]], colWidths=[inner_w_pt])
        p_box.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (0, 0), PBG),
            ("BOX",          (0, 0), (0, 0), 0.4, colors.HexColor("#cccccc")),
            ("TOPPADDING",   (0, 0), (0, 0), 5),
            ("BOTTOMPADDING",(0, 0), (0, 0), 5),
            ("LEFTPADDING",  (0, 0), (0, 0), 6),
            ("RIGHTPADDING", (0, 0), (0, 0), 6),
        ]))
        items.append(p_box)
        items.append(Spacer(1, 2 * mm))

    # 조건 박스
    if q.conditions:
        cond_items = []
        cond_ps = ParagraphStyle("cond", fontName=FONT_BOLD, fontSize=8, textColor=NAVY, leading=12)
        cond_items.append(Paragraph("【 조건 】", cond_ps))
        for c in q.conditions:
            cond_items.append(Paragraph(f"• {esc(c)}",
                ParagraphStyle("ci", fontName=FONT, fontSize=8, leading=12, textColor=C_DARK, leftIndent=6)))
        cond_box = Table([cond_items], colWidths=[inner_w_pt])
        cond_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#fff8ee")),
            ("BOX",        (0, 0), (0, 0), 0.4, ORANGE),
            ("TOPPADDING", (0, 0), (0, 0), 4),
            ("BOTTOMPADDING",(0, 0),(0, 0), 4),
            ("LEFTPADDING", (0, 0), (0, 0), 6),
            ("RIGHTPADDING",(0, 0), (0, 0), 6),
        ]))
        items.append(cond_box)
        items.append(Spacer(1, 2 * mm))

    # 답 쓰는 넓은 영역
    write_area = Table(
        [[""]],
        colWidths=[inner_w_pt],
        rowHeights=[18 * mm],
    )
    write_area.setStyle(TableStyle([
        ("BOX",          (0, 0), (0, 0), 0.5, C_LGRAY),
        ("BACKGROUND",   (0, 0), (0, 0), colors.white),
        ("TOPPADDING",   (0, 0), (0, 0), 0),
        ("BOTTOMPADDING",(0, 0), (0, 0), 0),
        ("LEFTPADDING",  (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
    ]))
    items.append(write_area)
    items.append(Spacer(1, 7 * mm))

    return [KeepTogether(items)]


# ── 정답 요약 표 ──────────────────────────────────────
def _ans_summary(questions: List[Question], ST: dict) -> list:
    """MC 한눈에 보기 표"""
    mc_qs = [q for q in questions if q.qtype == "mc"]
    if not mc_qs:
        return []

    full_inner = W - ML - MR - FPL - FPR
    n = len(mc_qs)
    col_w = full_inner / max(n, 1)

    hdr_cells = [Paragraph(str(q.num), ST["tbl_num"]) for q in mc_qs]
    ans_cells = [Paragraph(esc(q.answer) if q.answer else "—", ST["tbl_ans"]) for q in mc_qs]

    summary_tbl = Table(
        [hdr_cells, ans_cells],
        colWidths=[col_w] * n,
        rowHeights=[6 * mm, 7 * mm],
    )
    summary_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#eef2f8")),
        ("BACKGROUND",   (0, 1), (-1, 1), colors.white),
        ("GRID",         (0, 0), (-1, -1), 0.4, C_LGRAY),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME",     (0, 0), (-1, -1), FONT),
        ("FONTSIZE",     (0, 0), (-1, -1), 8.5),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))

    title_style = ParagraphStyle("sum_title", fontName=FONT_BOLD, fontSize=10,
                                 textColor=NAVY, leading=14, spaceAfter=3)
    return [
        Paragraph("■ 정답 한눈에 보기", title_style),
        Spacer(1, 2 * mm),
        summary_tbl,
        Spacer(1, 5 * mm),
        HRFlowable(width="100%", thickness=0.5, color=C_LINE, spaceAfter=4 * mm),
    ]


# ── 답지 개별 문제 렌더링 ──────────────────────────────
def _render_ans_q(q: Question, full_inner_w_pt: float, ST: dict) -> list:
    items = []

    # 타입 색상
    type_color = {
        "mc": BLUE,
        "sa": GREEN,
        "es": ORANGE,
    }.get(q.qtype, NAVY)
    type_label = {"mc": "객관식", "sa": "주관식", "es": "서술형"}.get(q.qtype, "")

    # 번호 + 타입 태그 + 정답
    ans_text = q.answer if q.answer else "—"
    num_cell = Paragraph(f"<b>{q.num}.</b>  [{type_label}]", ST["ans_num"])
    ans_cell = Paragraph(f"<b>정답: {esc(ans_text)}</b>",
                         ParagraphStyle("aq", fontName=FONT_BOLD, fontSize=9,
                                        textColor=type_color, leading=13, alignment=TA_RIGHT))
    hdr = Table(
        [[num_cell, ans_cell]],
        colWidths=[full_inner_w_pt * 0.5, full_inner_w_pt * 0.5],
    )
    hdr.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW",    (0, 0), (-1, -1), 0.3, C_LGRAY),
    ]))
    items.append(hdr)

    # 해설
    if q.explanation:
        items.append(Spacer(1, 1 * mm))
        items.append(Paragraph(f"해설: {esc(q.explanation)}", ST["ans_body"]))

    # 모범답안 (서술형)
    if q.model_ans:
        items.append(Spacer(1, 1 * mm))
        items.append(Paragraph(f"모범답안: {esc(q.model_ans)}", ST["ans_body"]))

    # 채점기준 (서술형)
    if q.rubric:
        items.append(Spacer(1, 1 * mm))
        items.append(Paragraph("채점기준:",
            ParagraphStyle("rb_hd", fontName=FONT_BOLD, fontSize=8.5, textColor=NAVY, leading=12, leftIndent=6)))
        for r in q.rubric:
            items.append(Paragraph(f"• {esc(r)}",
                ParagraphStyle("rb", fontName=FONT, fontSize=8.5, textColor=C_DARK, leading=12, leftIndent=12)))

    items.append(Spacer(1, 5 * mm))
    return [KeepTogether(items)]


# ── 메인 생성 함수 ────────────────────────────────────
def generate_exam_pdf(
    exam_md:   str,
    answer_md: str,
    academy:   str = "미래학원",
    subject:   str = "영어",
    grade:     str = "중등2",
    scope:     str = "",
    out_path:  str = None,
) -> bytes:
    """
    exam_md, answer_md → 학원 시험지 스타일 PDF bytes 반환.
    out_path 지정 시 파일로도 저장.
    """
    buf = io.BytesIO()
    ST = make_styles(INNER_W / mm)

    # 문제 파싱
    questions = parse_questions(exam_md, answer_md)

    mc_qs = [q for q in questions if q.qtype == "mc"]
    sa_qs = [q for q in questions if q.qtype == "sa"]
    es_qs = [q for q in questions if q.qtype == "es"]

    # ── 콜백 함수 (클로저로 변수 캡처) ──────────────────
    def exam_cb(canvas, doc):
        _exam_page_cb(canvas, doc, academy, subject, grade, scope, questions)

    def ans_cb(canvas, doc):
        _ans_page_cb(canvas, doc, academy, subject, grade, scope)

    # ── DocTemplate ──────────────────────────────────
    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=ML, rightMargin=MR,
        topMargin=HEADER_H, bottomMargin=MB,
        title=f"{academy} {grade} {subject} 유사변형 문제지",
    )

    # 문제지: 2컬럼 레이아웃
    frame_L = Frame(
        x1=ML, y1=MB,
        width=COL_W, height=FRAME_H,
        topPadding=FPT, bottomPadding=FPB,
        leftPadding=FPL, rightPadding=FPR,
        id='left',
    )
    frame_R = Frame(
        x1=ML + COL_W + COL_GAP, y1=MB,
        width=COL_W, height=FRAME_H,
        topPadding=FPT, bottomPadding=FPB,
        leftPadding=FPL, rightPadding=FPR,
        id='right',
    )
    pt_exam = PageTemplate(id='EXAM', frames=[frame_L, frame_R], onPage=exam_cb)

    # 답지: 단일 컬럼
    frame_ans = Frame(
        x1=ML, y1=MB,
        width=W - ML - MR, height=H - ANS_HEADER - MB,
        topPadding=FPT, bottomPadding=FPB,
        leftPadding=FPL, rightPadding=FPR,
        id='ans',
    )
    pt_ans = PageTemplate(id='ANS', frames=[frame_ans], onPage=ans_cb)

    doc.addPageTemplates([pt_exam, pt_ans])

    # ── Story 구성 ────────────────────────────────────
    story = []

    # 문제지 섹션
    inner_pt = INNER_W  # frame usable width in points

    if mc_qs:
        story += _section_bar("■ 객관식", "5지선다형 — 정답 번호를 ( ) 안에 쓰시오", inner_pt, BLUE)
        for q in mc_qs:
            story += _render_mc(q, inner_pt, ST)

    if sa_qs:
        story += _section_bar("■ 주관식", "답을 빈칸에 직접 쓰시오", inner_pt, GREEN)
        for q in sa_qs:
            story += _render_sa(q, inner_pt, ST)

    if es_qs:
        story += _section_bar("■ 서술형", "조건에 맞게 서술하시오", inner_pt, ORANGE)
        for q in es_qs:
            story += _render_es(q, inner_pt, ST)

    # 답지 섹션
    story.append(NextPageTemplate('ANS'))
    story.append(PageBreak())

    full_inner = W - ML - MR - FPL - FPR
    story += _ans_summary(questions, ST)
    for q in questions:
        story += _render_ans_q(q, full_inner, ST)

    doc.build(story)

    pdf_bytes = buf.getvalue()
    if out_path:
        Path(out_path).write_bytes(pdf_bytes)
    return pdf_bytes


def exam_md_to_pdf(
    exam_md:   str,
    answer_md: str,
    academy:   str = "미래학원",
    subject:   str = "영어",
    grade:     str = "중등2",
    scope:     str = "",
) -> bytes:
    """공개 API: exam_md + answer_md → PDF bytes"""
    return generate_exam_pdf(exam_md, answer_md, academy, subject, grade, scope)
