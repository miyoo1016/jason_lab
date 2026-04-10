#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exam_pdf.py — 학원 시험지 수준 PDF 생성기
학생용 문제지 + 교사용 답지를 하나의 PDF로 출력
"""

import re
import io
from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 한글 폰트 등록 ─────────────────────────────────────
def _register_fonts():
    font_candidates = [
        ("/System/Library/Fonts/Supplemental/AppleGothic.ttf",   "KR",       "KR"),
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc",            "KR",       "KR"),
        ("/Library/Fonts/NanumGothic.ttf",                        "KR",       "KR"),
        ("/Library/Fonts/NanumGothicBold.ttf",                    "KRB",      "KRB"),
    ]
    registered = {}
    for path, name, _ in font_candidates:
        if Path(path).exists() and name not in registered:
            try:
                if path.endswith(".ttc"):
                    pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
                else:
                    pdfmetrics.registerFont(TTFont(name, path))
                registered[name] = path
            except Exception:
                pass

    # Bold 없으면 Regular로 대체
    if "KR" in registered and "KRB" not in registered:
        pdfmetrics.registerFont(TTFont("KRB", list(registered.values())[0]))

    return "KR" if "KR" in registered else "Helvetica"

FONT      = _register_fonts()
FONT_BOLD = "KRB" if FONT == "KR" else "Helvetica-Bold"

# ── 색상 ──────────────────────────────────────────────
C_BLACK    = colors.HexColor("#1a1a1a")
C_DARK     = colors.HexColor("#2d2d2d")
C_GRAY     = colors.HexColor("#666666")
C_LGRAY    = colors.HexColor("#cccccc")
C_LLGRAY   = colors.HexColor("#f5f5f5")
C_BORDER   = colors.HexColor("#999999")
C_ACCENT   = colors.HexColor("#1a3a6e")   # 남색 헤더
C_MC_BG    = colors.HexColor("#eef2f8")   # 객관식 배경
C_SA_BG    = colors.HexColor("#f0f7f0")   # 주관식 배경
C_ES_BG    = colors.HexColor("#fff8ee")   # 서술형 배경
C_ANS_BG   = colors.HexColor("#fffde7")   # 정답 배경
C_LINE     = colors.HexColor("#dddddd")

# ── 스타일 ────────────────────────────────────────────
def make_styles():
    s = {}

    def ps(name, **kw):
        defaults = dict(fontName=FONT, fontSize=10, leading=16,
                        textColor=C_DARK, spaceAfter=0, spaceBefore=0)
        defaults.update(kw)
        return ParagraphStyle(name, **defaults)

    s["title"]     = ps("title",    fontName=FONT_BOLD, fontSize=18, leading=24,
                         textColor=C_ACCENT, alignment=TA_CENTER, spaceAfter=2)
    s["subtitle"]  = ps("subtitle", fontName=FONT_BOLD, fontSize=13, leading=18,
                         textColor=C_ACCENT, alignment=TA_CENTER, spaceAfter=4)
    s["meta"]      = ps("meta",     fontSize=9, textColor=C_GRAY,
                         alignment=TA_CENTER, spaceAfter=0)
    s["section"]   = ps("section",  fontName=FONT_BOLD, fontSize=11, leading=18,
                         textColor=C_ACCENT, spaceBefore=8, spaceAfter=4)
    s["q_num"]     = ps("q_num",    fontName=FONT_BOLD, fontSize=10.5, leading=16,
                         textColor=C_DARK)
    s["q_body"]    = ps("q_body",   fontSize=10.5, leading=17, textColor=C_DARK,
                         spaceAfter=3)
    s["choice"]    = ps("choice",   fontSize=10, leading=16, textColor=C_DARK,
                         leftIndent=8)
    s["passage"]   = ps("passage",  fontSize=9.5, leading=16, textColor=C_DARK,
                         leftIndent=4, rightIndent=4)
    s["ans_head"]  = ps("ans_head", fontName=FONT_BOLD, fontSize=11,
                         textColor=C_ACCENT, alignment=TA_CENTER)
    s["ans_q"]     = ps("ans_q",    fontName=FONT_BOLD, fontSize=10, leading=16,
                         textColor=C_DARK)
    s["ans_body"]  = ps("ans_body", fontSize=10, leading=16, textColor=C_DARK,
                         leftIndent=6)
    s["footer"]    = ps("footer",   fontSize=8, textColor=C_GRAY,
                         alignment=TA_CENTER)
    s["normal"]    = ps("normal",   fontSize=10, leading=16)
    return s

# ── 문제 파서 ─────────────────────────────────────────
class Question:
    def __init__(self, qtype, num, body, choices, answer, explanation, conditions, model_ans, rubric):
        self.qtype       = qtype         # mc / sa / es
        self.num         = num           # 전체 문제 번호 (1,2,3...)
        self.body        = body          # 문제 본문
        self.choices     = choices       # ['①...','②...',...]  (mc만)
        self.answer      = answer        # 정답 문자열
        self.explanation = explanation   # 해설
        self.conditions  = conditions    # 조건 리스트 (es)
        self.model_ans   = model_ans     # 모범 답안 (es)
        self.rubric      = rubric        # 채점 기준 (es)


def parse_questions(exam_md: str, answer_md: str) -> list:
    """
    exam_md  : 문제지 마크다운 (정답 없음)
    answer_md: 답지 마크다운  (정답+해설 있음)
    → Question 리스트 반환
    """
    # 답지에서 정답·해설 추출
    ans_data = _parse_answers(answer_md)

    questions = []
    global_num = 0

    # ★ 블록 분리
    blocks = re.split(r'\n(?=★)', exam_md)
    for block in blocks:
        block = block.strip()
        if not block.startswith("★"):
            continue

        # 문제 유형
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

        body       = ""
        choices    = []
        conditions = []
        in_body    = False

        i = 1  # lines[0] = ★ 헤더
        while i < len(lines):
            line = lines[i].strip()
            # 문제 본문
            if line.startswith("문제:"):
                body = line[3:].strip()
                in_body = True
            elif in_body and line and not line.startswith(("①","②","③","④","⑤",
                                                           "정답:","해설:","답:","모범","채점","[조건]","- 조건")):
                if not re.match(r'^(①|②|③|④|⑤)', line):
                    body += " " + line
            # 선지
            elif re.match(r'^[①②③④⑤]', line):
                choices.append(line)
                in_body = False
            # 조건 (서술형)
            elif line.startswith("- 조건") or (line.startswith("- ") and "[조건]" in block[:block.find(line)]):
                conditions.append(line[2:].strip())
            i += 1

        body = body.strip()

        # 답지에서 해당 문제 정답·해설 가져오기
        ans_info = ans_data.get(global_num, {})

        questions.append(Question(
            qtype       = qtype,
            num         = global_num,
            body        = body,
            choices     = choices,
            answer      = ans_info.get("answer", ""),
            explanation = ans_info.get("explanation", ""),
            conditions  = conditions,
            model_ans   = ans_info.get("model_ans", ""),
            rubric      = ans_info.get("rubric", []),
        ))

    return questions


def _parse_answers(answer_md: str) -> dict:
    """답지 마크다운에서 {번호: {answer, explanation, model_ans, rubric}} 추출"""
    result  = {}
    num     = 0
    cur     = {}
    state   = None   # None | "expl" | "model" | "rubric"
    rubric_buf = []

    def flush():
        nonlocal cur, rubric_buf
        if num > 0:
            if rubric_buf:
                cur["rubric"] = rubric_buf[:]
            result[num] = dict(cur)
        cur       = {}
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
        elif state == "expl" and s and not s.startswith(("모범","채점","[조건]","- 조건","★")):
            cur["explanation"] = cur.get("explanation","") + " " + s
        elif s.startswith("모범 답안:"):
            cur["model_ans"] = s[6:].strip()
            state = "model"
        elif state == "model" and s and not s.startswith(("채점","[조건]","★")):
            cur["model_ans"] = cur.get("model_ans","") + " " + s
        elif s.startswith("채점 기준:"):
            state = "rubric"
        elif state == "rubric" and s.startswith("- "):
            rubric_buf.append(s[2:].strip())

    flush()
    return result


# ── 페이지 헤더/푸터 ──────────────────────────────────
def _make_page_fn(academy, subject, grade, is_answer=False):
    label = "교사용 정답 및 해설지" if is_answer else "학생용 문제지"

    def on_page(canvas, doc):
        W, H = A4
        canvas.saveState()

        # 상단 가로선
        canvas.setStrokeColor(C_ACCENT)
        canvas.setLineWidth(1.5)
        canvas.line(15*mm, H - 12*mm, W - 15*mm, H - 12*mm)

        # 하단 선 + 페이지 번호
        canvas.setStrokeColor(C_LGRAY)
        canvas.setLineWidth(0.5)
        canvas.line(15*mm, 12*mm, W - 15*mm, 12*mm)
        canvas.setFont(FONT, 8)
        canvas.setFillColor(C_GRAY)
        canvas.drawCentredString(W/2, 8*mm, f"- {doc.page} -")
        canvas.drawString(15*mm, 8*mm, f"{academy}  {subject} {grade}")
        canvas.drawRightString(W - 15*mm, 8*mm, label)

        canvas.restoreState()

    return on_page


# ── 핵심: PDF 생성 ─────────────────────────────────────
def generate_exam_pdf(
    exam_md   : str,
    answer_md : str,
    academy   : str  = "미래학원",
    subject   : str  = "영어",
    grade     : str  = "중등2",
    scope     : str  = "",
    out_path  : str  = None,
) -> bytes:
    """
    exam_md, answer_md → 학원 시험지 스타일 PDF bytes 반환.
    out_path 지정 시 파일로도 저장.
    """
    buf = io.BytesIO()
    ST  = make_styles()
    now = datetime.now().strftime("%Y년 %m월 %d일")

    # 문제 파싱
    questions = parse_questions(exam_md, answer_md)

    # ── DocTemplate ──────────────────────────────────
    ML, MR, MT, MB = 18*mm, 18*mm, 22*mm, 20*mm

    doc = BaseDocTemplate(
        buf,
        pagesize      = A4,
        leftMargin    = ML, rightMargin  = MR,
        topMargin     = MT, bottomMargin = MB,
        title         = f"{academy} {grade} {subject} 유사변형 문제지",
    )

    # 문제지 / 답지 페이지 템플릿
    frame_exam = Frame(ML, MB, A4[0]-ML-MR, A4[1]-MT-MB, id="exam")
    frame_ans  = Frame(ML, MB, A4[0]-ML-MR, A4[1]-MT-MB, id="ans")

    pt_exam = PageTemplate(id="EXAM", frames=[frame_exam],
                            onPage=_make_page_fn(academy, subject, grade, False))
    pt_ans  = PageTemplate(id="ANS",  frames=[frame_ans],
                            onPage=_make_page_fn(academy, subject, grade, True))
    doc.addPageTemplates([pt_exam, pt_ans])

    story = []

    # ════════════════════════════════════════════════
    #  문제지 섹션
    # ════════════════════════════════════════════════
    story += _exam_header(academy, subject, grade, scope, now, questions, ST)

    # 유형별 섹션
    mc_qs = [q for q in questions if q.qtype == "mc"]
    sa_qs = [q for q in questions if q.qtype == "sa"]
    es_qs = [q for q in questions if q.qtype == "es"]

    if mc_qs:
        story.append(Spacer(1, 6*mm))
        story += _section_header("객관식", "5지선다형 — 정답 번호를 괄호 안에 표시하시오", ST)
        for q in mc_qs:
            story += _render_mc(q, ST)

    if sa_qs:
        story.append(Spacer(1, 5*mm))
        story += _section_header("주관식", "답을 빈칸에 직접 쓰시오", ST)
        for q in sa_qs:
            story += _render_sa(q, ST)

    if es_qs:
        story.append(Spacer(1, 5*mm))
        story += _section_header("서술형", "조건에 맞게 서술하시오", ST)
        for q in es_qs:
            story += _render_es(q, ST)

    # ════════════════════════════════════════════════
    #  답지 섹션 (페이지 바꿈)
    # ════════════════════════════════════════════════
    # 답지는 ANS 템플릿으로 전환 후 페이지 넘김
    from reportlab.platypus import NextPageTemplate
    story.append(NextPageTemplate("ANS"))
    story.append(PageBreak())
    story += _answer_header(academy, subject, grade, scope, now, ST)
    story += build_answer_summary(questions, ST)

    for q in questions:
        story += _render_answer(q, ST)

    doc.build(story)

    pdf_bytes = buf.getvalue()
    if out_path:
        Path(out_path).write_bytes(pdf_bytes)
    return pdf_bytes


# ── 문제지 헤더 ───────────────────────────────────────
def _exam_header(academy, subject, grade, scope, now, questions, ST):
    W = A4[0] - 36*mm   # usable width
    items = []

    # 학원명
    items.append(Paragraph(academy, ST["title"]))
    items.append(Spacer(1, 1*mm))

    # 시험 제목
    scope_str = f" — {scope}" if scope else ""
    items.append(Paragraph(f"{grade} {subject} 유사변형 문제지{scope_str}", ST["subtitle"]))
    items.append(Spacer(1, 3*mm))
    items.append(HRFlowable(width="100%", thickness=2, color=C_ACCENT, spaceAfter=3*mm))

    # 이름/날짜/점수 테이블
    mc_cnt = sum(1 for q in questions if q.qtype=="mc")
    sa_cnt = sum(1 for q in questions if q.qtype=="sa")
    es_cnt = sum(1 for q in questions if q.qtype=="es")
    total  = len(questions)
    info   = f"총 {total}문항  (객관식 {mc_cnt}  주관식 {sa_cnt}  서술형 {es_cnt})   |   출제일: {now}"

    name_tbl = Table(
        [
            [
                Paragraph("이  름", ParagraphStyle("n", fontName=FONT_BOLD, fontSize=10, textColor=C_DARK)),
                "",
                Paragraph("날  짜", ParagraphStyle("n", fontName=FONT_BOLD, fontSize=10, textColor=C_DARK)),
                "",
                Paragraph("점  수", ParagraphStyle("n", fontName=FONT_BOLD, fontSize=10, textColor=C_DARK)),
                "",
            ]
        ],
        colWidths=[18*mm, 40*mm, 18*mm, 35*mm, 18*mm, 35*mm],
        rowHeights=[10*mm],
    )
    name_tbl.setStyle(TableStyle([
        ("GRID",        (0,0), (-1,-1), 0.8, C_BORDER),
        ("BACKGROUND",  (0,0), (0,0),   C_LLGRAY),
        ("BACKGROUND",  (2,0), (2,0),   C_LLGRAY),
        ("BACKGROUND",  (4,0), (4,0),   C_LLGRAY),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",       (0,0), (-1,-1), "CENTER"),
        ("FONTNAME",    (0,0), (-1,-1), FONT),
        ("FONTSIZE",    (0,0), (-1,-1), 10),
    ]))
    items.append(name_tbl)
    items.append(Spacer(1, 2*mm))
    items.append(Paragraph(info, ST["meta"]))
    items.append(Spacer(1, 1*mm))
    items.append(HRFlowable(width="100%", thickness=0.5, color=C_LINE))
    return items


# ── 유형 섹션 헤더 ────────────────────────────────────
def _section_header(title, desc, ST):
    tbl = Table(
        [[Paragraph(f"■ {title}", ParagraphStyle("sh", fontName=FONT_BOLD, fontSize=11,
                    textColor=colors.white, leading=16)),
          Paragraph(desc, ParagraphStyle("sd", fontName=FONT, fontSize=9,
                    textColor=colors.white, leading=14, alignment=TA_RIGHT))]],
        colWidths=["35%", "65%"],
        rowHeights=[8*mm],
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), C_ACCENT),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING",(0,0), (0,-1),  6),
        ("RIGHTPADDING",(-1,0),(-1,-1),6),
        ("TOPPADDING", (0,0), (-1,-1), 0),
        ("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    return [tbl, Spacer(1, 3*mm)]


# ── 객관식 렌더링 ─────────────────────────────────────
def _render_mc(q: Question, ST):
    items = []

    # 문제 번호 + 본문
    q_text = f"<b>{q.num}.</b>  {_esc(q.body)}"
    header = Table(
        [[Paragraph(q_text, ST["q_body"]),
          Paragraph("정답: (      )", ParagraphStyle("ans_blank",
              fontName=FONT_BOLD, fontSize=10, textColor=C_DARK, alignment=TA_RIGHT))]],
        colWidths=["75%", "25%"],
    )
    header.setStyle(TableStyle([
        ("VALIGN",      (0,0),(-1,-1),"TOP"),
        ("TOPPADDING",  (0,0),(-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))

    # 선지 (2열 배치)
    choices = q.choices or []
    # 2열로 배치
    rows = []
    for i in range(0, len(choices), 2):
        left  = Paragraph(_esc(choices[i]),   ST["choice"]) if i   < len(choices) else ""
        right = Paragraph(_esc(choices[i+1]), ST["choice"]) if i+1 < len(choices) else ""
        rows.append([left, right])

    choice_tbl = Table(rows, colWidths=["50%", "50%"]) if rows else None
    if choice_tbl:
        choice_tbl.setStyle(TableStyle([
            ("VALIGN",       (0,0),(-1,-1),"TOP"),
            ("TOPPADDING",   (0,0),(-1,-1), 1),
            ("BOTTOMPADDING",(0,0),(-1,-1), 1),
            ("LEFTPADDING",  (0,0),(-1,-1), 4),
        ]))

    # 전체를 배경 박스로 감쌈
    inner = [header]
    if choice_tbl:
        inner.append(choice_tbl)

    box = Table([[inner]], colWidths=["100%"])
    box.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(0,0), C_MC_BG),
        ("ROUNDEDCORNERS",(0,0),(0,0), [3,3,3,3]),
        ("BOX",          (0,0),(0,0), 0.6, C_ACCENT),
        ("TOPPADDING",   (0,0),(0,0), 4),
        ("BOTTOMPADDING",(0,0),(0,0), 5),
        ("LEFTPADDING",  (0,0),(0,0), 6),
        ("RIGHTPADDING", (0,0),(0,0), 6),
    ]))
    items.append(KeepTogether([box, Spacer(1, 4*mm)]))
    return items


# ── 주관식 렌더링 ─────────────────────────────────────
def _render_sa(q: Question, ST):
    q_text = f"<b>{q.num}.</b>  {_esc(q.body)}"
    ans_line = Table(
        [["답:   " + "_" * 40]],
        colWidths=["100%"], rowHeights=[8*mm],
    )
    ans_line.setStyle(TableStyle([
        ("FONTNAME",     (0,0),(0,0), FONT),
        ("FONTSIZE",     (0,0),(0,0), 10),
        ("VALIGN",       (0,0),(0,0), "MIDDLE"),
        ("LEFTPADDING",  (0,0),(0,0), 6),
        ("TOPPADDING",   (0,0),(0,0), 0),
        ("BOTTOMPADDING",(0,0),(0,0), 0),
        ("LINEABOVE",    (0,0),(0,0), 0.5, C_LINE),
    ]))

    box = Table(
        [[Paragraph(q_text, ST["q_body"])], [ans_line]],
        colWidths=["100%"],
    )
    box.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(0,0), C_SA_BG),
        ("BACKGROUND",   (0,1),(0,1), colors.white),
        ("BOX",          (0,0),(0,-1), 0.6, colors.HexColor("#4a8a4a")),
        ("LEFTPADDING",  (0,0),(0,0), 8),
        ("RIGHTPADDING", (0,0),(0,0), 8),
        ("TOPPADDING",   (0,0),(0,0), 5),
        ("BOTTOMPADDING",(0,0),(0,0), 3),
        ("LEFTPADDING",  (0,1),(0,1), 0),
        ("RIGHTPADDING", (0,1),(0,1), 0),
        ("TOPPADDING",   (0,1),(0,1), 0),
        ("BOTTOMPADDING",(0,1),(0,1), 0),
    ]))
    return [KeepTogether([box, Spacer(1, 4*mm)])]


# ── 서술형 렌더링 ─────────────────────────────────────
def _render_es(q: Question, ST):
    q_text = f"<b>{q.num}.</b>  {_esc(q.body)}"
    inner  = [Paragraph(q_text, ST["q_body"])]

    if q.conditions:
        inner.append(Spacer(1, 1*mm))
        inner.append(Paragraph("【 조건 】",
            ParagraphStyle("cond_hd", fontName=FONT_BOLD, fontSize=9.5,
                           textColor=C_ACCENT, leading=14)))
        for c in q.conditions:
            inner.append(Paragraph(f"  • {_esc(c)}",
                ParagraphStyle("cond", fontName=FONT, fontSize=9.5,
                               textColor=C_DARK, leading=14, leftIndent=8)))

    # 답안 작성란
    line_rows = [[""] for _ in range(5)]   # 5줄
    ans_box = Table(line_rows, colWidths=["100%"], rowHeights=[7*mm]*5)
    ans_box.setStyle(TableStyle([
        ("GRID",         (0,0),(-1,-1), 0.3, C_LINE),
        ("BACKGROUND",   (0,0),(-1,-1), colors.white),
        ("TOPPADDING",   (0,0),(-1,-1), 0),
        ("BOTTOMPADDING",(0,0),(-1,-1), 0),
    ]))

    inner.append(Spacer(1, 2*mm))
    inner.append(Paragraph("[ 답안 작성란 ]",
        ParagraphStyle("abox", fontName=FONT_BOLD, fontSize=9,
                       textColor=C_GRAY, alignment=TA_CENTER)))
    inner.append(Spacer(1, 1*mm))
    inner.append(ans_box)

    box = Table([[[e for e in inner]]], colWidths=["100%"])
    box.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(0,0), C_ES_BG),
        ("BOX",          (0,0),(0,0), 0.8, colors.HexColor("#c0842a")),
        ("TOPPADDING",   (0,0),(0,0), 6),
        ("BOTTOMPADDING",(0,0),(0,0), 6),
        ("LEFTPADDING",  (0,0),(0,0), 8),
        ("RIGHTPADDING", (0,0),(0,0), 8),
    ]))
    return [KeepTogether([box, Spacer(1, 5*mm)])]


# ── 답지 헤더 ─────────────────────────────────────────
def _answer_header(academy, subject, grade, scope, now, ST):
    scope_str = f" — {scope}" if scope else ""
    items = [
        Paragraph(f"{academy}", ST["title"]),
        Spacer(1, 1*mm),
        Paragraph(f"{grade} {subject} 정답 및 해설지{scope_str}", ST["subtitle"]),
        Spacer(1, 2*mm),
        Paragraph("※ 교사용 자료입니다. 학생에게 배포하지 마세요.",
                  ParagraphStyle("warn", fontName=FONT_BOLD, fontSize=9.5,
                                 textColor=colors.HexColor("#cc2222"),
                                 alignment=TA_CENTER)),
        Spacer(1, 3*mm),
        HRFlowable(width="100%", thickness=2, color=C_ACCENT, spaceAfter=4*mm),
    ]

    # 정답 요약 테이블 (나중에 채움)
    items.append(Paragraph("▶ 정답 한눈에 보기", ST["section"]))
    return items


# ── 답지 각 문제 렌더링 ───────────────────────────────
def _render_answer(q: Question, ST):
    type_label = {"mc":"[객관식]","sa":"[주관식]","es":"[서술형]"}[q.qtype]
    items = []

    head = f"<b>{q.num}번</b> {type_label}"
    ans  = q.answer or "(정답 없음)"

    if q.qtype == "mc":
        rows = [
            [Paragraph(head, ST["ans_q"]),
             Paragraph(f"정답: <b>{_esc(ans)}</b>",
                       ParagraphStyle("a_r", fontName=FONT_BOLD, fontSize=10.5,
                                      textColor=C_ACCENT, alignment=TA_RIGHT))],
        ]
        if q.explanation:
            rows.append([Paragraph(f"해설: {_esc(q.explanation)}", ST["ans_body"]), ""])

    elif q.qtype == "sa":
        rows = [
            [Paragraph(head, ST["ans_q"]),
             Paragraph(f"정답: <b>{_esc(ans)}</b>",
                       ParagraphStyle("a_r", fontName=FONT_BOLD, fontSize=10.5,
                                      textColor=C_ACCENT, alignment=TA_RIGHT))],
        ]
        if q.explanation:
            rows.append([Paragraph(f"해설: {_esc(q.explanation)}", ST["ans_body"]), ""])

    else:  # es
        rows = [
            [Paragraph(head, ST["ans_q"]), ""],
            [Paragraph(f"모범 답안: {_esc(q.model_ans or ans)}", ST["ans_body"]), ""],
        ]
        for r in q.rubric:
            rows.append([Paragraph(f"  • {_esc(r)}", ST["ans_body"]), ""])

    tbl = Table(rows, colWidths=["60%", "40%"] if q.qtype != "es" else ["100%", "0%"])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,0), C_ANS_BG),
        ("LINEBELOW",    (0,0),(-1,0), 0.5, C_ACCENT),
        ("TOPPADDING",   (0,0),(-1,-1), 4),
        ("BOTTOMPADDING",(0,0),(-1,-1), 4),
        ("LEFTPADDING",  (0,0),(0,-1),  8),
        ("RIGHTPADDING", (-1,0),(-1,-1),8),
        ("BOX",          (0,0),(-1,-1), 0.5, C_LGRAY),
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
    ]))
    items.append(KeepTogether([tbl, Spacer(1, 3*mm)]))
    return items


# ── 정답 요약 테이블 (답지 상단) ──────────────────────
def build_answer_summary(questions, ST):
    """객관식 정답만 한눈에 보이는 요약 테이블"""
    mc_qs = [q for q in questions if q.qtype == "mc"]
    if not mc_qs:
        return []

    header = [Paragraph("번호", ParagraphStyle("th", fontName=FONT_BOLD, fontSize=9,
                alignment=TA_CENTER, textColor=colors.white))]
    answers = [Paragraph("정답", ParagraphStyle("th", fontName=FONT_BOLD, fontSize=9,
                alignment=TA_CENTER, textColor=colors.white))]
    for q in mc_qs:
        header.append(Paragraph(str(q.num),
            ParagraphStyle("tc", fontName=FONT, fontSize=9, alignment=TA_CENTER)))
        a = q.answer.strip()
        # ①~⑤ 추출
        m = re.search(r'[①②③④⑤]', a)
        a_short = m.group(0) if m else a[:4]
        answers.append(Paragraph(f"<b>{a_short}</b>",
            ParagraphStyle("ta", fontName=FONT_BOLD, fontSize=10, alignment=TA_CENTER,
                           textColor=C_ACCENT)))

    cws = [20*mm] + [12*mm]*len(mc_qs)
    tbl = Table([header, answers], colWidths=cws, rowHeights=[7*mm, 8*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,0), C_ACCENT),
        ("BACKGROUND",   (0,1),(-1,1), C_ANS_BG),
        ("GRID",         (0,0),(-1,-1), 0.5, C_BORDER),
        ("ALIGN",        (0,0),(-1,-1), "CENTER"),
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
    ]))
    return [tbl, Spacer(1, 5*mm),
            Paragraph("▶ 문제별 정답 및 해설", ST["section"])]


# ── 특수문자 이스케이프 ───────────────────────────────
def _esc(text: str) -> str:
    if not text:
        return ""
    text = str(text)
    text = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return text


# ── 공개 API: 마크다운 → PDF bytes ──────────────────
def exam_md_to_pdf(exam_md: str, answer_md: str,
                   academy="미래학원", subject="영어",
                   grade="중등2", scope="") -> bytes:
    return generate_exam_pdf(exam_md, answer_md,
                             academy=academy, subject=subject,
                             grade=grade, scope=scope)


# ── 테스트 ──────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        exam_md   = Path(sys.argv[1]).read_text(encoding="utf-8")
        answer_md = Path(sys.argv[2]).read_text(encoding="utf-8")
        out = sys.argv[3] if len(sys.argv) > 3 else "/tmp/test_exam.pdf"
        generate_exam_pdf(exam_md, answer_md, out_path=out)
        print(f"✅ 저장: {out}")
    else:
        print("사용법: python exam_pdf.py 문제지.md 답지.md [출력.pdf]")
