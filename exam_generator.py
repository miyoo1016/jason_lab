#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  초중고 유사변형 문제 자동 제작 시스템  v2.0
  Multi-Engine: Claude · Gemini · Gemma(Local)

  30년 경력 최고 입시학원 강사 AI 시스템
  대상: 초·중·고 / 과목: 국어·영어·수학·과학·사회·역사
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import sys
import json
import re
import os
from pathlib import Path
from datetime import datetime
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.rule import Rule

# 웹 검색 모듈 (선택적 import)
try:
    from web_searcher import KoreanEduSearcher, format_for_prompt
    WEB_SEARCH_AVAILABLE = True
except ImportError:
    WEB_SEARCH_AVAILABLE = False

console = Console()

# ─── 경로 설정 ───────────────────────────────────────
BASE_DIR    = Path(__file__).parent
OUTPUT_DIR  = BASE_DIR / "generated_exams"
OUTPUT_DIR.mkdir(exist_ok=True)

# ─── AI 엔진 레지스트리 ───────────────────────────────
# cost_tag: 과금 표시  /  cost_note: 소모량 설명
ENGINES = [
    {
        "group"      : "🔮  Claude  (Anthropic Cloud)",
        "provider"   : "anthropic",
        "base_url"   : "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
        "temperature": 0.3,
        "max_tokens" : 4096,
        "models": [
            {
                "name"     : "Claude Opus 4.6",
                "id"       : "claude-opus-4-6",
                "perf"     : "최고 성능",
                "cost_tag" : "💰 유료",
                "cost_note": "소모 높음  ($15/M input · $75/M output)",
            },
            {
                "name"     : "Claude Sonnet 4.6",
                "id"       : "claude-sonnet-4-6",
                "perf"     : "균형",
                "cost_tag" : "💰 유료",
                "cost_note": "소모 중간  ($3/M input · $15/M output)",
            },
            {
                "name"     : "Claude Haiku 4.5",
                "id"       : "claude-haiku-4-5-20251001",
                "perf"     : "빠름",
                "cost_tag" : "💰 유료",
                "cost_note": "소모 낮음  ($0.8/M input · $4/M output)",
            },
        ],
    },
    {
        "group"      : "✨  Gemini  (Google Cloud)",
        "provider"   : "google",
        "base_url"   : "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GOOGLE_API_KEY",
        "temperature": 0.4,
        "max_tokens" : 4096,
        "models": [
            {
                "name"     : "Gemini 2.5 Pro (Preview)",
                "id"       : "gemini-2.5-pro-preview-05-06",
                "perf"     : "최고 성능",
                "cost_tag" : "💰 유료",
                "cost_note": "소모 높음  ($1.25/M · 일부 키만 지원)",
            },
            {
                "name"     : "Gemini 2.5 Flash (Preview)",
                "id"       : "gemini-2.5-flash-preview-04-17",
                "perf"     : "균형 ★추천",
                "cost_tag" : "🆓 무료 티어",
                "cost_note": "소모 낮음  (무료 10req/min · 일부 키만 지원)",
            },
            {
                "name"     : "Gemini 2.0 Flash",
                "id"       : "gemini-2.0-flash",
                "perf"     : "빠름 ★안정",
                "cost_tag" : "🆓 무료 티어",
                "cost_note": "소모 낮음  (무료 15req/min · 유료 $0.10/M)",
            },
            {
                "name"     : "Gemini 2.0 Flash Lite",
                "id"       : "gemini-2.0-flash-lite",
                "perf"     : "가장 빠름",
                "cost_tag" : "🆓 무료 티어",
                "cost_note": "소모 최저  (무료 30req/min · 유료 $0.075/M)",
            },
        ],
    },
    {
        "group"      : "🏠  Gemma   (Local / Ollama)",
        "provider"   : "ollama",
        "base_url"   : "http://localhost:11434/v1",
        "api_key_env": None,
        "temperature": 0.4,
        "max_tokens" : 1200,   # 로컬 모델: 너무 길면 수분 소요 → 적정값
        "models": [
            {
                "name"     : "Gemma 4 E4B",
                "id"       : "gemma4:e4b",
                "perf"     : "로컬 고성능",
                "cost_tag" : "✅ 완전 무료",
                "cost_note": "API 비용 0원  (로컬 GPU/CPU 자원만 사용)",
            },
            {
                "name"     : "Gemma 4 E2B",
                "id"       : "gemma4:e2b",
                "perf"     : "로컬 경량",
                "cost_tag" : "✅ 완전 무료",
                "cost_note": "API 비용 0원  (E4B보다 빠르나 성능 낮음)",
            },
        ],
    },
]

# ─── 과목별 출제 전략 ─────────────────────────────────
SUBJECT_META = {
    "국어": {
        "icon"      : "📖",
        "points"    : ["주제·요지", "서술 방식", "어휘·어법", "문학적 표현", "추론·비판"],
        "mc_tips"   : "표현의 적절성, 글의 구조, 어휘의 문맥적 의미, 내용 일치 여부를 중심으로 출제하라.",
        "sa_tips"   : "핵심 어휘, 빈칸 완성, 지시어가 가리키는 내용을 묻는 유형을 출제하라.",
        "essay_tips": "글쓴이의 관점·의도, 서술 방식의 특징, 주제를 서술하는 문제를 출제하라.",
    },
    "영어": {
        "icon"      : "🌐",
        "points"    : ["어휘 추론", "문법·어법", "빈칸 완성", "내용 파악", "글의 순서·연결"],
        "mc_tips"   : "어법상 틀린 것 고르기, 빈칸에 들어갈 표현, 내용 불일치 고르기, 글의 제목/주제 고르기를 출제하라.",
        "sa_tips"   : "주어진 단어로 빈칸 채우기, 어형 변화, 짧은 문장 완성 유형을 출제하라.",
        "essay_tips": "문단 요약 영작, 주어진 조건에 맞는 영작, 구문 분석 설명 유형을 출제하라.",
    },
    "수학": {
        "icon"      : "🔢",
        "points"    : ["개념 이해", "공식 적용", "계산 능력", "문제 해결", "논리적 사고"],
        "mc_tips"   : "개념 적용, 계산 결과, 조건을 만족하는 값, 옳은/옳지 않은 것 고르기를 출제하라. 숫자·조건을 변형하여 유사문제를 만들어라.",
        "sa_tips"   : "답 구하기(수치), 식 세우기, 조건 찾기 유형을 출제하라.",
        "essay_tips": "풀이 과정을 단계별로 서술하는 문제를 출제하라. '풀이 과정을 쓰고 답을 구하시오' 형식을 사용하라.",
    },
    "과학": {
        "icon"      : "🔬",
        "points"    : ["과학 개념·용어", "실험 설계·결과", "원리 적용", "현상 분석", "탐구 능력"],
        "mc_tips"   : "실험 결과 예측, 원리 적용, 개념 정의, 옳은 설명 고르기 유형을 출제하라.",
        "sa_tips"   : "과학 용어 쓰기, 측정값·단위 쓰기, 실험 결과 서술 유형을 출제하라.",
        "essay_tips": "실험 설계 이유, 결과가 나타나는 원리, 현상의 원인 설명 유형을 출제하라.",
    },
    "사회": {
        "icon"      : "🌍",
        "points"    : ["개념 이해", "사례 적용", "인과 관계", "자료 해석", "비교 분석"],
        "mc_tips"   : "개념과 사례 연결, 지도·그래프 해석, 인과관계 파악, 옳은 설명 고르기 유형을 출제하라.",
        "sa_tips"   : "핵심 용어 쓰기, 빈칸 완성, 도표 해석 서술 유형을 출제하라.",
        "essay_tips": "사회 현상의 원인과 결과, 특정 제도나 개념의 의의 서술 유형을 출제하라.",
    },
    "역사": {
        "icon"      : "📜",
        "points"    : ["사건·인물 파악", "시대 순서", "원인·결과", "역사적 의의", "사료 분석"],
        "mc_tips"   : "역사 사건의 원인·결과, 시대 순서 나열, 인물 업적 연결, 사료 내용 파악 유형을 출제하라.",
        "sa_tips"   : "연도·인물·사건 이름 쓰기, 빈칸 완성, 사건 순서 나열 유형을 출제하라.",
        "essay_tips": "역사 사건의 배경과 결과, 역사적 의의와 영향 서술 유형을 출제하라.",
    },
}

GRADE_META = {
    # ── 유아
    "유아":   {"range": "4~7세",        "group": "유아",
               "vocab_note": "알파벳·파닉스·기초단어 중심. 색깔·동물·숫자·신체 등 생활 어휘. 그림과 함께 제시하고 따라 읽기·쓰기 활동 위주."},
    # ── 초등
    "초등1":  {"range": "초등학교 1학년", "group": "초등",
               "vocab_note": "받아쓰기·기초 문장 수준. 자음·모음 결합, 짧은 문장 완성. 쉬운 그림 단서 활용."},
    "초등2":  {"range": "초등학교 2학년", "group": "초등",
               "vocab_note": "짧은 문단 이해, 기초 낱말 쓰기. 일상 소재 중심."},
    "초등3":  {"range": "초등학교 3학년", "group": "초등",
               "vocab_note": "교과서 어휘, 기초 문법(주어·서술어). 짧은 지문 읽기·이해."},
    "초등4":  {"range": "초등학교 4학년", "group": "초등",
               "vocab_note": "중심 문장·세부 내용 파악. 단락 구조 이해 시작."},
    "초등5":  {"range": "초등학교 5학년", "group": "초등",
               "vocab_note": "글의 구조 파악·요약. 원인·결과 관계. 한자어 어휘 포함."},
    "초등6":  {"range": "초등학교 6학년", "group": "초등",
               "vocab_note": "추론·비교·대조. 논설문·설명문 구분. 중등 연계 어휘 포함."},
    # ── 중학교
    "중등1":  {"range": "중학교 1학년",   "group": "중등",
               "vocab_note": "교과서 기본 어휘·문법. 지문 분석·핵심어 파악."},
    "중등2":  {"range": "중학교 2학년",   "group": "중등",
               "vocab_note": "심화 문법·복잡한 문장 구조. 내신 서술형 대비 수준."},
    "중등3":  {"range": "중학교 3학년",   "group": "중등",
               "vocab_note": "고교 연계 어휘·문법. 논리적 글쓰기·비판적 사고 포함."},
    # ── 고등학교
    "고등1":  {"range": "고등학교 1학년", "group": "고등",
               "vocab_note": "고1 내신·수능 기초. 비문학 지문 독해 시작. 전문 어휘 도입."},
    "고등2":  {"range": "고등학교 2학년", "group": "고등",
               "vocab_note": "수능 유형 (빈칸·순서·삽입). 고난도 어휘·추론 문제."},
    "고등3":  {"range": "고등학교 3학년", "group": "고등",
               "vocab_note": "수능 실전 수준. EBS 연계·고난도 논리 추론. 최상위 어휘."},
}

# 표시용 그룹 순서
GRADE_GROUPS = [
    ("유아",   ["유아"]),
    ("초등",   ["초등1","초등2","초등3","초등4","초등5","초등6"]),
    ("중학교", ["중등1","중등2","중등3"]),
    ("고등학교",["고등1","고등2","고등3"]),
]

# ─── 엔진 선택 UI ─────────────────────────────────────
def select_engine() -> dict:
    """AI 엔진(provider + 모델)을 사용자가 선택하게 한다. engine_info dict 반환."""
    console.print(Rule("[bold cyan]AI 사고 엔진 선택[/bold cyan]"))

    # 그룹(provider) 선택
    g_table = Table(show_header=False, box=None, padding=(0,1))
    for i, eng in enumerate(ENGINES, 1):
        # 무료 여부 간단 표시
        has_free = any("무료" in m.get("cost_tag","") for m in eng["models"])
        badge = "[green]무료포함[/green]" if has_free else "[yellow]유료전용[/yellow]"
        g_table.add_row(f"[cyan]{i}.[/cyan]", eng["group"], badge)
    console.print(g_table)

    while True:
        g = Prompt.ask("[cyan]엔진 그룹 번호[/cyan]", default="3")
        if g.isdigit() and 1 <= int(g) <= len(ENGINES):
            engine = ENGINES[int(g) - 1]
            break
        console.print(f"[red]1~{len(ENGINES)} 중 선택하세요[/red]")

    # 모델 선택 (비용 정보 포함)
    console.print(f"\n  {engine['group']} — 모델 선택:")
    m_table = Table(show_header=True, box=None, padding=(0,1))
    m_table.add_column("번호",  style="cyan",   width=4)
    m_table.add_column("모델명",               width=22)
    m_table.add_column("성능",  style="dim",    width=12)
    m_table.add_column("과금",                 width=14)
    m_table.add_column("소모량/비고",  style="dim")
    for i, m in enumerate(engine["models"], 1):
        cost_color = (
            "green"  if "완전 무료"  in m.get("cost_tag","") else
            "yellow" if "무료 티어" in m.get("cost_tag","") else
            "red"
        )
        m_table.add_row(
            str(i),
            m["name"],
            m.get("perf",""),
            f"[{cost_color}]{m.get('cost_tag','')}[/{cost_color}]",
            m.get("cost_note",""),
        )
    console.print(m_table)

    while True:
        m = Prompt.ask("[cyan]모델 번호[/cyan]", default="1")
        if m.isdigit() and 1 <= int(m) <= len(engine["models"]):
            model = engine["models"][int(m) - 1]
            break
        console.print(f"[red]1~{len(engine['models'])} 중 선택하세요[/red]")

    # API 키 확인/입력
    api_key = "ollama"  # ollama 기본값
    if engine["api_key_env"]:
        api_key = os.environ.get(engine["api_key_env"], "")
        if not api_key:
            console.print(
                f"\n[yellow]환경변수 [bold]{engine['api_key_env']}[/bold]가 설정되지 않았습니다.[/yellow]"
            )
            api_key = Prompt.ask(
                f"  API 키를 직접 입력하세요 (엔터 시 건너뜀)",
                default="", password=True
            )
        if not api_key:
            console.print("[red]API 키 없이는 이 엔진을 사용할 수 없습니다.[/red]")
            return select_engine()  # 재선택

    engine_info = {
        "group"      : engine["group"],
        "provider"   : engine["provider"],
        "base_url"   : engine["base_url"],
        "api_key"    : api_key,
        "model_name" : model["name"].strip(),
        "model_id"   : model["id"],
        "temperature": engine["temperature"],
        "max_tokens" : engine["max_tokens"],
    }

    console.print(
        f"\n  [green]✓[/green] 선택된 엔진: [bold]{engine_info['group']}[/bold]  "
        f"→ [bold yellow]{engine_info['model_name']}[/bold yellow]"
    )
    return engine_info


def build_ai_client(engine_info: dict) -> OpenAI:
    """선택된 엔진 정보로 OpenAI 호환 클라이언트 생성."""
    return OpenAI(
        base_url=engine_info["base_url"],
        api_key=engine_info["api_key"],
    )


# ─── 프롬프트 빌더 ────────────────────────────────────
def build_system_prompt(subject: str, grade: str) -> str:
    sm = SUBJECT_META[subject]
    gm = GRADE_META[grade]
    return f"""당신은 대한민국 최고 수준의 입시 전문 강사입니다. 30년 경력을 가진 {subject} 과목 전문가로서, 실제 학교 시험과 수능 출제 방식을 완벽히 이해하고 있습니다.

[역할]
{grade}({gm["range"]}) {subject} 유사변형 문제를 제작합니다.

[어휘 수준]
{gm["vocab_note"]}

[{subject} 출제 핵심 포인트]
{", ".join(sm["points"])}

[절대 준수 원칙]
1. 지문의 핵심 내용과 개념을 정확히 반영할 것
2. 원본 문제와 유사하되, 표현·소재·수치·조건을 변형할 것
3. 정답이 명확하고 오답 보기는 그럴듯하지만 틀린 것으로 구성할 것
4. 해설은 왜 정답인지, 왜 오답인지 교육적으로 설명할 것
5. 지정된 출력 형식을 반드시 지킬 것"""


def build_mc_prompt(subject: str, grade: str, passage: str, original_q: str,
                    difficulty: str = "", web_ref: str = "", num_questions: int = 2) -> str:
    """객관식 통합 프롬프트 — num_questions개를 난이도 혼합으로 한 번에 생성"""
    sm = SUBJECT_META[subject]
    web_section = f"\n\n{web_ref}" if web_ref else ""

    # 난이도 배분 계획 (num_questions에 따라 자동 배분)
    if num_questions == 1:
        diff_plan = "중(심화) 1개"
    elif num_questions == 2:
        diff_plan = "하(기초) 1개, 상(최고) 1개"
    else:
        n_ha  = max(1, num_questions // 3)
        n_sang= max(1, num_questions // 3)
        n_jung= num_questions - n_ha - n_sang
        diff_plan = f"하(기초) {n_ha}개, 중(심화) {n_jung}개, 상(최고) {n_sang}개"

    q_blocks = "\n\n".join(
        f"""★ 객관식 {i}번
문제: (문제 내용)
① (보기1)
② (보기2)
③ (보기3)
④ (보기4)
⑤ (보기5)
정답: ①②③④⑤ 중 하나
해설: (정답 이유 + 오답 이유 간략히)"""
        for i in range(1, num_questions + 1)
    )
    return f"""다음 {subject} 지문을 분석하여 {grade} 수준의 객관식 문제 {num_questions}개를 제작하시오.{web_section}

[지문]
{passage}

[원본 문제 참고]
{original_q}

[출제 지침]
- 총 {num_questions}개 / 난이도 배분: {diff_plan}
- 출제 유형 힌트: {sm["mc_tips"]}
- 보기는 반드시 5개, 정답은 1개만

[출력 형식 - 반드시 이 형식 그대로]

{q_blocks}"""


def build_sa_prompt(subject: str, grade: str, passage: str, original_q: str,
                    difficulty: str = "", web_ref: str = "", num_questions: int = 2) -> str:
    """주관식 통합 프롬프트"""
    sm = SUBJECT_META[subject]
    web_section = f"\n\n{web_ref}" if web_ref else ""

    if num_questions == 1:
        diff_plan = "중(심화) 1개"
    elif num_questions == 2:
        diff_plan = "하(기초) 1개, 상(최고) 1개"
    else:
        n_ha = max(1, num_questions // 3)
        n_sang = max(1, num_questions // 3)
        diff_plan = f"하(기초) {n_ha}개, 중(심화) {num_questions-n_ha-n_sang}개, 상(최고) {n_sang}개"

    q_blocks = "\n\n".join(
        f"""★ 주관식 {i}번
문제: (문제 내용)
정답: (모범 답안)
해설: (채점 기준 포함)"""
        for i in range(1, num_questions + 1)
    )
    return f"""다음 {subject} 지문을 분석하여 {grade} 수준의 주관식 문제 {num_questions}개를 제작하시오.{web_section}

[지문]
{passage}

[원본 문제 참고]
{original_q}

[출제 지침]
- 총 {num_questions}개 / 난이도 배분: {diff_plan}
- 출제 유형 힌트: {sm["sa_tips"]}
- 답은 단어, 어구, 또는 1~2문장 수준

[출력 형식 - 반드시 이 형식 그대로]

{q_blocks}"""


def build_essay_prompt(subject: str, grade: str, passage: str, original_q: str,
                       difficulty: str = "", web_ref: str = "", num_questions: int = 1) -> str:
    """서술형 통합 프롬프트"""
    sm = SUBJECT_META[subject]
    web_section = f"\n\n{web_ref}" if web_ref else ""

    q_blocks = "\n\n".join(
        f"""★ 서술형 {i}번
문제: (문제 내용)
[조건]
- 조건1: (내용)
- 조건2: (내용)
모범 답안: (이상적인 답안 예시)
채점 기준:
- (기준1): (점수)점
- (기준2): (점수)점, 총 (합계)점"""
        for i in range(1, num_questions + 1)
    )
    return f"""다음 {subject} 지문을 분석하여 {grade} 수준의 서술형 문제 {num_questions}개를 제작하시오.{web_section}

[지문]
{passage}

[원본 문제 참고]
{original_q}

[출제 지침]
- 총 {num_questions}개 / 난이도: 문제별로 기초~최고 수준 혼합
- 출제 유형 힌트: {sm["essay_tips"]}
- 각 문제마다 조건 2~3개, 모범 답안, 채점 기준 제시

[출력 형식 - 반드시 이 형식 그대로]

{q_blocks}"""


# ══════════════════════════════════════════════════════
#  웹 검색 전용 프롬프트 빌더 (지문 없이 웹 자료만으로 출제)
# ══════════════════════════════════════════════════════

def build_web_system_prompt(subject: str, grade: str) -> str:
    """웹 검색 전용 모드용 시스템 프롬프트"""
    sm = SUBJECT_META[subject]
    gm = GRADE_META[grade]
    return f"""당신은 대한민국 최고 수준의 입시 전문 강사입니다. 30년 경력을 가진 {subject} 과목 전문가로서, 실제 학교 시험과 수능 출제 방식을 완벽히 이해하고 있습니다.

[역할]
{grade}({gm["range"]}) {subject} 시험 문제를 웹에서 수집한 교육 자료를 바탕으로 직접 출제합니다.
별도의 지문이 없으므로, 수집된 자료에서 핵심 개념·지식·문제 패턴을 추출하여
해당 학년 교육과정에 맞는 고품질 문제를 직접 창작합니다.

[어휘 수준]
{gm["vocab_note"]}

[{subject} 출제 핵심 포인트]
{", ".join(sm["points"])}

[웹 전용 출제 원칙]
1. 수집 자료의 개념·지식·문제 패턴을 철저히 분석할 것
2. {grade} 교육과정에 맞는 내용만 출제할 것
3. 실제 학교 시험에서 출제될 법한 현실적인 문제를 만들 것
4. 필요시 지문(단문)을 직접 창작하여 문제에 포함할 것
5. 정답이 명확하고 해설이 교육적이어야 함
6. 지정된 출력 형식을 반드시 지킬 것"""


def build_web_mc_prompt(subject: str, grade: str, web_content: str,
                        difficulty: str = "", num_questions: int = 2) -> str:
    """웹 자료만으로 객관식 문제 생성 (통합)"""
    sm = SUBJECT_META[subject]
    q_blocks = "\n\n".join(
        f"""★ 객관식 {i}번
문제: (문제 내용 — 필요시 짧은 지문·자료 직접 창작 가능)
① (보기1)
② (보기2)
③ (보기3)
④ (보기4)
⑤ (보기5)
정답: ①②③④⑤ 중 하나
해설: (정답 이유 + 오답 이유 간략히)"""
        for i in range(1, num_questions + 1)
    )
    return f"""아래 웹 수집 교육 자료를 분석하여 {grade} 수준의 {subject} 객관식 문제 {num_questions}개를 직접 출제하시오.

{web_content}

[출제 지침]
- 총 {num_questions}개 / 난이도 기초~최고 혼합
- 출제 유형 힌트: {sm["mc_tips"]}
- 수집 자료에서 핵심 개념을 추출하여 새로운 문제 창작 (복사 금지)

[출력 형식 - 반드시 이 형식 그대로]

{q_blocks}"""


def build_web_sa_prompt(subject: str, grade: str, web_content: str,
                        difficulty: str = "", num_questions: int = 2) -> str:
    """웹 자료만으로 주관식 문제 생성 (통합)"""
    sm = SUBJECT_META[subject]
    q_blocks = "\n\n".join(
        f"""★ 주관식 {i}번
문제: (문제 내용 — 필요시 짧은 지문·자료 직접 창작 가능)
정답: (모범 답안)
해설: (채점 기준 포함)"""
        for i in range(1, num_questions + 1)
    )
    return f"""아래 웹 수집 교육 자료를 분석하여 {grade} 수준의 {subject} 주관식 문제 {num_questions}개를 직접 출제하시오.

{web_content}

[출제 지침]
- 총 {num_questions}개 / 난이도 기초~최고 혼합
- 출제 유형 힌트: {sm["sa_tips"]}
- 수집 자료에서 핵심 개념을 추출하여 새로운 문제 창작 (복사 금지)

[출력 형식 - 반드시 이 형식 그대로]

{q_blocks}"""


def build_web_essay_prompt(subject: str, grade: str, web_content: str,
                           difficulty: str = "", num_questions: int = 1) -> str:
    """웹 자료만으로 서술형 문제 생성 (통합)"""
    sm = SUBJECT_META[subject]
    q_blocks = "\n\n".join(
        f"""★ 서술형 {i}번
문제: (문제 내용 — 필요시 짧은 지문·자료 직접 창작 가능)
[조건]
- 조건1: (내용)
- 조건2: (내용)
모범 답안: (이상적인 답안)
채점 기준:
- (기준1): (점수)점
- (기준2): (점수)점, 총 (합계)점"""
        for i in range(1, num_questions + 1)
    )
    return f"""아래 웹 수집 교육 자료를 분석하여 {grade} 수준의 {subject} 서술형 문제 {num_questions}개를 직접 출제하시오.

{web_content}

[출제 지침]
- 총 {num_questions}개 / 난이도 기초~최고 혼합
- 출제 유형 힌트: {sm["essay_tips"]}
- 각 문제마다 조건·모범답안·채점기준 포함 (복사 금지)

[출력 형식 - 반드시 이 형식 그대로]

{q_blocks}"""


# ─── 통합 AI 호출 ─────────────────────────────────────
def clean_output(text: str) -> str:
    """★ 블록 이전의 서문/코멘트 제거 (Gemma 전용 노이즈 처리)"""
    star_idx = text.find("★")
    if star_idx > 0:
        text = text[star_idx:]
    text = re.sub(r"\*\*\[출제 의도.*?\]\*\*.*?\n\n", "", text, flags=re.DOTALL)
    return text.strip()


def call_ai(client: OpenAI, engine_info: dict,
            system_prompt: str, user_prompt: str, label: str) -> str:
    """선택된 엔진으로 AI 호출. 모든 엔진 공통."""
    with Progress(
        SpinnerColumn(),
        TextColumn(f"[cyan]생성 중: {label}  [{engine_info['model_name']}][/cyan]"),
        transient=True, console=console
    ) as progress:
        progress.add_task("", total=None)
        try:
            resp = client.chat.completions.create(
                model      = engine_info["model_id"],
                messages   = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature= engine_info["temperature"],
                max_tokens = engine_info["max_tokens"],
            )
            raw = resp.choices[0].message.content.strip()
            return clean_output(raw)
        except Exception as e:
            return f"[오류] {label} 생성 실패: {e}"


# ─── 마크다운 포매터 ──────────────────────────────────
def format_output(subject: str, grade: str, passage: str,
                  results: dict, engine_info: dict,
                  scope: str = "", web_used: bool = False,
                  academy_name: str = "미래학원",
                  show_academy: bool = True,
                  num_mc: int = 2, num_sa: int = 2, num_es: int = 1) -> str:
    now = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
    sm  = SUBJECT_META[subject]
    scope_line = f"  \n> **진도 범위:** {scope}" if scope else ""
    web_line   = "  \n> **웹 기출 참고:** ✅ 기출문제 검색 적용" if web_used else ""
    total_q = num_mc * 3 + num_sa * 3 + num_es * 3
    academy_line = f"\n# 🏫 {academy_name}\n" if show_academy else ""
    lines = [
        academy_line,
        f"# {sm['icon']} {grade} {subject} 유사변형 문제지",
        f"",
        f"> **생성일시:** {now}  ",
        f"> **과목:** {subject} | **학년:** {grade} | **총 문항:** {total_q}문항 "
        f"(객관식 {num_mc*3} · 주관식 {num_sa*3} · 서술형 {num_es*3})",
        f"> **사용 엔진:** {engine_info['group'].strip()}  →  `{engine_info['model_name']}`"
        + scope_line + web_line,
        f"",
        f"---",
        f"",
        f"## 📋 원본 지문",
        f"",
        passage.strip(),
        f"",
        f"---",
        f"",
    ]
    type_labels = {
        "mc": ("📝 객관식 문제", "5지선다형 · 각 문항 중 하나를 선택하시오"),
        "sa": ("✏️ 주관식 문제", "답을 직접 쓰시오"),
        "es": ("📄 서술형 문제", "조건에 맞게 서술하시오"),
    }
    diff_labels = {"하": "⬇ 하(기초)", "중": "➡ 중(심화)", "상": "⬆ 상(최고)"}
    for q_type, (type_title, type_desc) in type_labels.items():
        lines += [f"## {type_title}", f"*{type_desc}*", ""]
        for diff in ["하", "중", "상"]:
            key = f"{q_type}_{diff}"
            lines += [
                f"### {diff_labels[diff]}", "",
                results.get(key, "(생성 실패)"), "",
                "---", "",
            ]
    return "\n".join(lines)


# ─── 문제지 / 답지 분리 ──────────────────────────────
def _strip_answers(raw: str) -> str:
    """문제지 버전: 정답·해설·모범답안·채점기준 제거, 답란 삽입"""
    out, skip = [], False
    for line in raw.split("\n"):
        s = line.strip()
        # 새 문제 블록 → skip 리셋
        if s.startswith("★"):
            skip = False; out.append(line); continue
        # 정답 줄 → 빈칸 대체
        if s.startswith("정답:"):
            if any(c in s for c in "①②③④⑤"):
                out.append("정답: (          )")
            else:
                out.append("답: ___________________________________")
            skip = False; continue
        # 해설 → 건너뜀
        if s.startswith("해설:"):
            skip = True; continue
        # 모범 답안 → 답 작성란으로 대체
        if s.startswith("모범 답안:"):
            out.append("")
            out.append("[ 답 작성란 ]")
            out.append("")
            out.append("")
            out.append("")
            skip = True; continue
        # 채점 기준 → 건너뜀
        if s.startswith("채점 기준:"):
            skip = True; continue
        # [조건]은 유지 (문제의 일부)
        if skip and (s.startswith("[조건]") or s.startswith("- 조건")):
            skip = False
        if skip:
            continue
        out.append(line)
    return "\n".join(out)


def _extract_answers(raw: str) -> str:
    """답지 버전: 헤더 + 정답 + 해설 / 모범답안 + 채점기준만 추출"""
    out, collect, pending_header = [], False, ""
    for line in raw.split("\n"):
        s = line.strip()
        if s.startswith("★"):
            if collect:          # 이전 블록 끝 빈줄
                out.append("")
            collect = False
            pending_header = line; continue
        if s.startswith("정답:") or s.startswith("모범 답안:"):
            if not collect:
                out.append(pending_header)
                collect = True
            out.append(line); continue
        if s.startswith("해설:") or s.startswith("채점 기준:"):
            collect = True; out.append(line); continue
        if collect:
            # 멀티라인 해설 / 채점 기준 내용 유지, 빈줄 두 개 연속이면 블록 끝
            if s == "" and out and out[-1].strip() == "":
                collect = False
            else:
                out.append(line)
    return "\n".join(out)


def _make_header(title: str, subject: str, grade: str, now: str,
                 scope: str, engine_info: dict, total_q: int,
                 num_mc: int, num_sa: int, num_es: int,
                 academy_name: str, show_academy: bool,
                 web_used: bool, extra: str = "") -> list:
    sm = SUBJECT_META[subject]
    scope_line = f"  \n> **진도 범위:** {scope}" if scope else ""
    web_line   = "  \n> **웹 기출 참고:** ✅ 기출문제 검색 적용" if web_used else ""
    acad = [f"# 🏫 {academy_name}", ""] if show_academy else []
    return acad + [
        f"# {sm['icon']} {grade} {subject} {title}",
        "",
        f"> **생성일시:** {now}  ",
        f"> **과목:** {subject} | **학년:** {grade}{extra}"
        + scope_line + web_line,
        "", "---", "",
    ]


def format_exam_sheet(subject: str, grade: str, passage: str,
                      results: dict, engine_info: dict,
                      scope: str = "", web_used: bool = False,
                      academy_name: str = "미래학원", show_academy: bool = True,
                      num_mc: int = 2, num_sa: int = 2, num_es: int = 1) -> str:
    """학생용 문제지 (정답·해설 없음)"""
    now     = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
    total_q = num_mc + num_sa + num_es
    lines = _make_header("유사변형 문제지 [문제지]", subject, grade, now,
                         scope, engine_info, total_q, num_mc, num_sa, num_es,
                         academy_name, show_academy, web_used,
                         f" | **총 {total_q}문항** (객관식 {num_mc}·주관식 {num_sa}·서술형 {num_es})")
    if passage.strip():
        lines += ["## 📋 지문", "", passage.strip(), "", "---", ""]
    elif web_used:
        lines += ["## 🌐 웹 수집 자료 기반 출제",
                  "*본 문제는 인터넷 기출·교육 자료를 수집하여 AI가 직접 출제한 문제입니다.*",
                  "", "---", ""]
    type_labels = {
        "mc": ("📝 객관식", "5지선다형 — 정답 번호를 괄호 안에 쓰시오", num_mc),
        "sa": ("✏️ 주관식", "답을 빈칸에 직접 쓰시오", num_sa),
        "es": ("📄 서술형", "조건에 맞게 서술하시오", num_es),
    }
    for q_type, (title, desc, nq) in type_labels.items():
        if nq == 0:
            continue
        raw = results.get(q_type, "(생성 실패)")
        lines += [f"## {title}", f"*{desc}*", "", _strip_answers(raw), "", "---", ""]
    return "\n".join(lines)


def format_answer_sheet(subject: str, grade: str,
                        results: dict, engine_info: dict,
                        scope: str = "", web_used: bool = False,
                        academy_name: str = "미래학원", show_academy: bool = True,
                        num_mc: int = 2, num_sa: int = 2, num_es: int = 1) -> str:
    """교사용 답지 (정답·해설·모범답안·채점기준)"""
    now = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
    lines = _make_header("유사변형 문제지 [정답 및 해설지]", subject, grade, now,
                         scope, engine_info, 0, num_mc, num_sa, num_es,
                         academy_name, show_academy, web_used,
                         f" | **사용 엔진:** `{engine_info['model_name']}`")
    lines += ["> ⚠️ 이 문서는 교사용 답지입니다. 학생에게 배포하지 마세요.", "", "---", ""]
    type_labels = {
        "mc": ("📝 객관식 정답 및 해설", num_mc),
        "sa": ("✏️ 주관식 정답 및 해설", num_sa),
        "es": ("📄 서술형 모범답안 및 채점기준", num_es),
    }
    for q_type, (title, nq) in type_labels.items():
        if nq == 0:
            continue
        raw = results.get(q_type, "(생성 실패)")
        lines += [f"## {title}", "", _extract_answers(raw), "", "---", ""]
    return "\n".join(lines)


# ─── 파일 입력 파서 ───────────────────────────────────
def read_input_file(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    def extract(tag):
        m = re.search(rf"\[{tag}\]\s*\n(.*?)(?=\n\[|\Z)", text, re.DOTALL)
        return m.group(1).strip() if m else ""

    subject_raw = extract("과목") or extract("subject")
    grade_raw   = extract("학년") or extract("grade")
    passage     = extract("지문") or extract("passage")
    original_q  = extract("원본문제") or extract("original") or "원본 문제 없음"

    subject_map = {"국어":"국어","영어":"영어","수학":"수학","과학":"과학","사회":"사회","역사":"역사"}
    grade_map   = {
        "유아":"유아",
        "초1":"초등1","초등1":"초등1","초등1학년":"초등1",
        "초2":"초등2","초등2":"초등2","초등2학년":"초등2",
        "초3":"초등3","초등3":"초등3","초등3학년":"초등3",
        "초4":"초등4","초등4":"초등4","초등4학년":"초등4",
        "초5":"초등5","초등5":"초등5","초등5학년":"초등5",
        "초6":"초등6","초등6":"초등6","초등6학년":"초등6",
        "초등":"초등6",
        "중1":"중등1","중등1":"중등1","중학교1학년":"중등1","중1학년":"중등1",
        "중2":"중등2","중등2":"중등2","중학교2학년":"중등2","중2학년":"중등2",
        "중3":"중등3","중등3":"중등3","중학교3학년":"중등3","중3학년":"중등3",
        "중등":"중등2","중":"중등2",
        "고1":"고등1","고등1":"고등1","고등학교1학년":"고등1","고1학년":"고등1",
        "고2":"고등2","고등2":"고등2","고등학교2학년":"고등2","고2학년":"고등2",
        "고3":"고등3","고등3":"고등3","고등학교3학년":"고등3","고3학년":"고등3",
        "고등":"고등2","고":"고등2",
    }
    return {
        "subject"   : subject_map.get(subject_raw, "영어"),
        "grade"     : grade_map.get(grade_raw, "중등2"),
        "passage"   : passage,
        "original_q": original_q,
    }


# ─── 입력 수집 UI ─────────────────────────────────────
def collect_inputs() -> dict:
    # 과목
    subjects = list(SUBJECT_META.keys())
    table = Table(show_header=False, box=None, padding=(0,1))
    for i, s in enumerate(subjects, 1):
        table.add_row(f"[cyan]{i}.[/cyan]", f"{SUBJECT_META[s]['icon']} {s}")
    console.print(Rule("[bold]과목 선택[/bold]"))
    console.print(table)
    while True:
        c = Prompt.ask("[cyan]번호[/cyan]", default="2")
        if c.isdigit() and 1 <= int(c) <= len(subjects):
            subject = subjects[int(c) - 1]; break
        console.print("[red]1~6 중 선택하세요[/red]")

    # 학년 (그룹별 표시)
    grade_list = []  # 번호순 학년 코드 리스트
    console.print(Rule("[bold]학년 선택[/bold]"))
    for group_name, codes in GRADE_GROUPS:
        console.print(f"\n  [bold dim]── {group_name} ──[/bold dim]")
        for code in codes:
            grade_list.append(code)
            idx = len(grade_list)
            meta = GRADE_META[code]
            if group_name == "유아":
                console.print(f"  [cyan]{idx:>2}.[/cyan] {meta['range']}  "
                               "[dim](알파벳·파닉스·기초단어)[/dim]")
            else:
                console.print(f"  [cyan]{idx:>2}.[/cyan] {meta['range']}")
    total_grades = len(grade_list)
    while True:
        c = Prompt.ask(f"\n[cyan]번호[/cyan] (1~{total_grades})", default="8")
        if c.isdigit() and 1 <= int(c) <= total_grades:
            grade = grade_list[int(c) - 1]; break
        console.print(f"[red]1~{total_grades} 중 선택하세요[/red]")

    # 지문
    console.print(Rule("[bold]지문 입력[/bold]"))
    console.print("[dim]입력 후 빈 줄에서 Enter[/dim]")
    lines, empty = [], 0
    while empty < 1:
        line = input()
        if line == "": empty += 1
        else: empty = 0; lines.append(line)
    passage = "\n".join(lines).strip()

    # 원본 문제
    console.print(Rule("[bold]원본 문제 입력[/bold] [dim](없으면 Enter)[/dim]"))
    console.print("[dim]입력 후 빈 줄에서 Enter[/dim]")
    lines, empty = [], 0
    while empty < 1:
        line = input()
        if line == "": empty += 1
        else: empty = 0; lines.append(line)
    original_q = "\n".join(lines).strip() or "원본 문제 없음"

    return {"subject": subject, "grade": grade, "passage": passage, "original_q": original_q}


# ─── 웹 검색 UI ───────────────────────────────────────
def run_web_search(grade: str, subject: str) -> tuple[str, str]:
    """
    웹 기출문제 검색 옵션.
    반환: (web_ref_text, scope_str)  — 프롬프트 삽입용
    """
    if not WEB_SEARCH_AVAILABLE:
        console.print("[yellow]web_searcher.py 모듈 없음 — 웹 검색 건너뜀[/yellow]")
        return "", ""

    console.print(Rule("[bold]웹 기출문제 검색 옵션[/bold]"))

    # 검색 엔진 현황 표시
    has_google = bool(os.environ.get("GOOGLE_CSE_KEY") and os.environ.get("GOOGLE_CSE_CX"))
    has_naver  = bool(os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET"))
    engines_on = ["Naver 블로그·카페·지식iN (기본)"]
    if has_google: engines_on.append("Google CSE")
    if has_naver:  engines_on.append("Naver Open API")

    table = Table(show_header=False, box=None, padding=(0,2))
    table.add_row("[green]●[/green]", "Naver 블로그·카페·지식iN", "[green]사용 가능 (항상, 키 불필요)[/green]")
    table.add_row("[green]●[/green]", "족보닷컴 직접 검색",    "[green]사용 가능 (항상)[/green]")
    table.add_row(
        "[green]●[/green]" if has_google else "[dim]○[/dim]",
        "Google Custom Search",
        "[green]사용 가능[/green]" if has_google
        else "[dim]미설정 (GOOGLE_CSE_KEY + GOOGLE_CSE_CX)[/dim]"
    )
    table.add_row(
        "[green]●[/green]" if has_naver else "[dim]○[/dim]",
        "Naver Open API (블로그+카페+지식iN+웹)",
        "[green]사용 가능[/green]" if has_naver
        else "[dim]미설정 (NAVER_CLIENT_ID + NAVER_CLIENT_SECRET)[/dim]"
    )
    console.print(table)
    console.print(f"  활성 엔진: [cyan]{', '.join(engines_on)}[/cyan]")

    if not Confirm.ask("\n  웹에서 기출문제를 검색하여 참고자료로 활용할까요?", default=True):
        return "", ""

    # 진도 범위 입력
    console.print("\n  [bold]진도 범위 입력[/bold] [dim](예: Unit 2, 이차함수, 3·1 운동, 광합성)[/dim]")
    scope = Prompt.ask("  범위").strip()
    if not scope:
        return "", ""

    # 검색 강도 선택
    console.print("\n  검색 강도:")
    console.print("    [cyan]1.[/cyan] 빠름  (쿼리 2개 · 페이지 3개)")
    console.print("    [cyan]2.[/cyan] 보통  (쿼리 4개 · 페이지 6개)  ← 권장")
    console.print("    [cyan]3.[/cyan] 심층  (쿼리 6개 · 페이지 10개)")
    depth = Prompt.ask("  번호", default="2")
    depth_cfg = {
        "1": (2, 3, 0.8),
        "2": (4, 6, 1.0),
        "3": (6, 10, 1.2),
    }.get(depth, (4, 6, 1.0))
    max_q, max_f, delay = depth_cfg

    searcher = KoreanEduSearcher(
        max_queries=max_q, max_fetch=max_f, delay=delay
    )

    console.print(f"\n  [cyan]검색 시작:[/cyan] {grade} {subject} — {scope}")

    with Progress(SpinnerColumn(),
                  TextColumn("[cyan]{task.description}[/cyan]"),
                  transient=True, console=console) as progress:
        task = progress.add_task("초기화...", total=None)

        def cb(msg):
            progress.update(task, description=msg)

        results, questions = searcher.run(grade, subject, scope, progress_cb=cb)

    if not results and not questions:
        console.print("  [yellow]검색 결과가 없습니다. 지문만으로 문제를 생성합니다.[/yellow]")
        return "", scope

    # 결과 요약 출력
    console.print(f"\n  [green]✓[/green] 검색 결과: [bold]{len(results)}[/bold]건  "
                  f"/ 문제 패턴 추출: [bold]{len(questions)}[/bold]개\n")

    if questions:
        console.print("  [dim]── 추출된 문제 샘플 (상위 3개) ──[/dim]")
        for i, q in enumerate(questions[:3], 1):
            console.print(f"  [dim]{i}. {q.question[:100].strip()}...[/dim]")
        console.print()

    # 검색 결과 저장 (선택)
    if Confirm.ask("  검색 결과를 별도 파일로 저장할까요?", default=False):
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = OUTPUT_DIR / f"web_search_{grade}_{subject}_{ts}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# 웹 기출문제 검색 결과\n")
            f.write(f"# {grade} {subject} — {scope}\n")
            f.write(f"# 생성: {datetime.now()}\n\n")
            f.write(f"## 검색된 URL ({len(results)}건)\n")
            for r in results:
                f.write(f"- [{r.title}]({r.url})  [{r.source}]\n  {r.snippet[:100]}\n\n")
            f.write(f"\n## 추출된 문제 패턴 ({len(questions)}개)\n")
            for i, q in enumerate(questions, 1):
                f.write(f"\n[{i}] (출처: {q.context})\n{q.question}\n")
        console.print(f"  [cyan]저장 →[/cyan] {path}")

    web_ref = format_for_prompt(results, questions, max_chars=2500)
    return web_ref, scope


# ─── 공통 문제 생성 실행 ──────────────────────────────
TASKS = [
    ("mc","하","객관식 하(기초)",  build_mc_prompt),
    ("mc","중","객관식 중(심화)",  build_mc_prompt),
    ("mc","상","객관식 상(최고)",  build_mc_prompt),
    ("sa","하","주관식 하(기초)",  build_sa_prompt),
    ("sa","중","주관식 중(심화)",  build_sa_prompt),
    ("sa","상","주관식 상(최고)",  build_sa_prompt),
    ("es","하","서술형 하(기초)",  build_essay_prompt),
    ("es","중","서술형 중(심화)",  build_essay_prompt),
    ("es","상","서술형 상(최고)",  build_essay_prompt),
]

def run_generation(client: OpenAI, engine_info: dict,
                   subject: str, grade: str, passage: str, original: str,
                   web_ref: str = "") -> dict:
    sys_prompt = build_system_prompt(subject, grade)
    results = {}
    for q_type, diff, label, prompt_fn in TASKS:
        result = call_ai(client, engine_info, sys_prompt,
                         prompt_fn(subject, grade, passage, original, diff,
                                   web_ref=web_ref), label)
        results[f"{q_type}_{diff}"] = result
        console.print(f"  [green]✓[/green] {label} 완료")
    return results


def save_and_print(subject: str, grade: str, passage: str,
                   results: dict, engine_info: dict,
                   scope: str = "", web_used: bool = False) -> tuple:
    markdown = format_output(subject, grade, passage, results, engine_info,
                             scope=scope, web_used=web_used)
    ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
    engine_tag = engine_info["provider"]
    scope_tag  = f"_{scope.replace(' ','_')}" if scope else ""
    filename   = OUTPUT_DIR / f"{grade}_{subject}{scope_tag}_{engine_tag}_{ts}.md"
    filename.write_text(markdown, encoding="utf-8")
    return filename, markdown


# ─── 메인 ────────────────────────────────────────────
def main():
    console.print(Panel.fit(
        "[bold cyan]초중고 유사변형 문제 자동 제작 시스템  v2.0[/bold cyan]\n"
        "[dim]Multi-Engine: Claude · Gemini · Gemma | 30년 경력 입시 전문 AI[/dim]",
        border_style="cyan"
    ))

    # 1) 엔진 선택
    engine_info = select_engine()
    client      = build_ai_client(engine_info)

    # 2) 입력 수집
    inputs   = collect_inputs()
    subject  = inputs["subject"]
    grade    = inputs["grade"]
    passage  = inputs["passage"]
    original = inputs["original_q"]

    if not passage:
        console.print("[red]지문을 입력해야 합니다.[/red]")
        sys.exit(1)

    # 3) 웹 기출 검색 (선택)
    web_ref, scope = run_web_search(grade, subject)
    web_used = bool(web_ref)

    # 4) 생성
    console.print(f"\n[bold green]✦ {grade} {subject} 문제 생성 시작[/bold green]  "
                  f"[dim]엔진: {engine_info['model_name']}[/dim]"
                  + (f"  [cyan]+웹 기출 참고[/cyan]" if web_used else "") + "\n")
    results = run_generation(client, engine_info, subject, grade, passage, original,
                             web_ref=web_ref)

    # 5) 저장
    filename, markdown = save_and_print(subject, grade, passage, results, engine_info,
                                        scope=scope, web_used=web_used)
    console.print(f"\n[bold green]✦ 생성 완료![/bold green]  →  [cyan]{filename}[/cyan]")

    # 5) 미리보기
    if Confirm.ask("\n화면에서 미리 볼까요?", default=True):
        console.print("\n" + "─" * 70)
        console.print(markdown)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # ── 파일 입력 모드: python exam_generator.py input.txt ──
        input_file = sys.argv[1]
        if not Path(input_file).exists():
            console.print(f"[red]파일 없음: {input_file}[/red]"); sys.exit(1)

        console.print(Panel.fit(
            "[bold cyan]초중고 유사변형 문제 자동 제작 시스템  v2.0[/bold cyan]\n"
            "[dim]파일 입력 모드[/dim]",
            border_style="cyan"
        ))

        # 엔진 선택 (파일 모드도 동일)
        engine_info = select_engine()
        client      = build_ai_client(engine_info)

        inputs   = read_input_file(input_file)
        subject  = inputs["subject"]
        grade    = inputs["grade"]
        passage  = inputs["passage"]
        original = inputs["original_q"]

        console.print(f"\n  과목: [yellow]{subject}[/yellow] | 학년: [yellow]{grade}[/yellow]")

        # 웹 기출 검색 (선택)
        web_ref, scope = run_web_search(grade, subject)
        web_used = bool(web_ref)

        console.print(f"[bold green]✦ 문제 생성 시작[/bold green]  "
                      f"[dim]엔진: {engine_info['model_name']}[/dim]"
                      + (f"  [cyan]+웹 기출 참고[/cyan]" if web_used else "") + "\n")

        results  = run_generation(client, engine_info, subject, grade, passage, original,
                                  web_ref=web_ref)
        filename, _ = save_and_print(subject, grade, passage, results, engine_info,
                                     scope=scope, web_used=web_used)
        console.print(f"\n[bold green]✦ 완료![/bold green]  →  [cyan]{filename}[/cyan]")
    else:
        main()
