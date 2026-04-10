#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
  web_ui.py — 미래학원 유사변형 문제 제작기 웹 인터페이스 v2
  실행: egw  →  http://localhost:7777
"""

import sys, os, io, json, threading, queue, time, re, tempfile
from pathlib import Path
from datetime import datetime
from flask import Flask, request, Response, jsonify

sys.path.insert(0, str(Path(__file__).parent))
from exam_generator import (
    ENGINES, SUBJECT_META, GRADE_META, GRADE_GROUPS,
    build_system_prompt, build_mc_prompt, build_sa_prompt, build_essay_prompt,
    build_web_system_prompt, build_web_mc_prompt, build_web_sa_prompt, build_web_essay_prompt,
    build_ai_client, OUTPUT_DIR, TASKS,
    format_exam_sheet, format_answer_sheet,
)
try:
    from web_searcher import KoreanEduSearcher, format_for_prompt, format_as_rich_content
    WEB_SEARCH_AVAILABLE = True
except ImportError:
    WEB_SEARCH_AVAILABLE = False

from openai import OpenAI

app = Flask(__name__)
UPLOAD_DIR  = Path(__file__).parent / "uploads"
CONFIG_FILE = Path(__file__).parent / "config.json"
UPLOAD_DIR.mkdir(exist_ok=True)

# ══════════════════════════════════════════════════════
#  API 키 저장/불러오기
# ══════════════════════════════════════════════════════
def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def get_api_key(env_name) -> str:
    """환경변수 → config.json → 빈 문자열 순서로 키 반환 (None 안전)"""
    if not env_name:
        return ""
    return (os.environ.get(env_name, "")
            or load_config().get("api_keys", {}).get(env_name, ""))

# ══════════════════════════════════════════════════════
#  파일 텍스트 추출
# ══════════════════════════════════════════════════════
def extract_text(filepath: str, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    try:
        if ext == "txt":
            return open(filepath, encoding="utf-8", errors="ignore").read()
        elif ext == "pdf":
            import pdfplumber
            with pdfplumber.open(filepath) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages)
        elif ext in ("xlsx", "xls"):
            import openpyxl
            wb = openpyxl.load_workbook(filepath, data_only=True)
            rows = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    rows.append("\t".join(str(c) if c else "" for c in row))
            return "\n".join(rows)
        elif ext in ("jpg", "jpeg", "png", "gif", "bmp", "webp"):
            # Pillow로 읽어서 base64 → AI 비전 추출 시도, 없으면 파일명만
            from PIL import Image
            img = Image.open(filepath)
            return f"[이미지 파일: {filename} ({img.size[0]}×{img.size[1]}px) — 이미지 내 텍스트는 수동 입력 필요]"
        elif ext == "hwp":
            return f"[HWP 파일: {filename} — HWP는 직접 텍스트를 복사해서 붙여넣어 주세요]"
        else:
            return open(filepath, encoding="utf-8", errors="ignore").read()
    except Exception as e:
        return f"[파일 읽기 오류: {e}]"


# ══════════════════════════════════════════════════════
#  DOCX 생성
# ══════════════════════════════════════════════════════
def make_docx(markdown_text: str, academy_name: str, show_academy: bool) -> bytes:
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    # 여백 설정
    for section in doc.sections:
        section.top_margin    = Cm(2)
        section.bottom_margin = Cm(2)
        section.left_margin   = Cm(2.5)
        section.right_margin  = Cm(2.5)

    lines = markdown_text.split("\n")
    for line in lines:
        line = line.rstrip()
        if not line:
            doc.add_paragraph("")
            continue
        # h1
        if line.startswith("# "):
            p = doc.add_heading(line[2:], level=1)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        # h2
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        # h3
        elif line.startswith("### "):
            doc.add_heading(line[4:], level=3)
        # hr
        elif line.startswith("---"):
            doc.add_paragraph("─" * 40)
        # blockquote
        elif line.startswith("> "):
            p = doc.add_paragraph(line[2:])
            p.paragraph_format.left_indent = Cm(1)
            run = p.runs[0] if p.runs else p.add_run("")
            run.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
            run.font.size = Pt(9)
        else:
            doc.add_paragraph(line)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ══════════════════════════════════════════════════════
#  AI 호출 (429 자동 재시도 포함)
# ══════════════════════════════════════════════════════
def call_ai(client, engine_info, sys_prompt, user_prompt, max_retries=3):
    import re as _re
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model      = engine_info["model_id"],
                messages   = [{"role":"system","content":sys_prompt},
                              {"role":"user",  "content":user_prompt}],
                temperature= engine_info["temperature"],
                max_tokens = engine_info["max_tokens"],
            )
            return (resp.choices[0].message.content or "").strip()

        except Exception as e:
            err_str = str(e)
            is_429  = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str

            if not is_429 or attempt == max_retries - 1:
                # 429 에러 메시지 친절하게 변환
                if is_429:
                    if "limit: 0" in err_str or "limit_: 0" in err_str.lower():
                        raise Exception(
                            "❌ Gemini 무료 할당량 소진 또는 결제 미등록\n\n"
                            "해결 방법:\n"
                            "① Google AI Studio(aistudio.google.com) → 결제 등록\n"
                            "② 또는 Claude / Gemma(로컬) 엔진으로 변경"
                        )
                    else:
                        # retryDelay 파싱
                        m = _re.search(r"retry.*?(\d+)s", err_str, _re.IGNORECASE)
                        wait_s = m.group(1) if m else "60"
                        raise Exception(
                            f"❌ Gemini 요청 한도 초과 (Rate limit)\n"
                            f"약 {wait_s}초 후 자동 재시도했으나 실패했습니다.\n"
                            "잠시 후 다시 시도하거나 다른 엔진을 사용하세요."
                        )
                raise

            # 재시도 대기 시간 파싱 (retryDelay 또는 기본 15초)
            m = _re.search(r"retry.*?(\d+)s", err_str, _re.IGNORECASE)
            wait_sec = int(m.group(1)) + 2 if m else 15
            wait_sec = min(wait_sec, 60)  # 최대 60초
            time.sleep(wait_sec)

    raise Exception("최대 재시도 횟수 초과")


# ══════════════════════════════════════════════════════
#  SSE 생성 스트림
# ══════════════════════════════════════════════════════
def generate_stream(engine_info, subject, grade, passage, original,
                    web_ref="", scope="",
                    academy_name="미래학원", show_academy=True,
                    num_mc=2, num_sa=2, num_es=1,
                    web_only_content=""):
    """
    web_only_content: 웹 전용 모드일 때 format_as_rich_content() 결과물.
                      지문(passage)이 없고 이 값이 있으면 웹 전용 프롬프트 사용.
    """

    def send(event, data):
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    # ── 웹 전용 모드 판별
    WEB_ONLY = bool(web_only_content) and not passage.strip()

    yield send("status", {"msg": "🔗 AI 연결 중..."})
    try:
        client = build_ai_client(engine_info)
    except Exception as e:
        yield send("error", {"msg": str(e)}); return

    # ── 유형별 1회 통합 호출 (9회 → 최대 3회)
    is_ollama = engine_info.get("provider") == "ollama"
    timeout_sec = 300 if is_ollama else 120  # 로컬 모델은 5분

    if WEB_ONLY:
        sys_prompt = build_web_system_prompt(subject, grade)
        yield send("status", {"msg": "🌐 웹 검색 전용 모드 — 수집 자료로 문제 출제 중..."})
        tasks = [
            ("mc", "객관식", build_web_mc_prompt,    num_mc),
            ("sa", "주관식", build_web_sa_prompt,    num_sa),
            ("es", "서술형", build_web_essay_prompt,  num_es),
        ]
    else:
        sys_prompt = build_system_prompt(subject, grade)
        tasks = [
            ("mc", "객관식", build_mc_prompt,    num_mc),
            ("sa", "주관식", build_sa_prompt,    num_sa),
            ("es", "서술형", build_essay_prompt,  num_es),
        ]

    # ── 클라우드는 병렬, 로컬(Ollama)은 순차 (로컬은 동시 요청 시 메모리 초과)
    active_tasks = [(qt, lb, fn, nq) for qt, lb, fn, nq in tasks if nq > 0]

    results = {}

    if is_ollama:
        # 로컬 모델: 순차 처리 (메모리 공유로 병렬 불가)
        for q_type, label, prompt_fn, nq in active_tasks:
            yield send("progress", {"step": label, "key": q_type})
            q = queue.Queue()

            def worker(fn=prompt_fn, nq=nq, key=q_type):
                try:
                    if WEB_ONLY:
                        prompt = fn(subject, grade, web_only_content, "", nq)
                    else:
                        prompt = fn(subject, grade, passage, original, "",
                                    web_ref=web_ref, num_questions=nq)
                    text = call_ai(client, engine_info, sys_prompt, prompt)
                    q.put(("ok", text))
                except Exception as e:
                    q.put(("err", str(e)))

            t = threading.Thread(target=worker, daemon=True)
            t.start(); t.join(timeout=timeout_sec)
            status, text = q.get() if not q.empty() else ("err", f"시간 초과 ({timeout_sec}초)")
            if status == "err":
                yield send("error", {"msg": f"{label}: {text}"}); return
            results[q_type] = text
            yield send("chunk", {"key": q_type, "label": label, "text": text})

    else:
        # 클라우드 모델: 병렬 처리 (객관식·주관식·서술형 동시 생성)
        result_queues = {qt: queue.Queue() for qt, lb, fn, nq in active_tasks}

        def make_worker(q_type, label, prompt_fn, nq):
            def worker():
                try:
                    if WEB_ONLY:
                        prompt = prompt_fn(subject, grade, web_only_content, "", nq)
                    else:
                        prompt = prompt_fn(subject, grade, passage, original, "",
                                           web_ref=web_ref, num_questions=nq)
                    text = call_ai(client, engine_info, sys_prompt, prompt)
                    result_queues[q_type].put(("ok", text))
                except Exception as e:
                    result_queues[q_type].put(("err", str(e)))
            return worker

        # 전체 유형 동시 시작
        threads = []
        for q_type, label, prompt_fn, nq in active_tasks:
            yield send("progress", {"step": label, "key": q_type})
            t = threading.Thread(target=make_worker(q_type, label, prompt_fn, nq), daemon=True)
            t.start()
            threads.append((q_type, label, t))

        # 완료 순서대로 수집
        for q_type, label, t in threads:
            t.join(timeout=timeout_sec)
            status, text = result_queues[q_type].get() if not result_queues[q_type].empty() \
                           else ("err", f"시간 초과 ({timeout_sec}초)")
            if status == "err":
                yield send("error", {"msg": f"{label}: {text}"}); return
            results[q_type] = text
            yield send("chunk", {"key": q_type, "label": label, "text": text})

    # ── 공통 파라미터
    kw = dict(scope=scope, web_used=bool(web_ref),
              academy_name=academy_name, show_academy=show_academy,
              num_mc=num_mc, num_sa=num_sa, num_es=num_es)

    exam_md   = format_exam_sheet(subject, grade, passage, results, engine_info, **kw)
    answer_md = format_answer_sheet(subject, grade, results, engine_info, **kw)

    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    etag = engine_info["provider"]
    stag = f"_{scope.replace(' ','_')}" if scope else ""
    base = f"{grade}_{subject}{stag}_{etag}_{ts}"

    (OUTPUT_DIR / f"{base}_문제지.md").write_text(exam_md,   encoding="utf-8")
    (OUTPUT_DIR / f"{base}_답지.md").write_text(answer_md, encoding="utf-8")

    yield send("done", {
        "exam_md"   : exam_md,
        "answer_md" : answer_md,
        "exam_file" : f"{base}_문제지.md",
        "answer_file": f"{base}_답지.md",
    })


# ══════════════════════════════════════════════════════
#  웹 검색 SSE
# ══════════════════════════════════════════════════════
def search_stream(grade, subject, scope, keywords, depth):
    def send(event, data):
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    if not WEB_SEARCH_AVAILABLE:
        yield send("error", {"msg": "web_searcher.py 없음"}); return

    # 키워드가 있으면 scope에 추가
    full_scope = f"{scope} {keywords}".strip() if keywords else scope
    cfg = {"1":(2,3,0.6),"2":(4,6,0.9),"3":(6,10,1.1)}.get(str(depth),(4,6,0.9))
    max_q, max_f, delay = cfg
    searcher = KoreanEduSearcher(max_queries=max_q, max_fetch=max_f, delay=delay)
    results_holder, questions_holder = [], []
    done_ev = threading.Event()

    def run():
        res, qs = searcher.run(grade, subject, full_scope, progress_cb=lambda m: None)
        results_holder.extend(res); questions_holder.extend(qs); done_ev.set()

    threading.Thread(target=run, daemon=True).start()
    while not done_ev.wait(timeout=1.5):
        yield send("ping", {})

    # 보조 참고 텍스트 (지문 있을 때 사용)
    web_ref = format_for_prompt(results_holder, questions_holder, max_chars=2500)
    # 풍부한 본문 (웹 전용 모드에서 사용)
    web_rich = format_as_rich_content(
        results_holder, questions_holder,
        grade, subject, full_scope, max_chars=7000
    )
    yield send("done", {
        "web_ref"  : web_ref,
        "web_rich" : web_rich,
        "count"    : len(results_holder),
        "q_count"  : len(questions_holder),
        "samples"  : [q.question[:100] for q in questions_holder[:3]],
    })


# ══════════════════════════════════════════════════════
#  라우트
# ══════════════════════════════════════════════════════
@app.route("/")
def index():
    return HTML_PAGE

@app.route("/api/upload", methods=["POST"])
def api_upload():
    files = request.files.getlist("files")
    texts = []
    for f in files:
        if not f.filename: continue
        tmp = UPLOAD_DIR / f.filename
        f.save(str(tmp))
        text = extract_text(str(tmp), f.filename)
        texts.append({"name": f.filename, "text": text, "chars": len(text)})
    return jsonify(texts)

@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json()
    g_idx = int(data.get("engine_group", 2))
    m_idx = int(data.get("engine_model", 0))
    eng   = ENGINES[g_idx]; mdl = eng["models"][m_idx]
    env_name = eng.get("api_key_env") or ""   # None → ""
    api_key  = data.get("api_key","").strip() or get_api_key(env_name)
    if not api_key and eng["provider"] == "ollama": api_key = "ollama"
    if not api_key: return jsonify({"error": "API 키 없음"}), 400

    engine_info = {
        "group": eng["group"], "provider": eng["provider"],
        "base_url": eng["base_url"], "api_key": api_key,
        "model_name": mdl["name"], "model_id": mdl["id"],
        "temperature": eng["temperature"], "max_tokens": eng["max_tokens"],
    }
    def stream():
        for chunk in generate_stream(
            engine_info,
            data.get("subject","영어"), data.get("grade","중등2"),
            data.get("passage",""),    data.get("original","원본 문제 없음"),
            data.get("web_ref",""),    data.get("scope",""),
            data.get("academy_name","미래학원"), data.get("show_academy", True),
            int(data.get("num_mc", 2)), int(data.get("num_sa", 2)), int(data.get("num_es", 1)),
            data.get("web_rich",""),
        ):
            yield chunk
    return Response(stream(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering":"no","Cache-Control":"no-cache"})

@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json()
    def stream():
        for c in search_stream(data.get("grade","중등2"), data.get("subject","영어"),
                               data.get("scope",""), data.get("keywords",""),
                               data.get("depth","2")):
            yield c
    return Response(stream(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering":"no","Cache-Control":"no-cache"})

@app.route("/api/engines")
def api_engines():
    return jsonify([{
        "group": e["group"], "provider": e["provider"], "key_env": e.get("api_key_env"),
        "models": [{"name":m["name"],"id":m["id"],
                    "cost_tag":m.get("cost_tag",""),"cost_note":m.get("cost_note",""),
                    "perf":m.get("perf","")} for m in e["models"]],
    } for e in ENGINES])

@app.route("/api/check_key")
def api_check_key():
    env = request.args.get("env","")
    key = get_api_key(env) if env else ""
    masked = (key[:6] + "···" + key[-4:]) if len(key) > 12 else ("설정됨" if key else "")
    return jsonify({"ok": bool(key), "masked": masked})

@app.route("/api/save_key", methods=["POST"])
def api_save_key():
    """API 키를 config.json에 영구 저장"""
    data    = request.get_json()
    env     = data.get("env", "")
    new_key = data.get("key", "").strip()
    if not env:
        return jsonify({"ok": False, "msg": "env 없음"}), 400
    cfg = load_config()
    if "api_keys" not in cfg:
        cfg["api_keys"] = {}
    if new_key:
        cfg["api_keys"][env] = new_key
    else:
        cfg["api_keys"].pop(env, None)   # 빈 값이면 삭제
    save_config(cfg)
    return jsonify({"ok": True, "msg": "저장 완료"})

@app.route("/api/load_keys")
def api_load_keys():
    """저장된 키 목록 (마스킹) 반환"""
    cfg  = load_config()
    keys = cfg.get("api_keys", {})
    result = {}
    for env, key in keys.items():
        result[env] = (key[:6] + "···" + key[-4:]) if len(key) > 12 else "설정됨"
    return jsonify(result)

@app.route("/api/export/docx", methods=["POST"])
def api_export_docx():
    data = request.get_json()
    md   = data.get("markdown","")
    show = data.get("show_academy", True)
    acad = data.get("academy_name","미래학원")
    docx_bytes = make_docx(md, acad, show)
    return Response(docx_bytes,
                    mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition":"attachment; filename=exam.docx"})

@app.route("/api/export/pdf", methods=["POST"])
def api_export_pdf():
    """문제지 + 답지 마크다운 → 학원 스타일 PDF"""
    try:
        from exam_pdf import exam_md_to_pdf
    except ImportError as e:
        return jsonify({"error": f"exam_pdf 모듈 없음: {e}"}), 500

    data       = request.get_json()
    exam_md    = data.get("exam_md", "")
    answer_md  = data.get("answer_md", "")
    academy    = data.get("academy_name", "미래학원")
    subject    = data.get("subject", "영어")
    grade      = data.get("grade", "중등2")
    scope      = data.get("scope", "")

    if not exam_md:
        return jsonify({"error": "문제지 내용 없음"}), 400

    try:
        pdf_bytes = exam_md_to_pdf(exam_md, answer_md, academy, subject, grade, scope)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "detail": traceback.format_exc()}), 500

    from urllib.parse import quote
    fname_utf8 = f"{grade}_{subject}_exam.pdf"
    cd = f"attachment; filename=\"exam.pdf\"; filename*=UTF-8''{quote(fname_utf8)}"
    return Response(pdf_bytes,
                    mimetype="application/pdf",
                    headers={"Content-Disposition": cd})

@app.route("/api/files")
def api_files():
    files = sorted(OUTPUT_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    return jsonify([{"name":f.name,"size":f.stat().st_size,
                     "mtime":datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
                    for f in files[:30]])

@app.route("/api/files/<filename>")
def api_file_content(filename):
    path = OUTPUT_DIR / filename
    if not path.exists(): return "없음",404
    return path.read_text(encoding="utf-8"),200,{"Content-Type":"text/plain;charset=utf-8"}


# ══════════════════════════════════════════════════════
#  HTML 단일 페이지
# ══════════════════════════════════════════════════════
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>미래학원 문제 제작기</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
/* ── Claude 배경 테마 (따뜻한 크림) ── */
:root {
  --bg      : #f5f0e8;
  --panel   : #ffffff;
  --sidebar : #faf7f2;
  --border  : #e2d9ce;
  --accent  : #c96442;   /* Anthropic 오렌지 */
  --accent2 : #2563eb;   /* 파란색 버튼 */
  --green   : #16a34a;
  --red     : #dc2626;
  --yellow  : #d97706;
  --text    : #1a1a1a;
  --dim     : #6b7280;
  --card    : #fdf9f5;
  --hover   : #f0ebe2;
  --shadow  : rgba(0,0,0,.08);
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
          background:var(--bg);color:var(--text);font-size:14px}

/* ── 레이아웃 ── */
.app{display:grid;grid-template-rows:54px 1fr;height:100vh;overflow:hidden}

/* 탑바 */
.topbar{display:flex;align-items:center;gap:12px;padding:0 20px;
        background:var(--panel);border-bottom:2px solid var(--border);
        box-shadow:0 1px 4px var(--shadow)}
.logo{font-size:18px;font-weight:800;color:var(--accent);letter-spacing:-.5px}
.logo span{font-size:12px;font-weight:400;color:var(--dim);margin-left:6px}
.tabs{display:flex;gap:2px;margin-left:auto}
.tab{padding:6px 18px;border-radius:20px;border:none;background:transparent;
     color:var(--dim);cursor:pointer;font-size:13px;font-weight:500;transition:.15s}
.tab:hover{background:var(--hover)}
.tab.on{background:var(--accent);color:#fff}

/* ── 본문 */
.body{display:grid;grid-template-columns:300px 1fr;overflow:hidden}

/* 왼쪽 설정 패널 */
.left{overflow-y:auto;background:var(--sidebar);border-right:1px solid var(--border);
      padding:14px 12px;display:flex;flex-direction:column;gap:16px}
.sec-hd{font-size:10px;font-weight:700;color:var(--dim);letter-spacing:1.2px;
        text-transform:uppercase;margin-bottom:6px;padding-bottom:4px;
        border-bottom:1px solid var(--border)}

/* 엔진 */
.eng-list{display:flex;flex-direction:column;gap:4px}
.eng-card{padding:9px 11px;border-radius:8px;border:1.5px solid var(--border);
          cursor:pointer;background:var(--panel);transition:.13s}
.eng-card:hover{border-color:var(--accent);background:var(--hover)}
.eng-card.on{border-color:var(--accent);background:#fff3ef}
.eng-name{font-weight:600;font-size:12px}
.badge{font-size:10px;padding:1px 7px;border-radius:10px;display:inline-block;margin-top:3px}
.b-free{background:#dcfce7;color:#166534}
.b-tier{background:#fef9c3;color:#854d0e}
.b-paid{background:#fee2e2;color:#991b1b}
select{width:100%;padding:7px 9px;background:var(--panel);border:1.5px solid var(--border);
       border-radius:7px;color:var(--text);font-size:12px;outline:none;cursor:pointer}
select:focus{border-color:var(--accent)}
.tip{font-size:10px;color:var(--dim);margin-top:3px}

/* 학원명 */
.academy-row{display:flex;align-items:center;gap:6px}
.academy-row input[type=text]{flex:1;padding:7px 9px;background:var(--panel);
  border:1.5px solid var(--border);border-radius:7px;color:var(--text);font-size:13px;
  font-weight:600;outline:none}
.academy-row input:focus{border-color:var(--accent)}
.toggle-row{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--dim)}
.toggle-row input{accent-color:var(--accent);width:15px;height:15px;cursor:pointer}

/* 학년 그리드 */
.grade-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
.g-sep{grid-column:1/-1;font-size:10px;color:var(--dim);padding:5px 0 2px;
       border-top:1px solid var(--border);margin-top:3px}
.g-item{padding:7px 3px;text-align:center;border-radius:7px;border:1.5px solid var(--border);
        cursor:pointer;font-size:11px;background:var(--panel);transition:.12s;line-height:1.35;
        white-space:pre-line}
.g-item:hover{border-color:var(--accent)}
.g-item.on{background:var(--accent);color:#fff;border-color:var(--accent)}

/* 과목 그리드 */
.subj-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
.s-item{padding:8px 3px;text-align:center;border-radius:7px;border:1.5px solid var(--border);
        cursor:pointer;font-size:12px;background:var(--panel);transition:.12s}
.s-item:hover{border-color:var(--accent)}
.s-item.on{background:var(--accent);color:#fff;border-color:var(--accent)}

/* 문항 수 */
.qnum-row{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.qnum-cell{display:flex;flex-direction:column;gap:3px;align-items:center}
.qnum-cell label{font-size:10px;color:var(--dim)}
.qnum-cell input[type=number]{width:56px;padding:5px;text-align:center;
  background:var(--panel);border:1.5px solid var(--border);border-radius:7px;
  font-size:14px;font-weight:700;color:var(--accent);outline:none}
.qnum-cell input:focus{border-color:var(--accent)}

/* ── 오른쪽 */
.right{display:grid;grid-template-rows:1fr 60px;overflow:hidden}
.content{overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}

/* 지문/원본 입력 */
.lbl{font-size:11px;font-weight:700;color:var(--dim);margin-bottom:4px;
     text-transform:uppercase;letter-spacing:.5px}
textarea{width:100%;background:var(--panel);border:1.5px solid var(--border);
         border-radius:8px;color:var(--text);font-size:13px;padding:9px;
         resize:vertical;outline:none;font-family:inherit;line-height:1.6}
textarea:focus{border-color:var(--accent)}

/* 파일 업로드 */
.upload-zone{border:2px dashed var(--border);border-radius:10px;padding:14px;
             text-align:center;cursor:pointer;transition:.15s;background:var(--card)}
.upload-zone:hover,.upload-zone.drag{border-color:var(--accent);background:#fff3ef}
.upload-zone p{font-size:12px;color:var(--dim);margin-top:4px}
.file-chips{display:flex;flex-wrap:wrap;gap:5px;padding:8px 10px}
.file-chips:empty{padding:0}
.chip{padding:4px 10px;background:#dbeafe;border:1px solid #93c5fd;
      border-radius:12px;font-size:11px;color:#1e40af;
      display:flex;align-items:center;gap:5px;cursor:pointer;transition:.12s}
.chip:hover{background:#fee2e2;border-color:#fca5a5;color:#991b1b}
.chip .chip-size{font-size:9px;opacity:.7}

/* 웹 검색 */
.search-box{background:var(--card);border:1.5px solid var(--border);
            border-radius:10px;padding:12px}
.search-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
.depth-row{display:flex;gap:5px;margin-top:6px}
.d-btn{padding:4px 12px;border-radius:6px;border:1.5px solid var(--border);
       background:transparent;color:var(--dim);cursor:pointer;font-size:12px}
.d-btn.on{background:#dcfce7;border-color:var(--green);color:var(--green);font-weight:700}
.search-go{padding:6px 14px;background:var(--green);color:#fff;border:none;
           border-radius:7px;cursor:pointer;font-size:12px;font-weight:600;margin-top:8px}
.search-go:disabled{opacity:.5;cursor:not-allowed}
.srch-st{font-size:11px;color:var(--dim);margin-top:6px;min-height:16px}
.srch-ok{display:inline-block;padding:2px 8px;background:#dcfce7;color:#166534;
         border-radius:10px;font-size:11px;margin-top:4px}

/* 하단 액션 */
.action{padding:0 16px;display:flex;gap:8px;align-items:center;
        background:var(--panel);border-top:1px solid var(--border)}
.btn-gen{flex:1;padding:11px;background:var(--accent);color:#fff;font-size:15px;
         font-weight:700;border:none;border-radius:8px;cursor:pointer;transition:.15s}
.btn-gen:hover:not(:disabled){filter:brightness(1.1)}
.btn-gen:disabled{opacity:.45;cursor:not-allowed}
.btn-s{padding:9px 14px;background:var(--panel);border:1.5px solid var(--border);
       color:var(--text);border-radius:8px;cursor:pointer;font-size:13px;transition:.15s}
.btn-s:hover{background:var(--hover)}
.btn-blue{background:var(--accent2);color:#fff;border-color:var(--accent2)}
.btn-blue:hover{filter:brightness(1.1)}
.btn-pdf{background:#c0392b;color:#fff;border-color:#c0392b}
.btn-pdf:hover{filter:brightness(1.1)}

/* 결과 패널 */
.prog-list{display:flex;flex-direction:column;gap:5px;margin-bottom:16px}
.prog-item{display:flex;align-items:center;gap:9px;font-size:13px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--border);flex-shrink:0}
.dot.done{background:var(--green)}
.dot.spin{background:var(--yellow);animation:blink .7s infinite alternate}
@keyframes blink{from{opacity:.3}to{opacity:1}}

/* 마크다운 렌더 */
.md-view{background:var(--panel);border:1.5px solid var(--border);
         border-radius:10px;padding:28px;line-height:1.85;
         box-shadow:0 1px 6px var(--shadow)}
.md-view h1{font-size:20px;color:var(--accent);border-bottom:2px solid var(--border);
            padding-bottom:10px;margin-bottom:14px}
.md-view h2{font-size:16px;color:#1d4ed8;margin:18px 0 9px}
.md-view h3{font-size:14px;color:var(--yellow);margin:12px 0 6px}
.md-view p{margin-bottom:9px}
.md-view hr{border:none;border-top:1px solid var(--border);margin:14px 0}
.md-view blockquote{border-left:3px solid var(--accent);padding-left:12px;
                    color:var(--dim);margin:8px 0;font-size:13px}
.md-view code{background:#f3f4f6;padding:1px 5px;border-radius:4px;font-size:12px}
.md-view pre{background:#f3f4f6;padding:12px;border-radius:8px;overflow-x:auto;margin:8px 0}

/* 파일 목록 */
.flist{display:flex;flex-direction:column;gap:5px}
.fitem{display:flex;align-items:center;gap:9px;padding:10px 13px;
       background:var(--panel);border:1.5px solid var(--border);
       border-radius:8px;cursor:pointer;transition:.12s}
.fitem:hover{border-color:var(--accent);background:var(--hover)}
.fname{flex:1;font-size:12px}
.fmeta{font-size:11px;color:var(--dim)}

/* ── 전체화면 편집 모달 ── */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.45);
          display:none;align-items:center;justify-content:center;z-index:999}
.modal-bg.open{display:flex}
.modal{width:96vw;height:96vh;background:var(--panel);border-radius:12px;
       display:grid;grid-template-rows:52px 1fr 56px;
       box-shadow:0 8px 40px rgba(0,0,0,.25);overflow:hidden}
.modal-hd{display:flex;align-items:center;gap:10px;padding:0 18px;
          background:var(--sidebar);border-bottom:1px solid var(--border)}
.modal-hd h2{font-size:15px;font-weight:700}
.modal-body{display:grid;grid-template-columns:1fr 1fr;overflow:hidden}
.edit-pane{display:flex;flex-direction:column;border-right:1px solid var(--border)}
.edit-pane .pane-hd{padding:8px 14px;font-size:11px;font-weight:700;
                    color:var(--dim);background:var(--sidebar);border-bottom:1px solid var(--border)}
#edit-ta{flex:1;padding:14px;font-size:13px;line-height:1.7;
         border:none;outline:none;resize:none;background:var(--panel);
         font-family:'D2Coding','Consolas',monospace;overflow-y:auto}
.preview-pane{overflow-y:auto;padding:20px}
.modal-ft{display:flex;gap:8px;padding:0 18px;align-items:center;
          background:var(--sidebar);border-top:1px solid var(--border)}
.modal-ft .info{font-size:11px;color:var(--dim);flex:1}

/* 결과 서브탭 */
.res-tab{padding:8px 18px;border-radius:8px;border:1.5px solid var(--border);
         background:var(--panel);color:var(--dim);cursor:pointer;font-size:13px;
         font-weight:600;transition:.13s}
.res-tab:hover{border-color:var(--accent);background:var(--hover)}
.res-tab.on{background:var(--accent);color:#fff;border-color:var(--accent)}
#st-answer.on{background:#1d4ed8;border-color:#1d4ed8}

/* 스크롤바 */
::-webkit-scrollbar{width:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}

/* 인쇄 */
@media print{
  .topbar,.left,.action,.tabs,.modal-bg{display:none!important}
  .body{grid-template-columns:1fr!important}
  .right{grid-template-rows:1fr!important}
  .md-view{border:none;box-shadow:none;padding:0}
}
</style>
</head>
<body>
<div class="app">

<!-- 탑바 -->
<div id="tunnel-banner" style="display:none;background:#1a7f4b;color:#fff;text-align:center;padding:7px 16px;font-size:13px;font-weight:600;letter-spacing:.2px">
  🌍 외부 접속 주소: <a id="tunnel-link" href="#" target="_blank" style="color:#90ffca;text-decoration:underline"></a>
  <span style="font-weight:400;margin-left:10px;opacity:.8">(어디서든 이 주소로 접속 가능)</span>
</div>
<div class="topbar">
  <div class="logo">🎓 미래학원<span>유사변형 문제 제작기</span></div>
  <div class="tabs">
    <button class="tab on"  onclick="go('make',this)">✏️ 문제 만들기</button>
    <button class="tab"     onclick="go('result',this)">📄 결과</button>
    <button class="tab"     onclick="go('files',this)">📁 파일</button>
  </div>
</div>

<div class="body">
<!-- ══ 왼쪽 설정 ══ -->
<div class="left">

  <!-- 학원명 -->
  <div>
    <div class="sec-hd">🏫 학원명</div>
    <div class="academy-row">
      <input type="text" id="academy-name" value="미래학원" placeholder="학원명">
    </div>
    <div class="toggle-row" style="margin-top:6px">
      <input type="checkbox" id="show-academy" checked>
      <label for="show-academy">문제지에 학원명 표시</label>
    </div>
  </div>

  <!-- AI 엔진 -->
  <div>
    <div class="sec-hd">① AI 엔진</div>
    <div class="eng-list" id="eng-list"></div>
    <div style="margin-top:8px">
      <div class="lbl">모델</div>
      <select id="model-sel"></select>
      <div class="tip" id="model-tip"></div>
    </div>
    <div id="api-area" style="margin-top:8px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
        <div class="lbl" style="margin:0">API 키 <span id="key-badge"></span></div>
        <span id="key-saved-msg" style="font-size:10px;color:var(--green);display:none">✓ 저장됨</span>
      </div>
      <div style="display:flex;gap:5px">
        <input type="password" id="api-key" placeholder="입력 후 💾 저장하면 다음부터 자동 입력"
               style="flex:1;padding:7px 9px;background:var(--panel);border:1.5px solid var(--border);
                      border-radius:7px;font-size:12px;outline:none"
               oninput="onKeyInput()">
        <button id="btn-save-key" onclick="saveKey()"
                style="padding:6px 10px;background:var(--green);color:#fff;border:none;
                       border-radius:7px;cursor:pointer;font-size:13px;white-space:nowrap"
                title="API 키 저장">💾</button>
        <button id="btn-del-key" onclick="deleteKey()"
                style="padding:6px 10px;background:var(--panel);border:1.5px solid var(--border);
                       border-radius:7px;cursor:pointer;font-size:13px;display:none"
                title="저장된 키 삭제">🗑</button>
      </div>
      <div style="font-size:10px;color:var(--dim);margin-top:3px">
        저장하면 앱을 재시작해도 자동으로 불러옵니다
      </div>
    </div>
  </div>

  <!-- 학년 -->
  <div>
    <div class="sec-hd">② 학년</div>
    <div class="grade-grid" id="grade-grid"></div>
  </div>

  <!-- 과목 -->
  <div>
    <div class="sec-hd">③ 과목</div>
    <div class="subj-grid" id="subj-grid"></div>
  </div>

  <!-- 문항 수 -->
  <div>
    <div class="sec-hd">④ 문항 수 (난이도별)</div>
    <div class="qnum-row">
      <div class="qnum-cell">
        <label>객관식</label>
        <input type="number" id="num-mc" value="2" min="0" max="10">
      </div>
      <div class="qnum-cell">
        <label>주관식</label>
        <input type="number" id="num-sa" value="2" min="0" max="10">
      </div>
      <div class="qnum-cell">
        <label>서술형</label>
        <input type="number" id="num-es" value="1" min="0" max="5">
      </div>
    </div>
    <div class="tip" style="margin-top:5px" id="total-tip"></div>
  </div>

</div><!-- /left -->

<!-- ══ 오른쪽 ══ -->
<div class="right">
<div class="content" id="tab-make">

  <!-- ⑤ 지문 / 파일 업로드 통합 -->
  <div style="border:1.5px solid var(--border);border-radius:10px;overflow:hidden">

    <!-- 헤더 -->
    <div style="display:flex;align-items:center;justify-content:space-between;
                padding:9px 13px;background:var(--sidebar);border-bottom:1px solid var(--border)">
      <span style="font-weight:700;font-size:13px">⑤ 지문 · 문제 파일</span>
      <span style="font-size:10px;color:var(--dim)">파일 업로드 또는 직접 입력</span>
    </div>

    <!-- 드래그앤드롭 영역 -->
    <div class="upload-zone" id="drop-zone"
         onclick="document.getElementById('file-input').click()"
         ondragover="dragOver(event)" ondragleave="dragLeave(event)" ondrop="dropFiles(event)"
         style="border:none;border-bottom:1.5px dashed var(--border);border-radius:0;
                padding:18px 14px;background:var(--card)">
      <div style="font-size:32px;margin-bottom:4px">📂</div>
      <div style="font-weight:600;font-size:13px;color:var(--text);margin-bottom:5px">
        클릭하거나 파일을 여기에 끌어다 놓으세요
      </div>
      <div style="display:flex;flex-wrap:wrap;justify-content:center;gap:5px;margin-top:4px">
        <span style="padding:2px 8px;background:#e5e7eb;border-radius:8px;font-size:11px">PDF</span>
        <span style="padding:2px 8px;background:#e5e7eb;border-radius:8px;font-size:11px">HWP</span>
        <span style="padding:2px 8px;background:#e5e7eb;border-radius:8px;font-size:11px">XLSX</span>
        <span style="padding:2px 8px;background:#e5e7eb;border-radius:8px;font-size:11px">TXT</span>
        <span style="padding:2px 8px;background:#e5e7eb;border-radius:8px;font-size:11px">JPG</span>
        <span style="padding:2px 8px;background:#e5e7eb;border-radius:8px;font-size:11px">PNG</span>
        <span style="padding:2px 8px;background:#e5e7eb;border-radius:8px;font-size:11px">GIF</span>
        <span style="padding:2px 8px;background:#e5e7eb;border-radius:8px;font-size:11px">여러 장 가능</span>
      </div>
    </div>
    <input type="file" id="file-input" multiple
           accept=".pdf,.hwp,.xlsx,.xls,.txt,.jpg,.jpeg,.png,.gif,.bmp,.webp"
           style="display:none" onchange="uploadFiles(this.files)">

    <!-- 업로드된 파일 칩 -->
    <div class="file-chips" id="file-chips"
         style="padding:0 10px;min-height:0;transition:.2s"></div>

    <!-- 직접 입력 textarea -->
    <textarea id="passage" rows="4"
              placeholder="또는 지문·문제를 직접 붙여넣으세요..."
              style="width:100%;border:none;border-top:1px solid var(--border);
                     border-radius:0;resize:vertical;padding:10px 13px;
                     font-size:13px;line-height:1.6;background:var(--panel);
                     color:var(--text);outline:none;font-family:inherit"></textarea>
  </div>

  <!-- 원본 문제 -->
  <div>
    <div class="lbl">⑦ 원본 문제 <span style="color:var(--dim)">(선택)</span></div>
    <textarea id="original" rows="3" placeholder="참고할 원본 문제 (없으면 비워두세요)"></textarea>
  </div>

  <!-- 웹 검색 — 항상 표시 -->
  <div class="search-box">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <span style="font-weight:800;font-size:14px;color:var(--accent)">⑧ 🔍 웹 기출문제 검색</span>
      <label class="toggle-row" style="font-size:12px">
        <input type="checkbox" id="web-on" checked>
        <span>검색 사용</span>
      </label>
    </div>

    <!-- 검색어·키워드 — 항상 보임 -->
    <div style="margin-bottom:10px">
      <div class="lbl" style="color:var(--accent);font-size:12px;margin-bottom:5px">
        🔑 검색어 / 키워드 <span style="color:var(--red)">(중요)</span>
      </div>
      <input type="text" id="keywords"
             placeholder="예: 중2 영어 Unit2 기출 변형 서술형  /  수능 빈칸 추론  /  광합성 단원평가"
             style="width:100%;padding:9px 11px;background:var(--panel);
                    border:2px solid var(--accent);border-radius:8px;
                    font-size:13px;outline:none;color:var(--text)">
      <div style="font-size:10px;color:var(--dim);margin-top:4px">
        학년·과목·단원·문제유형을 자유롭게 입력 — 네이버 블로그·카페·족보닷컴에서 기출문제를 찾아옵니다
      </div>
    </div>

    <!-- 진도 범위 -->
    <div style="margin-bottom:10px">
      <div class="lbl">진도 범위 <span style="color:var(--dim);font-weight:400">(선택)</span></div>
      <input type="text" id="scope" placeholder="예: Unit 2,  이차함수,  3·1 운동,  광합성"
             style="width:100%;padding:7px 9px;background:var(--panel);border:1.5px solid var(--border);
                    border-radius:7px;font-size:12px;outline:none;color:var(--text)">
    </div>

    <!-- 검색 강도 + 버튼 -->
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <div class="depth-row" style="margin:0">
        <button class="d-btn" onclick="setDepth(1)">빠름</button>
        <button class="d-btn on" onclick="setDepth(2)">보통 ★</button>
        <button class="d-btn" onclick="setDepth(3)">심층</button>
      </div>
      <button class="search-go" id="srch-btn" onclick="doSearch()" style="margin:0;flex:1;min-width:100px">
        🔍 검색 시작
      </button>
    </div>
    <div class="srch-st" id="srch-st" style="margin-top:8px"></div>
    <div id="srch-ok"></div>
  </div>

</div><!-- /tab-make -->

<!-- 결과 탭 -->
<div class="content" id="tab-result" style="display:none">
  <div class="prog-list" id="prog-list"></div>
  <!-- 문제지/답지 서브탭 -->
  <div id="result-subtabs" style="display:none">
    <div style="display:flex;gap:6px;margin-bottom:12px;align-items:center">
      <button class="res-tab on" id="st-exam"   onclick="switchResult('exam')">
        📝 문제지 <span style="font-size:10px;opacity:.7">(학생 배포용)</span>
      </button>
      <button class="res-tab"    id="st-answer" onclick="switchResult('answer')">
        📋 정답·해설지 <span style="font-size:10px;opacity:.7">(교사 보관용)</span>
      </button>
      <span style="margin-left:auto;font-size:11px;color:var(--dim)">
        ⚠️ 문제지와 답지는 별도로 인쇄하세요
      </span>
    </div>
  </div>
  <div id="result-area"></div>
</div>

<!-- 파일 탭 -->
<div class="content" id="tab-files" style="display:none">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
    <div class="lbl">저장된 문제지</div>
    <button class="btn-s" style="font-size:11px;padding:5px 10px" onclick="loadFiles()">🔄 새로고침</button>
  </div>
  <div class="flist" id="flist"></div>
</div>

<!-- 하단 액션 -->
<div class="action">
  <button class="btn-gen" id="btn-gen" onclick="doGenerate()">🚀 문제 생성</button>
  <button class="btn-s"   onclick="clearAll()">🗑 초기화</button>
  <button class="btn-s btn-blue" id="btn-edit" style="display:none" onclick="openEdit()">✏️ 편집·인쇄</button>
  <button class="btn-s"   id="btn-docx" style="display:none" onclick="dlDocx()">📄 DOCX</button>
  <button class="btn-s btn-pdf" id="btn-pdf"  style="display:none" onclick="dlPdf()">🖨️ PDF 인쇄용</button>
</div>
</div><!-- /right -->
</div><!-- /body -->

<!-- ══ 전체화면 편집 모달 ══ -->
<div class="modal-bg" id="modal-bg" onclick="closeEditOutside(event)">
<div class="modal">
  <div class="modal-hd">
    <h2>✏️ 최종 문제지 편집</h2>
    <span style="font-size:11px;color:var(--dim);margin-left:6px">— 수정 후 인쇄하거나 다운로드하세요</span>
    <button class="btn-s" style="margin-left:auto;font-size:12px" onclick="closeEdit()">✕ 닫기</button>
  </div>
  <div class="modal-body">
    <!-- 편집 -->
    <div class="edit-pane">
      <div class="pane-hd">📝 마크다운 편집</div>
      <textarea id="edit-ta" oninput="syncPreview()"></textarea>
    </div>
    <!-- 미리보기 -->
    <div class="preview-pane">
      <div class="pane-hd" style="padding:8px 14px;font-size:11px;font-weight:700;color:var(--dim);background:var(--sidebar);border-bottom:1px solid var(--border);margin:-20px -20px 16px -20px">
        👁 미리보기</div>
      <div class="md-view" id="modal-preview"></div>
    </div>
  </div>
  <div class="modal-ft">
    <span class="info" id="modal-info"></span>
    <button class="btn-s btn-pdf" onclick="dlPdfFromModal()" style="font-weight:700">🖨️ PDF 다운로드 (2단 시험지)</button>
    <button class="btn-s" onclick="dlDocxFromModal()">📄 DOCX 다운로드</button>
    <button class="btn-s" onclick="applyEdit()">✅ 적용 후 닫기</button>
  </div>
</div>
</div>

<script>
// ── 상태
let engines=[], selEng=2, selMdl=0, selGrade='중등2', selSubj='영어';
let depth=2, webRef='', webRich='', lastMd='', lastFile='';
let uploadedTexts = [];

// ── 초기화
async function init(){
  engines = await fetch('/api/engines').then(r=>r.json());
  renderEngs(); renderGrades(); renderSubjs();
  selectEng(2); selectGrade('중등2'); selectSubj('영어');
  updateTotal();
  document.getElementById('num-mc').oninput =
  document.getElementById('num-sa').oninput =
  document.getElementById('num-es').oninput = updateTotal;
  checkTunnelUrl();
}

async function checkTunnelUrl(){
  try{
    const r=await fetch('/api/tunnel_url').then(r=>r.json());
    if(r.url){
      document.getElementById('tunnel-banner').style.display='block';
      const a=document.getElementById('tunnel-link');
      a.href=r.url; a.textContent=r.url;
    } else {
      // 터널 URL이 아직 없으면 10초 후 재시도
      setTimeout(checkTunnelUrl, 10000);
    }
  }catch(e){ setTimeout(checkTunnelUrl, 15000); }
}

function updateTotal(){
  const mc = parseInt(document.getElementById('num-mc').value) || 0;
  const sa = parseInt(document.getElementById('num-sa').value) || 0;
  const es = parseInt(document.getElementById('num-es').value) || 0;
  const total = mc + sa + es;
  const parts = [];
  if(mc > 0) parts.push(`객관식 ${mc}`);
  if(sa > 0) parts.push(`주관식 ${sa}`);
  if(es > 0) parts.push(`서술형 ${es}`);
  document.getElementById('total-tip').textContent =
    total === 0
      ? '⚠️ 문항 수를 1 이상 설정하세요'
      : `총 ${total}문항` + (parts.length ? ` (${parts.join(' · ')})` : '');
}

// ── 탭
function go(t,btn){
  document.querySelectorAll('.tab').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
  document.getElementById('tab-make').style.display   = t==='make'  ?'flex':'none';
  document.getElementById('tab-result').style.display = t==='result'?'flex':'none';
  document.getElementById('tab-files').style.display  = t==='files' ?'flex':'none';
  if(t==='files') loadFiles();
}

// ── 엔진
function renderEngs(){
  document.getElementById('eng-list').innerHTML = engines.map((e,i)=>{
    const free=e.models.some(m=>m.cost_tag.includes('무료'));
    const badge=free?'무료 포함':'유료 전용';
    const cls=free?'b-free':'b-paid';
    return `<div class="eng-card${i===selEng?' on':''}" onclick="selectEng(${i})">
      <div class="eng-name">${e.group}</div>
      <span class="badge ${cls}">${badge}</span></div>`;
  }).join('');
}
function selectEng(i){
  selEng=i; renderEngs(); renderMdls(); checkKey();
}
function renderMdls(){
  const e=engines[selEng];
  const s=document.getElementById('model-sel');
  s.innerHTML=e.models.map((m,i)=>{
    const ic=m.cost_tag.includes('완전')?'✅':m.cost_tag.includes('티어')?'🆓':'💰';
    return `<option value="${i}">${ic} ${m.name}  (${m.cost_note})</option>`;
  }).join('');
  s.onchange=()=>{selMdl=+s.value; updateMdlTip()};
  selMdl=0; updateMdlTip();
}
function updateMdlTip(){
  const m=engines[selEng].models[selMdl];
  document.getElementById('model-tip').textContent=m?`${m.perf||''} · ${m.cost_tag}`:'';
}
async function checkKey(){
  const e=engines[selEng];
  const area=document.getElementById('api-area');
  if(e.provider==='ollama'){area.style.display='none';return;}
  area.style.display='';
  if(!e.key_env){document.getElementById('key-badge').innerHTML='';return;}

  const r=await fetch('/api/check_key?env='+e.key_env).then(d=>d.json());
  const badge=document.getElementById('key-badge');
  const savedMsg=document.getElementById('key-saved-msg');
  const delBtn=document.getElementById('btn-del-key');
  const inp=document.getElementById('api-key');

  if(r.ok){
    badge.innerHTML=`<span style="font-size:10px;background:#dcfce7;color:#166534;
      padding:1px 6px;border-radius:8px">✓ ${r.masked}</span>`;
    savedMsg.style.display='inline';
    delBtn.style.display='';
    inp.placeholder='저장된 키 사용 중 (변경 시 새 키 입력 후 💾)';
    inp.value='';
  } else {
    badge.innerHTML='<span style="font-size:10px;background:#fee2e2;color:#991b1b;padding:1px 6px;border-radius:8px">미설정</span>';
    savedMsg.style.display='none';
    delBtn.style.display='none';
    inp.placeholder='API 키 입력 후 💾 저장';
  }
}

function onKeyInput(){
  // 입력 시작하면 저장 버튼 강조
  const v=document.getElementById('api-key').value.trim();
  document.getElementById('btn-save-key').style.background=v?'var(--accent)':'var(--green)';
}

async function saveKey(){
  const e=engines[selEng];
  if(!e.key_env){alert('이 엔진은 API 키가 필요 없습니다');return;}
  const key=document.getElementById('api-key').value.trim();
  if(!key){alert('저장할 키를 입력하세요');return;}
  const r=await fetch('/api/save_key',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({env:e.key_env,key})}).then(d=>d.json());
  if(r.ok){
    document.getElementById('api-key').value='';
    document.getElementById('btn-save-key').style.background='var(--green)';
    // 저장 후 배지 새로고침
    await checkKey();
    // 잠깐 성공 표시
    const btn=document.getElementById('btn-save-key');
    btn.textContent='✓'; setTimeout(()=>btn.textContent='💾',1500);
  } else {
    alert('저장 실패: '+r.msg);
  }
}

async function deleteKey(){
  const e=engines[selEng];
  if(!e.key_env) return;
  if(!confirm('저장된 API 키를 삭제하시겠습니까?')) return;
  await fetch('/api/save_key',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({env:e.key_env,key:''})});
  await checkKey();
}

// ── 학년
const GG=[
  {l:'유아',   cs:['유아']},
  {l:'초등',   cs:['초등1','초등2','초등3','초등4','초등5','초등6']},
  {l:'중학교', cs:['중등1','중등2','중등3']},
  {l:'고등학교',cs:['고등1','고등2','고등3']},
];
const GL={'유아':'유아\n(파닉스)','초등1':'초등\n1학년','초등2':'초등\n2학년',
  '초등3':'초등\n3학년','초등4':'초등\n4학년','초등5':'초등\n5학년','초등6':'초등\n6학년',
  '중등1':'중1','중등2':'중2','중등3':'중3',
  '고등1':'고1','고등2':'고2','고등3':'고3'};
function renderGrades(){
  let h='';
  GG.forEach(g=>{
    h+=`<div class="g-sep">${g.l}</div>`;
    g.cs.forEach(c=>h+=`<div class="g-item${c===selGrade?' on':''}" onclick="selectGrade('${c}')">${GL[c]}</div>`);
  });
  document.getElementById('grade-grid').innerHTML=h;
}
function selectGrade(c){selGrade=c;renderGrades();}

// ── 과목
const SUBJS=['국어','영어','수학','과학','사회','역사'];
const SICO={국어:'📖',영어:'🌍',수학:'📐',과학:'🔬',사회:'🏛',역사:'📜'};
function renderSubjs(){
  document.getElementById('subj-grid').innerHTML=SUBJS.map(s=>
    `<div class="s-item${s===selSubj?' on':''}" onclick="selectSubj('${s}')">${SICO[s]} ${s}</div>`
  ).join('');
}
function selectSubj(s){selSubj=s;renderSubjs();}

// ── 파일 업로드
function dragOver(e){e.preventDefault();document.getElementById('drop-zone').classList.add('drag')}
function dragLeave(){document.getElementById('drop-zone').classList.remove('drag')}
function dropFiles(e){e.preventDefault();dragLeave();uploadFiles(e.dataTransfer.files)}

function renderChips(){
  const chips=document.getElementById('file-chips');
  if(!uploadedTexts.length){chips.innerHTML='';return;}
  chips.innerHTML=uploadedTexts.map((f,i)=>{
    const kb=(f.chars/1000).toFixed(1);
    const icon=f.name.endsWith('.pdf')?'📄':
               f.name.match(/\.(jpg|jpeg|png|gif|bmp|webp)$/i)?'🖼':
               f.name.endsWith('.hwp')?'📝':
               f.name.match(/\.(xlsx|xls)$/i)?'📊':'📃';
    return `<span class="chip" onclick="removeFile(${i})" title="클릭하면 제거">
      ${icon} ${f.name} <span class="chip-size">${kb}K자</span> ✕</span>`;
  }).join('');
}

function syncPassage(){
  const ta=document.getElementById('passage');
  if(uploadedTexts.length){
    ta.value=uploadedTexts.map(f=>`[출처: ${f.name}]\n${f.text}`).join('\n\n---\n\n');
    ta.placeholder='파일에서 추출된 텍스트 (직접 편집 가능)';
  } else {
    ta.placeholder='또는 지문·문제를 직접 붙여넣으세요...';
  }
}

async function uploadFiles(files){
  if(!files.length) return;

  // 업로드 중 표시
  const zone=document.getElementById('drop-zone');
  zone.style.opacity='.5';
  zone.querySelector('div').textContent='⏳';

  const fd=new FormData();
  [...files].forEach(f=>fd.append('files',f));
  const res=await fetch('/api/upload',{method:'POST',body:fd}).then(r=>r.json());

  zone.style.opacity='1';
  zone.querySelector('div').textContent='📂';

  uploadedTexts.push(...res);
  renderChips();
  syncPassage();
}

function removeFile(i){
  uploadedTexts.splice(i,1);
  renderChips();
  syncPassage();
}

// ── 웹 검색
function toggleWeb(){
  // 검색 사용 체크박스 — 검색 버튼 활성/비활성만 제어
  const on=document.getElementById('web-on').checked;
  document.getElementById('srch-btn').disabled=!on;
  if(!on){webRef='';document.getElementById('srch-ok').innerHTML='';
          document.getElementById('srch-st').textContent='';}
}
function setDepth(d){
  depth=d;
  document.querySelectorAll('.d-btn').forEach((b,i)=>b.classList.toggle('on',i+1===d));
}
async function doSearch(){
  const scope=document.getElementById('scope').value.trim();
  const kw=document.getElementById('keywords').value.trim();
  if(!scope&&!kw){alert('진도 범위 또는 키워드를 입력하세요');return;}
  const btn=document.getElementById('srch-btn');
  const st=document.getElementById('srch-st');
  btn.disabled=true; st.textContent='🔍 검색 중...'; webRef='';
  document.getElementById('srch-ok').innerHTML='';

  const resp=await fetch('/api/search',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({grade:selGrade,subject:selSubj,scope,keywords:kw,depth})});
  const reader=resp.body.getReader();const dec=new TextDecoder();let buf='';
  while(true){
    const{done,value}=await reader.read(); if(done) break;
    buf+=dec.decode(value);
    const lines=buf.split('\n\n'); buf=lines.pop();
    for(const chunk of lines){
      const ev=chunk.split('\n').find(l=>l.startsWith('event:'))?.slice(6).trim();
      const dt=chunk.split('\n').find(l=>l.startsWith('data:'))?.slice(5).trim();
      if(!ev||!dt) continue;
      const d=JSON.parse(dt);
      if(ev==='ping') st.textContent='🔍 검색 중...';
      if(ev==='done'){
        webRef=d.web_ref;
        webRich=d.web_rich||'';
        st.textContent=`✓ ${d.count}건 수집 · 문제패턴 ${d.q_count}개`;
        const hasPassage=document.getElementById('passage').value.trim()||uploadedTexts.length;
        const modeTag = hasPassage ? '🌐 웹+지문 혼합' : '🌐 웹 전용 출제 가능';
        document.getElementById('srch-ok').innerHTML=`<span class="srch-ok">${modeTag} 완료</span>`;
      }
      if(ev==='error') st.textContent='❌ '+d.msg;
    }
  }
  btn.disabled=false;
}

// ── 문제지/답지 상태
let examMd='', answerMd='', examFile='', answerFile='';
let curResult='exam'; // 현재 보는 탭

function switchResult(type){
  curResult=type;
  document.getElementById('st-exam').classList.toggle('on',type==='exam');
  document.getElementById('st-answer').classList.toggle('on',type==='answer');
  const md = type==='exam' ? examMd : answerMd;
  lastMd   = md;
  lastFile = type==='exam' ? examFile : answerFile;
  document.getElementById('result-area').innerHTML=
    `<div class="md-view">${marked.parse(md)}</div>`;
  // 답지 경고 배너
  const warn=document.getElementById('answer-warn');
  if(warn) warn.style.display = type==='answer'?'block':'none';
}

// ── 문제 생성
async function doGenerate(){
  // ── 변수 먼저 선언
  const passage   = document.getElementById('passage').value.trim();
  const hasUpload = uploadedTexts.length > 0;
  const hasWebRich= webRich.length > 0;
  const numMc     = parseInt(document.getElementById('num-mc').value) || 0;
  const numSa     = parseInt(document.getElementById('num-sa').value) || 0;
  const numEs     = parseInt(document.getElementById('num-es').value) || 0;
  const scope     = document.getElementById('scope').value.trim();
  const original  = document.getElementById('original').value.trim() || '원본 문제 없음';
  const academy   = document.getElementById('academy-name').value.trim() || '미래학원';
  const showA     = document.getElementById('show-academy').checked;
  const apiKey    = document.getElementById('api-key').value.trim();

  // ── 유효성 검사
  if(!passage && !hasUpload && !hasWebRich){
    alert('① 지문/파일을 입력하거나\n② 웹 검색을 먼저 실행한 후 생성하세요.\n\n웹 검색만으로도 문제 생성이 가능합니다!');
    return;
  }
  if(numMc + numSa + numEs === 0){
    alert('문항 수를 최소 1개 이상 설정하세요.');
    return;
  }

  // ── 웹 전용 모드 판별 및 확인
  const isWebOnly = !passage && !hasUpload && hasWebRich;
  if(isWebOnly){
    const ok=confirm('🌐 웹 검색 전용 모드\n\n지문 없이 웹에서 수집한 자료만으로 문제를 출제합니다.\n계속 진행할까요?');
    if(!ok) return;
  }

  // ── UI: 결과 탭으로 전환
  document.querySelectorAll('.tab').forEach(b=>b.classList.remove('on'));
  document.querySelectorAll('.tab')[1].classList.add('on');
  document.getElementById('tab-make').style.display='none';
  document.getElementById('tab-result').style.display='flex';
  document.getElementById('tab-files').style.display='none';

  // ── 진행 표시 초기화
  const allTasks=[];
  if(numMc>0) allTasks.push({key:'mc', label:`객관식 ${numMc}문항`});
  if(numSa>0) allTasks.push({key:'sa', label:`주관식 ${numSa}문항`});
  if(numEs>0) allTasks.push({key:'es', label:`서술형 ${numEs}문항`});
  document.getElementById('prog-list').innerHTML=allTasks.map(t=>
    `<div class="prog-item"><div class="dot" id="dot-${t.key}"></div><span>${t.label}</span></div>`
  ).join('');
  document.getElementById('result-subtabs').style.display='none';
  document.getElementById('result-area').innerHTML=
    `<div style="text-align:center;color:var(--dim);padding:30px 20px;font-size:14px">
       ⏳ AI가 문제를 생성하고 있습니다...<br>
       <small style="opacity:.7;margin-top:6px;display:block">
         ${selEng===2 ? '🏠 Gemma 로컬 모델: 문항당 20~40초 소요 (정상)' : '☁️ 클라우드: 보통 20~40초 소요'}
       </small><br>
       <small style="opacity:.5;display:block">왼쪽 진행 현황을 확인하세요</small>
     </div>`;
  document.getElementById('btn-gen').disabled=true;
  document.getElementById('btn-edit').style.display='none';
  document.getElementById('btn-docx').style.display='none';
  document.getElementById('btn-pdf').style.display='none';
  examMd=''; answerMd=''; examFile=''; answerFile=''; lastMd=''; lastFile='';

  const body={engine_group:selEng,engine_model:selMdl,api_key:apiKey,
    subject:selSubj,grade:selGrade,passage,original,web_ref:webRef,scope,
    academy_name:academy,show_academy:showA,num_mc:numMc,num_sa:numSa,num_es:numEs,
    web_rich: isWebOnly ? webRich : ''};

  const resp=await fetch('/api/generate',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const reader=resp.body.getReader();const dec=new TextDecoder();let buf='';
  while(true){
    const{done,value}=await reader.read();if(done)break;
    buf+=dec.decode(value);
    const lines=buf.split('\n\n');buf=lines.pop();
    for(const chunk of lines){
      const ev=chunk.split('\n').find(l=>l.startsWith('event:'))?.slice(6).trim();
      const dt=chunk.split('\n').find(l=>l.startsWith('data:'))?.slice(5).trim();
      if(!ev||!dt) continue;
      const d=JSON.parse(dt);
      if(ev==='progress'){const dot=document.getElementById('dot-'+d.key);if(dot)dot.className='dot spin';}
      if(ev==='chunk'){const dot=document.getElementById('dot-'+d.key);if(dot)dot.className='dot done';}
      if(ev==='done'){
        examMd=d.exam_md; answerMd=d.answer_md;
        examFile=d.exam_file; answerFile=d.answer_file;
        curResult='exam'; lastMd=examMd; lastFile=examFile;
        document.querySelectorAll('.dot').forEach(x=>x.className='dot done');
        document.getElementById('result-subtabs').style.display='block';
        document.getElementById('st-exam').classList.add('on');
        document.getElementById('st-answer').classList.remove('on');
        document.getElementById('result-area').innerHTML=
          `<div id="answer-warn" style="display:none;background:#fee2e2;border:1.5px solid #fca5a5;
           border-radius:8px;padding:10px 14px;margin-bottom:12px;font-size:13px;color:#991b1b">
           ⚠️ <strong>교사용 답지입니다.</strong> 학생에게 배포하지 마세요.</div>
           <div class="md-view">${marked.parse(examMd)}</div>`;
        document.getElementById('btn-edit').style.display='';
        document.getElementById('btn-docx').style.display='';
        document.getElementById('btn-pdf').style.display='';
      }
      if(ev==='error'){
        const msg=(d.msg||'').replace(/\n/g,'<br>');
        let hint='';
        if(msg.includes('404')) hint='<br><small>💡 해당 모델이 이 API 키에서 지원되지 않습니다. 다른 모델을 선택하세요.</small>';
        else if(msg.includes('403')) hint='<br><small>💡 API 키 권한 부족. 키를 확인하거나 다른 모델을 선택하세요.</small>';
        else if(msg.includes('API 키')) hint='<br><small>💡 API 키를 입력/저장하세요.</small>';
        document.getElementById('result-area').innerHTML=
          `<div style="background:#fff0f0;border:1.5px solid #fca5a5;border-radius:10px;
           padding:20px 24px;line-height:2;font-size:13px">${msg}${hint}</div>`;
        document.getElementById('btn-gen').disabled=false;
      }
    }
  }
  document.getElementById('btn-gen').disabled=false;
}

// ── 파일 목록
async function loadFiles(){
  const files=await fetch('/api/files').then(r=>r.json());
  const el=document.getElementById('flist');
  if(!files.length){el.innerHTML='<div style="color:var(--dim)">저장된 파일 없음</div>';return;}
  // 문제지/답지 쌍으로 그룹화해서 표시
  const pairs={};
  files.forEach(f=>{
    const key=f.name.replace('_문제지.md','').replace('_답지.md','');
    if(!pairs[key]) pairs[key]={mtime:f.mtime};
    if(f.name.includes('_문제지')) pairs[key].exam=f.name;
    else if(f.name.includes('_답지')) pairs[key].answer=f.name;
    else pairs[key].single=f.name;
  });
  el.innerHTML=Object.entries(pairs).map(([key,v])=>`
    <div style="background:var(--panel);border:1.5px solid var(--border);border-radius:8px;padding:10px 13px;margin-bottom:4px">
      <div style="font-size:11px;color:var(--dim);margin-bottom:6px">📁 ${key.slice(-19)} · ${v.mtime||''}</div>
      <div style="display:flex;gap:6px">
        ${v.exam   ? `<button class="btn-s" style="font-size:12px;padding:5px 12px" onclick="openFile('${v.exam}',false)">📝 문제지</button>`:''}
        ${v.answer ? `<button class="btn-s btn-blue" style="font-size:12px;padding:5px 12px" onclick="openFile('${v.answer}',true)">📋 답지</button>`:''}
        ${v.single ? `<button class="btn-s" style="font-size:12px;padding:5px 12px" onclick="openFile('${v.single}',false)">📄 열기</button>`:''}
      </div>
    </div>`
  ).join('');
}
async function openFile(name, isAnswer=false){
  const text=await fetch('/api/files/'+encodeURIComponent(name)).then(r=>r.text());
  lastMd=text; lastFile=name;
  document.querySelectorAll('.tab').forEach(b=>b.classList.remove('on'));
  document.querySelectorAll('.tab')[1].classList.add('on');
  document.getElementById('tab-make').style.display='none';
  document.getElementById('tab-result').style.display='flex';
  document.getElementById('tab-files').style.display='none';
  document.getElementById('prog-list').innerHTML='';
  document.getElementById('result-subtabs').style.display='none';
  const warn = isAnswer
    ? `<div style="background:#fee2e2;border:1.5px solid #fca5a5;border-radius:8px;
        padding:10px 14px;margin-bottom:12px;font-size:13px;color:#991b1b">
        ⚠️ <strong>교사용 답지입니다.</strong> 학생에게 배포하지 마세요.</div>` : '';
  document.getElementById('result-area').innerHTML=
    warn+`<div class="md-view">${marked.parse(text)}</div>`;
  document.getElementById('btn-edit').style.display='';
  document.getElementById('btn-docx').style.display='';
        document.getElementById('btn-pdf').style.display='';
}

// ── 편집 모달
function openEdit(){
  document.getElementById('edit-ta').value=lastMd;
  syncPreview();
  updateModalInfo();
  document.getElementById('modal-bg').classList.add('open');
}
function closeEdit(){document.getElementById('modal-bg').classList.remove('open')}
function closeEditOutside(e){if(e.target===document.getElementById('modal-bg'))closeEdit();}
function syncPreview(){
  const txt=document.getElementById('edit-ta').value;
  document.getElementById('modal-preview').innerHTML=marked.parse(txt);
  updateModalInfo();
}
function updateModalInfo(){
  const txt=document.getElementById('edit-ta').value;
  document.getElementById('modal-info').textContent=
    `${txt.length}자 · ${txt.split('\n').length}줄`;
}
function applyEdit(){
  lastMd=document.getElementById('edit-ta').value;
  document.getElementById('result-area').innerHTML=
    `<div class="md-view">${marked.parse(lastMd)}</div>`;
  closeEdit();
}
function printModal(){
  // 미리보기 내용으로 인쇄
  const content=document.getElementById('modal-preview').innerHTML;
  const win=window.open('','_blank');
  win.document.write(`<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
  <title>미래학원 문제지</title>
  <style>
    body{font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
         padding:20mm 18mm;font-size:12pt;line-height:1.8;color:#111}
    h1{font-size:18pt;text-align:center;border-bottom:2px solid #333;padding-bottom:8px;margin-bottom:14px}
    h2{font-size:14pt;color:#1a4c8b;margin:16px 0 8px}
    h3{font-size:12pt;color:#b45309;margin:12px 0 6px}
    hr{border:none;border-top:1px solid #ccc;margin:12px 0}
    blockquote{border-left:3px solid #c96442;padding-left:10px;color:#555;font-size:10pt}
    @media print{@page{margin:15mm}}
  </style></head><body>${content}</body></html>`);
  win.document.close();
  setTimeout(()=>win.print(),400);
}
async function dlDocxFromModal(){
  const txt=document.getElementById('edit-ta').value;
  await downloadDocx(txt);
}

// ── 모달에서 PDF 다운로드 (2단 시험지)
async function dlPdfFromModal(){
  if(!examMd){alert('문제지 내용이 없습니다. 먼저 문제를 생성하세요.');return;}
  const academy=document.getElementById('academy-name').value.trim()||'미래학원';
  const subject=document.getElementById('subject').value||'영어';
  const grade  =document.getElementById('grade').value||'중등2';
  const scope  =document.getElementById('scope').value||'';
  const btn=event.target;
  btn.textContent='⏳ PDF 생성중...'; btn.disabled=true;
  try{
    const r=await fetch('/api/export/pdf',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({exam_md:examMd,answer_md:answerMd,
        academy_name:academy,subject,grade,scope})});
    if(!r.ok){const e=await r.json().catch(()=>({error:'오류'}));alert('PDF 실패: '+e.error);return;}
    const blob=await r.blob();
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=`${grade}_${subject}_exam.pdf`;
    a.click();
  }catch(e){alert('PDF 오류: '+e.message);}
  finally{btn.textContent='🖨️ PDF 다운로드 (2단 시험지)';btn.disabled=false;}
}

// ── DOCX 다운로드
async function dlDocx(){await downloadDocx(lastMd);}
async function downloadDocx(md){
  const academy=document.getElementById('academy-name').value.trim()||'미래학원';
  const showA=document.getElementById('show-academy').checked;
  const blob=await fetch('/api/export/docx',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({markdown:md,academy_name:academy,show_academy:showA})}).then(r=>r.blob());
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=(lastFile||'exam').replace('.md','')+'.docx';
  a.click();
}

// ── PDF 인쇄용 다운로드
async function dlPdf(){
  if(!examMd){alert('먼저 문제를 생성하세요.');return;}
  const academy=document.getElementById('academy-name').value.trim()||'미래학원';
  const subject=document.getElementById('subject').value||'영어';
  const grade  =document.getElementById('grade').value||'중등2';
  const scope  =document.getElementById('scope').value||'';
  const btn=document.getElementById('btn-pdf');
  btn.textContent='⏳ PDF 생성중...'; btn.disabled=true;
  try{
    const r=await fetch('/api/export/pdf',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        exam_md:examMd, answer_md:answerMd,
        academy_name:academy, subject, grade, scope
      })});
    if(!r.ok){
      const err=await r.json().catch(()=>({error:'알 수 없는 오류'}));
      alert('PDF 생성 실패: '+err.error); return;
    }
    const blob=await r.blob();
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=`${grade}_${subject}_exam.pdf`;
    a.click();
  }catch(e){
    alert('PDF 오류: '+e.message);
  }finally{
    btn.textContent='🖨️ PDF 인쇄용'; btn.disabled=false;
  }
}

// ── 초기화
function clearAll(){
  uploadedTexts=[];
  renderChips();
  syncPassage();
  document.getElementById('original').value='';
  document.getElementById('scope').value='';
  document.getElementById('keywords').value='';
  document.getElementById('web-on').checked=true;
  webRef='';
  document.getElementById('srch-ok').innerHTML='';
  document.getElementById('srch-st').textContent='';
  // 파일 input 초기화
  document.getElementById('file-input').value='';
}

init();
</script>
</body>
</html>"""

TUNNEL_URL_FILE = Path("/tmp/miraehakwon_tunnel_url.txt")

@app.route("/api/tunnel_url")
def api_tunnel_url():
    try:
        if TUNNEL_URL_FILE.exists():
            url = TUNNEL_URL_FILE.read_text().strip()
            if url:
                return jsonify({"url": url})
    except Exception:
        pass
    return jsonify({"url": ""})

if __name__ == "__main__":
    import webbrowser, threading
    threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open("http://localhost:7777")), daemon=True).start()
    print("🌐  http://localhost:7777  열리는 중...")
    app.run(host="0.0.0.0", port=7777, debug=False, threaded=True)
