#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  web_searcher.py  v2.0
  기출문제 웹 검색 모듈  (API 키 없이 동작)

  검색 채널:
  ├─ Naver 블로그  (네이버 검색 직접 스크래핑)
  ├─ Naver 카페    (네이버 검색 직접 스크래핑)
  ├─ Naver 지식iN  (네이버 검색 직접 스크래핑)
  ├─ Naver 웹문서  (네이버 검색 직접 스크래핑)
  ├─ Google 검색   (googlesearch-python / Naver 폴백)
  ├─ 족보닷컴      (직접 URL 스크래핑)
  └─ Naver API     (선택 · NAVER_CLIENT_ID/SECRET 설정 시)

  API 키 선택 추가:
  ├─ Google CSE    (GOOGLE_CSE_KEY + GOOGLE_CSE_CX)
  └─ Naver Open API (NAVER_CLIENT_ID + NAVER_CLIENT_SECRET)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import re
import time
import warnings
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, quote
from dataclasses import dataclass, field
from typing import List, Optional, Callable

warnings.filterwarnings("ignore")   # urllib3 LibreSSL 경고 억제

# ─── HTTP 공통 헤더 ──────────────────────────────────
HEADERS = {
    "User-Agent"     : "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept"         : "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer"        : "https://www.naver.com",
}

# ─── 데이터 클래스 ────────────────────────────────────
@dataclass
class SearchResult:
    title  : str
    url    : str
    snippet: str
    source : str   # 출처 태그 (naver_blog / naver_cafe / google / zocbo 등)
    content: str = ""   # 페이지 본문 (fetch 후 채움)

@dataclass
class ExtractedQA:
    question: str
    context : str = ""
    score   : int = 0

# ─── 우선 도메인 / 차단 도메인 ───────────────────────
PRIORITY_DOMAINS = [
    "zocbo.com",       # 족보닷컴
    "blog.naver.com",  # 네이버 블로그
    "cafe.naver.com",  # 네이버 카페
    "kin.naver.com",   # 지식iN
    "tistory.com",     # 티스토리 (교사 블로그)
    "ebs.co.kr", "ebsi.co.kr",
    "edunet.net",
    "mathpang.com",
    "visang.com", "chunjae.co.kr", "mirae-n.com",
    "etoos.com", "megastudy.net",
    "exam4u.net",
]
BLOCKED_DOMAINS = [
    "youtube.com", "youtu.be", "instagram.com", "facebook.com",
    "twitter.com", "tiktok.com", "namu.wiki",
    "news.naver.com", "news.daum.net",
    "coupang.com", "11st.co.kr",
    "schoolinfo.go.kr", "neis.go.kr",
    "antworker.tistory.com",   # 학교순위 (무관)
    "prompie.com",
]
# 제목+스니펫에 이 단어가 있어야 교육 관련으로 간주
EDU_KEYWORDS = [
    "기출", "문제", "시험", "단원평가", "예상", "변형",
    "학습지", "풀이", "정답", "해설", "서술형", "객관식",
    "내신", "교과서", "수능", "모의고사",
]

# 추출된 문제 텍스트에 이 단어 중 하나 이상 포함돼야 실제 문제로 간주
Q_EDU_KEYWORDS = [
    "문제", "정답", "보기", "빈칸", "어법", "서술", "밑줄",
    "다음", "글", "지문", "영어", "수학", "국어", "과학", "사회", "역사",
    "단어", "문장", "어휘", "문법", "이유", "특징", "설명",
    "풀이", "해설", "쓰시오", "고르시오", "답하시오",
    "①", "②", "③", "④", "⑤",
    "what", "why", "how", "which", "when", "where",   # 영어 문제
]

# 비교육 컨텍스트 차단 키워드 (본문 전체에서 이 단어가 많으면 건너뜀)
NON_EDU_CONTEXT_WORDS = [
    "엘리베이터", "주민", "관리비", "경비", "아파트", "입대의",
    "층", "세대", "주차", "분리수거", "택배",
]

def _is_blocked(url: str) -> bool:
    d = urlparse(url).netloc.lower()
    return any(b in d for b in BLOCKED_DOMAINS)

def _priority(url: str) -> int:
    d = urlparse(url).netloc.lower()
    for p in PRIORITY_DOMAINS:
        if p in d:
            return 3
    return 1

def _is_edu_relevant(r: SearchResult) -> bool:
    text = (r.title + " " + r.snippet).lower()
    return any(kw in text for kw in EDU_KEYWORDS)


# ═══════════════════════════════════════════════════
#  검색 쿼리 생성기
# ═══════════════════════════════════════════════════
def build_queries(grade: str, subject: str, scope: str) -> dict:
    """
    채널별로 최적화된 검색 쿼리 세트를 반환.
    반환: { 'common': [...], 'blog': [...], 'cafe': [...], 'google': [...] }
    grade: '초등1'~'초등6', '중등1'~'중등3', '고등1'~'고등3', '유아'
    """
    # 학년 코드 → 검색어 변환
    if grade == "유아":
        gk = "유아"; ga = "유아"
    elif grade.startswith("초등"):
        n = grade[2:]   # "1"~"6"
        gk = f"초등학교 {n}학년"; ga = f"초등 {n}학년"
    elif grade.startswith("중등"):
        n = grade[2:]
        gk = f"중학교 {n}학년"; ga = f"중 {n}학년"
    elif grade.startswith("고등"):
        n = grade[2:]
        gk = f"고등학교 {n}학년"; ga = f"고 {n}학년"
    else:  # 구버전 호환
        gk = {"초등": "초등학교", "중등": "중학교", "고등": "고등학교"}.get(grade, "중학교")
        ga = {"초등": "초등",     "중등": "중학교",  "고등": "고등"}.get(grade, "중학교")

    pubs = {
        "영어": ["미래엔", "YBM", "천재교육", "비상"],
        "국어": ["미래엔", "천재교육", "비상", "동아"],
        "수학": ["미래엔", "천재교육", "비상"],
    }.get(subject, [])

    # ── 공통 쿼리 (모든 채널에 적용)
    common = [
        f"{gk} {subject} {scope} 기출문제",
        f"{gk} {subject} {scope} 변형문제 정답",
        f"{gk} {subject} {scope} 단원평가 시험문제",
        f"{ga} {subject} {scope} 예상문제 서술형",
    ]

    # ── 블로그 특화 (교사·학원 강사 자료)
    blog = [
        f"{gk} {subject} {scope} 기출 문제 풀이 블로그",
        f"{gk} {subject} {scope} 시험 대비 학습자료",
    ]
    for pub in pubs[:2]:
        blog.append(f"{pub} {gk} {subject} {scope} 기출")

    # ── 카페 특화 (학부모·학생 커뮤니티)
    cafe = [
        f"{gk} {subject} {scope} 기출 공유 카페",
        f"{ga} {subject} {scope} 내신 대비 공유",
        f"{gk} {subject} {scope} 시험지 공유",
    ]

    # ── Google 특화 (site 연산자 활용)
    google = [
        f"site:blog.naver.com {gk} {subject} {scope} 기출문제",
        f"site:cafe.naver.com {gk} {subject} {scope} 변형문제",
        f"site:tistory.com {ga} {subject} {scope} 기출 문제",
        f"{gk} {subject} {scope} 기출 filetype:pdf",
    ]

    # 학교급별 추가 (세분화)
    if grade == "유아":
        common += [f"유아 {subject} {scope} 학습지", f"유아 영어 {scope} 워크시트"]
        blog   += [f"유아 {subject} 파닉스 {scope} 활동지"]
    elif grade.startswith("초등"):
        common += [f"{gk} {subject} {scope} 단원평가", f"{gk} {subject} {scope} 학습지"]
        cafe   += [f"{gk} {subject} {scope} 단원평가 공유"]
    elif grade.startswith("중등"):
        common += [f"{gk} 중간고사 기말고사 {subject} {scope} 기출"]
        cafe   += [f"{gk} {subject} {scope} 기말 기출 공유"]
    elif grade.startswith("고등"):
        common += [f"수능 {subject} {scope} 기출", f"교육청 {ga} 모의고사 {subject} {scope}"]
        google += [f"site:blog.naver.com 수능 {subject} {scope} 해설"]
    # 구버전 호환
    elif grade == "고등":
        common += [f"수능 {subject} {scope} 기출", f"교육청 모의고사 {subject} {scope}"]
        google += [f"site:blog.naver.com 수능 {subject} {scope} 해설"]
    elif grade == "중등":
        common += [f"중간고사 기말고사 {subject} {scope} 기출"]
        cafe   += [f"{gk} {subject} {scope} 기말 기출 공유"]
    else:
        common += [f"초등 {subject} {scope} 단원평가 학습지"]

    return {"common": common, "blog": blog, "cafe": cafe, "google": google}


# ═══════════════════════════════════════════════════
#  네이버 검색 직접 스크래핑 (API 키 불필요)
# ═══════════════════════════════════════════════════
_NAVER_CHAN = {
    "blog"  : "네이버 블로그",
    "cafe"  : "네이버 카페",
    "kin"   : "네이버 지식iN",
    "webkr" : "네이버 웹",
}

def _naver_scrape(query: str, where: str,
                  max_results: int = 8) -> List[SearchResult]:
    """
    네이버 검색 결과 페이지 직접 파싱.
    전략:
      1) data-url 속성 → 실제 콘텐츠 URL 수집 (추적 URL 우회)
      2) blog.naver.com / cafe.naver.com 등 직접 href + 의미 있는 title
    """
    search_url = (f"https://search.naver.com/search.naver"
                  f"?where={where}&query={quote(query)}&sm=top_hty&display=15")
    try:
        resp = requests.get(search_url, headers=HEADERS, timeout=12)
        if not resp.ok:
            return []
        soup    = BeautifulSoup(resp.text, "lxml")
        src_tag = f"naver_{where}"

        # ── Step 1: data-url → 실제 URL 목록 수집
        data_urls: set = set()
        for tag in soup.find_all(attrs={"data-url": True}):
            u = tag.get("data-url", "")
            if u and u.startswith("http") and not _is_blocked(u):
                data_urls.add(u)

        # ── Step 2: 의미 있는 <a> 태그에서 제목+URL+스니펫 추출
        seen: set = set()
        results: List[SearchResult] = []

        # edu 도메인 포함 href + 충분한 텍스트
        edu_domains = ["blog.naver.com", "cafe.naver.com", "kin.naver.com",
                       "post.naver.com", "tistory.com", "ebs.co.kr",
                       "zocbo.com", "mathpang.com"]

        for a in soup.find_all("a", href=True):
            href  = a.get("href", "")
            title = a.get_text(strip=True)

            # URL 유효성
            if not href.startswith("http") or _is_blocked(href):
                continue
            # 교육 도메인 또는 data-url 목록에 있는 URL
            is_edu_domain = any(d in href for d in edu_domains)
            is_data_url   = href in data_urls
            if not (is_edu_domain or is_data_url):
                continue
            # 의미있는 제목
            if len(title) < 6:
                continue
            # 중복
            base_url = href.split("?")[0]
            if base_url in seen:
                continue
            seen.add(base_url)

            # 스니펫: 부모 컨테이너 텍스트
            container = a.parent
            for _ in range(3):
                if container and len(container.get_text(strip=True)) > 30:
                    break
                container = container.parent if container else None
            snippet = (container.get_text(separator=" ", strip=True)[:250]
                       if container else "")

            results.append(SearchResult(
                title  = title[:120],
                url    = href,
                snippet= snippet,
                source = src_tag,
            ))
            if len(results) >= max_results * 3:
                break

        # ── Step 3: data-url 중 아직 못 담은 URL 추가 (제목은 URL에서 추론)
        for u in data_urls:
            base_url = u.split("?")[0]
            if base_url in seen:
                continue
            seen.add(base_url)
            # URL에서 제목 힌트 추출
            path_hint = u.replace("https://", "").replace("http://", "")
            results.append(SearchResult(
                title  = path_hint[:80],
                url    = u,
                snippet= "",
                source = src_tag,
            ))

        # 교육 관련 필터 (소프트: 없으면 전체 허용)
        edu   = [r for r in results if _is_edu_relevant(r)]
        final = edu if edu else results
        return final[:max_results]

    except Exception:
        return []


def search_naver_blog(query: str, max_results: int = 8) -> List[SearchResult]:
    return _naver_scrape(query, "blog", max_results)

def search_naver_cafe(query: str, max_results: int = 8) -> List[SearchResult]:
    return _naver_scrape(query, "cafe", max_results)

def search_naver_kin(query: str, max_results: int = 5) -> List[SearchResult]:
    return _naver_scrape(query, "kin", max_results)

def search_naver_web(query: str, max_results: int = 8) -> List[SearchResult]:
    return _naver_scrape(query, "webkr", max_results)


# ═══════════════════════════════════════════════════
#  Naver Open API (선택, API 키 있을 때)
# ═══════════════════════════════════════════════════
def _naver_api(endpoint: str, query: str, max_results: int,
               source_tag: str) -> List[SearchResult]:
    cid = os.environ.get("NAVER_CLIENT_ID", "")
    cs  = os.environ.get("NAVER_CLIENT_SECRET", "")
    if not cid or not cs:
        return []
    try:
        hdr = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": cs}
        params = {"query": query, "display": max_results, "sort": "sim"}
        resp = requests.get(endpoint, headers=hdr, params=params, timeout=8)
        if not resp.ok:
            return []
        return [
            SearchResult(
                title  = re.sub(r"<[^>]+>", "", i.get("title", "")),
                url    = i.get("link", "") or i.get("url", ""),
                snippet= re.sub(r"<[^>]+>", "", i.get("description", "")),
                source = source_tag,
            )
            for i in resp.json().get("items", [])
        ]
    except Exception:
        return []

def search_naver_api_all(query: str, max_each: int = 5) -> List[SearchResult]:
    """Naver API 4채널 통합 (블로그+카페+지식iN+웹)"""
    base = "https://openapi.naver.com/v1/search"
    results = []
    results += _naver_api(f"{base}/blog.json",        query, max_each, "naver_blog_api")
    results += _naver_api(f"{base}/cafearticle.json", query, max_each, "naver_cafe_api")
    results += _naver_api(f"{base}/kin.json",         query, max_each, "naver_kin_api")
    results += _naver_api(f"{base}/webkr.json",       query, max_each, "naver_web_api")
    return results


# ═══════════════════════════════════════════════════
#  Google 검색 (googlesearch-python)
# ═══════════════════════════════════════════════════
def search_google_free(query: str, max_results: int = 8) -> List[SearchResult]:
    """googlesearch-python 라이브러리 활용 (비공식, 무료)"""
    try:
        from googlesearch import search as gsearch
        results = []
        for url in gsearch(query, num_results=max_results, lang="ko",
                           sleep_interval=1):
            if not _is_blocked(url):
                results.append(SearchResult(
                    title  = url.split("/")[-1][:60] or url,
                    url    = url,
                    snippet= "",
                    source = "google",
                ))
        return results
    except Exception:
        return []


def search_google_cse(query: str, max_results: int = 8) -> List[SearchResult]:
    """Google Custom Search API (GOOGLE_CSE_KEY + GOOGLE_CSE_CX 필요)"""
    key = os.environ.get("GOOGLE_CSE_KEY", "")
    cx  = os.environ.get("GOOGLE_CSE_CX", "")
    if not key or not cx:
        return []
    try:
        params = {"key": key, "cx": cx, "q": query,
                  "num": min(max_results, 10), "lr": "lang_ko"}
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params, timeout=10
        )
        resp.raise_for_status()
        return [
            SearchResult(
                title  = i.get("title", ""),
                url    = i.get("link", ""),
                snippet= i.get("snippet", ""),
                source = "google_cse",
            )
            for i in resp.json().get("items", [])
        ]
    except Exception:
        return []


# ═══════════════════════════════════════════════════
#  족보닷컴 직접 검색
# ═══════════════════════════════════════════════════
def search_zocbo(grade: str, subject: str, scope: str,
                 max_results: int = 6) -> List[SearchResult]:
    """족보닷컴 검색 페이지 스크래핑"""
    results = []
    try:
        query = f"{subject} {scope}"
        url   = f"https://www.zocbo.com/search/search.do?keyword={quote(query)}"
        resp  = requests.get(url, headers=HEADERS, timeout=10)
        if not resp.ok:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.find_all("a", href=True):
            href  = a.get("href", "")
            title = a.get_text(strip=True)
            if not title or len(title) < 4:
                continue
            if not href.startswith("http"):
                href = "https://www.zocbo.com" + href
            if "zocbo.com" not in href:
                continue
            results.append(SearchResult(
                title=title[:100], url=href, snippet="족보닷컴 기출문제",
                source="zocbo"
            ))
            if len(results) >= max_results:
                break
    except Exception:
        pass
    return results


# ═══════════════════════════════════════════════════
#  페이지 본문 추출
# ═══════════════════════════════════════════════════
def _convert_naver_url(url: str) -> str:
    """네이버 블로그 URL을 모바일/API 버전으로 변환 (스크래핑 성공률 향상)"""
    # blog.naver.com/ID/NO → m.blog.naver.com/PostView.naver?blogId=ID&logNo=NO
    m = re.match(r"https?://blog\.naver\.com/([^/]+)/(\d+)", url)
    if m:
        return (f"https://m.blog.naver.com/PostView.naver"
                f"?blogId={m.group(1)}&logNo={m.group(2)}&isInf=true")
    return url


def fetch_page(url: str, timeout: int = 10) -> str:
    """URL 페이지 본문 텍스트 추출. 실패 시 빈 문자열."""
    # 네이버 블로그 URL 변환
    if "blog.naver.com" in url:
        url = _convert_naver_url(url)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout,
                            allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")

        # 불필요 태그 제거
        for tag in soup(["script", "style", "nav", "footer",
                         "header", "aside", "iframe", "noscript"]):
            tag.decompose()

        # 본문 우선 셀렉터 (네이버 블로그 → 티스토리 → 일반)
        for sel in [
            ".se-main-container",    # 네이버 블로그 스마트에디터 3.0
            ".se-section",           # 네이버 블로그 섹션
            "#postViewArea",         # 네이버 블로그 구버전
            ".post_ct",              # 네이버 블로그 (일부)
            "#viewTypeSelector",     # 네이버 m.blog
            ".wrap_body",            # 네이버 m.blog 래퍼
            ".entry-content",        # 티스토리 일반
            ".contents_style",       # 티스토리 스킨
            ".article-content",      # 티스토리 스킨2
            ".tt_article_useless_p_margin",  # 티스토리 구형
            "article", "main",
            ".content", "#content",
            ".question", ".problem",
            "section", "body",
        ]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator="\n", strip=True)
                if len(text) > 150:
                    return text[:6000]
        return soup.get_text(separator="\n", strip=True)[:6000]
    except Exception:
        return ""


# ═══════════════════════════════════════════════════
#  문제 패턴 추출기
# ═══════════════════════════════════════════════════
Q_PATTERNS = [
    # 번호형: 1. / 1) / 【1】 / [1]
    r"(?:^|\n)\s*(?:\d{1,2}[\.\)】]|【\d{1,2}】|\[\d{1,2}\])\s*.{10,200}(?:\n.{0,150}){0,5}",
    # 문X형
    r"문\s*\d+[\.\)]\s*.{10,200}(?:\n.{0,150}){0,4}",
    # 다음~에 답하시오 패턴
    r"다음\s+(?:글|문장|지문|자료|표|그림).{10,200}(?:\n.{0,150}){0,6}",
    # 빈칸 채우기
    r".{10,100}(?:______|　　　　|\(\s*\)|\[　\]).{5,100}",
    # 질문형
    r"(?:^|\n).{5,50}(?:이유|원인|의미|특징|차이|공통점).{0,50}[?？]",
]

def _is_edu_question(text: str) -> bool:
    """추출된 텍스트가 실제 교육용 문제인지 판별"""
    tl = text.lower()
    has_edu = any(kw in tl for kw in Q_EDU_KEYWORDS)
    if not has_edu:
        return False
    # 비교육 컨텍스트 단어가 2개 이상이면 제외 (아파트 카페 등)
    non_edu_count = sum(1 for kw in NON_EDU_CONTEXT_WORDS if kw in tl)
    return non_edu_count < 2


def extract_questions(text: str, source_url: str = "") -> List[str]:
    # 페이지 자체가 비교육적 컨텍스트면 전체 건너뜀
    non_edu_page = sum(1 for kw in NON_EDU_CONTEXT_WORDS if kw in text)
    if non_edu_page >= 3:
        return []

    candidates = []
    for pat in Q_PATTERNS:
        for m in re.finditer(pat, text, re.MULTILINE):
            chunk = m.group(0).strip()
            if len(chunk) >= 25 and chunk not in candidates:
                if _is_edu_question(chunk):
                    candidates.append(chunk)
    return candidates[:25]


# ═══════════════════════════════════════════════════
#  통합 검색 엔진
# ═══════════════════════════════════════════════════
class KoreanEduSearcher:
    """
    Naver(블로그·카페·지식iN·웹) + Google + 족보닷컴 통합 기출문제 검색기
    API 키 없이도 동작 / 키 설정 시 품질 향상
    """

    def __init__(self, max_queries: int = 4, max_fetch: int = 6,
                 delay: float = 1.0):
        self.max_queries = max_queries
        self.max_fetch   = max_fetch
        self.delay       = delay
        # Naver API 가용 여부
        self.has_naver_api = bool(
            os.environ.get("NAVER_CLIENT_ID") and
            os.environ.get("NAVER_CLIENT_SECRET")
        )
        self.has_google_cse = bool(
            os.environ.get("GOOGLE_CSE_KEY") and
            os.environ.get("GOOGLE_CSE_CX")
        )

    def _add(self, results: List[SearchResult], seen: set,
             new: List[SearchResult], limit: int = 999):
        for r in new:
            if r.url and r.url not in seen and not _is_blocked(r.url):
                seen.add(r.url)
                results.append(r)
                if len(results) >= limit:
                    break

    def search_all(self, grade: str, subject: str, scope: str,
                   progress_cb: Optional[Callable] = None) -> List[SearchResult]:
        queries   = build_queries(grade, subject, scope)
        common_qs = queries["common"][:self.max_queries]
        blog_qs   = queries["blog"][:2]
        cafe_qs   = queries["cafe"][:2]
        google_qs = queries["google"][:3]

        results: List[SearchResult] = []
        seen:    set = set()

        def _cb(msg):
            if progress_cb: progress_cb(msg)

        # ── 족보닷컴 직접 검색
        _cb("🔍 족보닷컴 직접 검색...")
        self._add(results, seen, search_zocbo(grade, subject, scope, 6))
        time.sleep(self.delay * 0.5)

        # ── Naver 공통 쿼리
        for i, q in enumerate(common_qs):
            _cb(f"📰 Naver 블로그 ({i+1}/{len(common_qs)}): {q[:45]}")
            self._add(results, seen, search_naver_blog(q, 8))
            time.sleep(self.delay)

            _cb(f"☕ Naver 카페 ({i+1}/{len(common_qs)}): {q[:45]}")
            self._add(results, seen, search_naver_cafe(q, 6))
            time.sleep(self.delay)

        # ── Naver 지식iN
        _cb(f"💡 Naver 지식iN: {common_qs[0][:45]}")
        self._add(results, seen, search_naver_kin(common_qs[0], 5))
        time.sleep(self.delay)

        # ── Naver 웹문서
        _cb(f"🌐 Naver 웹문서: {common_qs[0][:45]}")
        self._add(results, seen, search_naver_web(common_qs[0], 6))
        time.sleep(self.delay)

        # ── 블로그 특화 쿼리
        for q in blog_qs:
            _cb(f"✍️  블로그 특화: {q[:45]}")
            self._add(results, seen, search_naver_blog(q, 6))
            time.sleep(self.delay)

        # ── 카페 특화 쿼리
        for q in cafe_qs:
            _cb(f"🏘️  카페 특화: {q[:45]}")
            self._add(results, seen, search_naver_cafe(q, 5))
            time.sleep(self.delay)

        # ── Google (CSE 우선 → 무료 라이브러리 폴백)
        for q in google_qs[:2]:
            _cb(f"🔎 Google: {q[:45]}")
            if self.has_google_cse:
                self._add(results, seen, search_google_cse(q, 6))
            else:
                self._add(results, seen, search_google_free(q, 5))
            time.sleep(self.delay * 1.5)   # Google은 딜레이 더 줌

        # ── Naver Open API (있을 때)
        if self.has_naver_api:
            _cb("🔑 Naver API (블로그+카페+지식iN+웹 통합)...")
            for q in common_qs[:2]:
                self._add(results, seen, search_naver_api_all(q, max_each=4))
                time.sleep(self.delay)

        # 우선 도메인 정렬
        results.sort(key=lambda r: _priority(r.url), reverse=True)
        return results

    def fetch_top(self, results: List[SearchResult],
                  progress_cb: Optional[Callable] = None) -> List[SearchResult]:
        """교육 관련 도메인 우선, 비교육 URL 건너뛰며 페이지 본문 fetch"""
        fetched = 0
        for r in results:
            if fetched >= self.max_fetch:
                break
            # 제목+스니펫이 교육 관련이 아니면 fetch 스킵 (비용 절약)
            if not _is_edu_relevant(r):
                continue
            if progress_cb:
                progress_cb(f"📄 페이지 읽는 중: {r.url[:60]}")
            r.content = fetch_page(r.url)
            if r.content:
                fetched += 1
            time.sleep(self.delay * 0.5)
        return results

    def collect_questions(self, results: List[SearchResult]) -> List[ExtractedQA]:
        """fetch된 페이지 + 스니펫에서 문제 후보 추출 (교육 내용만)"""
        extracted = []
        for r in results:
            text = r.content or r.snippet
            if not text:
                continue
            for q in extract_questions(text, source_url=r.url):
                extracted.append(ExtractedQA(
                    question=q,
                    context =r.url,
                    score   = _priority(r.url) + (2 if r.content else 0),
                ))
        # 중복 제거 + 점수 정렬
        seen, unique = set(), []
        for e in sorted(extracted, key=lambda x: x.score, reverse=True):
            key = e.question[:50]
            if key not in seen:
                seen.add(key); unique.append(e)
        return unique[:30]

    def run(self, grade: str, subject: str, scope: str,
            progress_cb: Optional[Callable] = None):
        results   = self.search_all(grade, subject, scope, progress_cb)
        if not results:
            return [], []
        results   = self.fetch_top(results, progress_cb)
        questions = self.collect_questions(results)
        return results, questions


# ═══════════════════════════════════════════════════
#  프롬프트용 텍스트 생성
# ═══════════════════════════════════════════════════
def format_for_prompt(results: List[SearchResult],
                      questions: List[ExtractedQA],
                      max_chars: int = 3000) -> str:
    """검색 결과 → AI 프롬프트 삽입 참고 텍스트 (지문+웹 혼합 모드용)"""
    src_counts = {}
    for r in results:
        src_counts[r.source] = src_counts.get(r.source, 0) + 1

    parts = [
        "[인터넷 수집 기출/변형문제 참고자료]",
        f"(수집 출처: {', '.join(f'{s}:{n}건' for s,n in src_counts.items())})",
        "아래는 실제 웹에서 수집된 관련 기출문제 및 변형문제 정보입니다.",
        "이 자료의 유형, 난이도, 출제 패턴을 참고하여 고품질 유사문제를 제작하세요.",
        "",
    ]
    total = sum(len(p) for p in parts)

    if questions:
        parts.append("【 추출된 기출 문제 패턴 】")
        for i, q in enumerate(questions, 1):
            line = f"\n[기출{i}] {q.question.strip()[:280]}"
            if total + len(line) > max_chars * 0.75:
                break
            parts.append(line)
            total += len(line)
        parts.append("")

    if results:
        parts.append("【 관련 사이트·자료 제목 】")
        edu = [r for r in results if _is_edu_relevant(r)]
        show = edu if edu else results
        for r in show[:12]:
            line = f"• [{r.source}] {r.title[:60]}"
            if r.snippet:
                line += f"  ▷ {r.snippet[:100]}"
            if total + len(line) > max_chars:
                break
            parts.append(line)
            total += len(line)

    if not results and not questions:
        parts.append("(검색 결과 없음 — 지문과 원본 문제만으로 생성합니다)")

    return "\n".join(parts)


def format_as_rich_content(results: List[SearchResult],
                           questions: List[ExtractedQA],
                           grade: str, subject: str, scope: str,
                           max_chars: int = 7000) -> str:
    """
    웹 검색 전용 모드: 수집한 내용 전체를 풍부한 교육 자료로 구성.
    AI가 이 내용만으로 고품질 문제를 출제할 수 있도록 최대한 풍부하게 제공.
    """
    src_counts = {}
    for r in results:
        src_counts[r.source] = src_counts.get(r.source, 0) + 1

    header = [
        f"【 웹 수집 교육 자료 — {grade} {subject} / 범위: {scope or '전범위'} 】",
        f"(검색 출처: {len(results)}건 수집 / "
        f"{', '.join(f'{s} {n}건' for s, n in src_counts.items())})",
        "※ 아래 자료는 실제 교육 현장·기출 사이트·교사 블로그에서 수집한 내용입니다.",
        "   이 자료를 분석하여 해당 학년 수준에 맞는 고품질 시험 문제를 직접 출제하세요.",
        "",
    ]
    parts = header[:]
    total = sum(len(p) for p in parts)

    # ── 1) 실제 페이지 본문이 있는 것 우선 (교육 도메인 + 본문 길이순)
    fetched = [(r, r.content) for r in results if r.content and len(r.content) > 200]
    fetched.sort(key=lambda x: (_priority(x[0].url), len(x[1])), reverse=True)

    if fetched:
        parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        parts.append("【 수집된 교육 자료 본문 】")
        parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for idx, (r, content) in enumerate(fetched[:5], 1):
            # 본문을 줄별로 정리: 짧은 줄(메뉴·버튼 등) 제거, 교육 관련 내용만
            lines = [ln.strip() for ln in content.split("\n") if len(ln.strip()) > 20]
            clean = "\n".join(lines[:80])  # 최대 80줄
            # 글자 수 제한
            budget = min(1200, (max_chars - total) // max(1, len(fetched) - idx + 1))
            if budget < 200:
                break
            chunk = clean[:budget]
            block = (
                f"\n[자료{idx}] 출처: {r.title[:50]} ({r.source})\n"
                f"URL: {r.url[:80]}\n"
                f"---\n{chunk}\n"
            )
            if total + len(block) > max_chars * 0.85:
                break
            parts.append(block)
            total += len(block)
        parts.append("")

    # ── 2) 추출된 기출 문제 패턴 (있을 때만)
    if questions:
        parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        parts.append("【 수집된 기출·유사 문제 패턴 】")
        parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for i, q in enumerate(questions[:15], 1):
            line = f"[기출{i}] {q.question.strip()[:350]}"
            if total + len(line) + 2 > max_chars:
                break
            parts.append(line)
            total += len(line) + 2
        parts.append("")

    # ── 3) 스니펫만 있는 결과 (본문 없는 것)
    snippet_only = [r for r in results if not r.content and r.snippet and _is_edu_relevant(r)]
    if snippet_only and total < max_chars * 0.9:
        parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        parts.append("【 관련 자료 요약 (제목·내용 미리보기) 】")
        parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for r in snippet_only[:10]:
            line = f"• {r.title[:60]}  →  {r.snippet[:150]}"
            if total + len(line) > max_chars:
                break
            parts.append(line)
            total += len(line)
        parts.append("")

    if not fetched and not questions and not snippet_only:
        parts.append("⚠ 웹 수집 자료 없음. AI 자체 지식으로 해당 학년·과목·범위 문제를 출제합니다.")

    return "\n".join(parts)


# ─── 간단 테스트 ──────────────────────────────────
if __name__ == "__main__":
    print("=== KoreanEduSearcher 테스트 ===\n")
    searcher = KoreanEduSearcher(max_queries=2, max_fetch=3, delay=0.8)
    results, questions = searcher.run(
        "중등", "영어", "Unit 2",
        progress_cb=lambda m: print(f"  {m}")
    )
    print(f"\n검색 결과: {len(results)}건  /  문제 추출: {len(questions)}개")
    print("\n상위 결과:")
    for r in results[:8]:
        print(f"  [{r.source}] {r.title[:60]}")
        if r.snippet:
            print(f"         {r.snippet[:80]}")
    if questions:
        print("\n추출된 문제 샘플:")
        for q in questions[:3]:
            print(f"  [점수:{q.score}] {q.question[:120].strip()}")
