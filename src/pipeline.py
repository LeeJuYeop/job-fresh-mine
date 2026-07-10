"""
job-fresh 메인 파이프라인 — GitHub Actions cron으로 매일 실행되는 진입점.
소스 어댑터(zighangApi)를 호출해 수집 구간(전날 07:00 KST ~ 실행 시점) 내 등록 공고와 본문을 받아,
(옵션) Lambda AI 프록시로 한줄요약(ai_comment)·개인화 분석(personal_comment)을 요청하고,
`jobs/{YYYY-MM-DD}/`에 YAML frontmatter + 마크다운 본문 파일로 저장한다.
향후 플랫폼 추가 시 어댑터 모듈을 병렬로 붙이는 구조.

AI 실패·미설정 시에도 저장은 진행한다(ai_status: skipped — 최후 폴백).
커밋/푸시는 파이썬이 아닌 워크플로(yml)가 담당한다.
"""

import datetime
import json
import logging
import os
import pathlib
import re
import sys
import zoneinfo

import zighangApi

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

KST = zoneinfo.ZoneInfo("Asia/Seoul")

# 하루 탐색 개수 상한 — 잠정치. 확정 수치는 미결(Pending, UL-0005).
DAILY_LIMIT = 20

# 수집 구간 시작: 전날 이 시각(KST)부터 실행 시점까지. 크론(매일 KST 07:00)과 맞물려
# 구간이 이어지므로 실행 간 중복이 발생하지 않는다.
# 주기·시점 커스터마이징은 추후 온보딩 스크립트에서 지원 예정.
COLLECT_START_HOUR = 7

# filters.json 스키마 버전 — 구조 변경 감지용
FILTERS_SCHEMA_VERSION = 1

# 공고 저장 파일의 frontmatter 스키마 버전 — 각 fork가 소비하는 사실상의 API
JOB_SCHEMA_VERSION = 1

# 저장소 루트 — 설정·프로필·저장 폴더는 모두 루트 기준(src/ 내부가 아님)
ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]

# 공고 저장 루트 폴더 (하위에 일별 폴더 생성)
JOBS_DIR = ROOT_DIR / "jobs"

# 파일명 최대 길이(.md 포함) — Windows 경로 길이 제한 여유분 확보
MAX_FILENAME_LEN = 150

# Lambda AI 프록시 엔드포인트. 미설정 시 AI 단계를 건너뛴다.
AI_PROXY_URL = os.environ.get("AI_PROXY_URL", "")


# ── 설정·프로필 로드 ──────────────────────────────────────────────────────────

def load_config() -> dict:
    """filters.json(평탄 스키마)을 읽어 반환한다. 없거나 파싱할 수 없으면 종료한다."""
    try:
        with open(ROOT_DIR / "filters.json", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        log.error("filters.json 파일을 찾을 수 없습니다 — 수집을 종료합니다.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error("filters.json 파싱 실패 (%s) — 수집을 종료합니다.", e)
        sys.exit(1)

    version = config.get("schema_version")
    if version != FILTERS_SCHEMA_VERSION:
        log.warning(
            "filters.json schema_version 불일치 (기대: %s, 실제: %s) — 계속 진행하나 필터가 적용되지 않을 수 있습니다.",
            FILTERS_SCHEMA_VERSION, version,
        )
    return config

_RESUME_PATH = ROOT_DIR / "resume.json"
_user_profile: dict | None = None
_profile_loaded = False


def load_user_profile() -> dict | None:
    """resume.json(개인화 분석용 프로필)을 읽어 반환한다. 없으면 개인화 비활성(None)."""
    global _user_profile, _profile_loaded
    if _profile_loaded:
        return _user_profile
    _profile_loaded = True
    try:
        with _RESUME_PATH.open(encoding="utf-8") as f:
            profile: dict = json.load(f)
        _user_profile = profile
        log.info("[resume] 로드 완료: 기술스택 %d개, 프로젝트 %d개",
                 len(profile.get("tech_stack", [])),
                 len(profile.get("projects", [])))
    except FileNotFoundError:
        log.info("[resume] resume.json 없음 — 개인화 비활성")
    except Exception as e:
        log.warning("[resume] 로드 실패 (%s) — 개인화 비활성", e)
    return _user_profile


# ── AI 프록시 (Lambda) ─────────────────────────────────────────────────────────

def request_ai_analysis(job: dict, content: str, profile: dict | None) -> dict | None:
    """Lambda AI 프록시에 한줄요약(+profile 제공 시 개인화 분석)을 요청한다.
    반환: {"ai_comment": str, "personal_comment": str(옵션)} 또는 None(생략·실패).

    프롬프트는 Lambda가 소유하며, fork는 공고 데이터(+선택 profile)만 구조화 JSON으로
    전송한다. 엔드포인트·요청 스키마·OIDC 토큰 발급 절차가 아직 미확정이라 스텁 상태.
    """
    if not AI_PROXY_URL:
        log.info("[AI] AI_PROXY_URL 미설정 — 요약 생략")
        return None
    # TODO: GitHub OIDC 토큰 발급(ACTIONS_ID_TOKEN_REQUEST_URL/TOKEN)
    #       → 프록시 POST {"job": job, "content": content, "profile": profile}
    #       → {"ai_comment", "personal_comment"} 파싱
    log.warning("[AI] 프록시 호출 미구현 — 요약 생략")
    return None


# ── 파일명·frontmatter 구성 ────────────────────────────────────────────────────

# Windows 금지문자 + 대괄호(파일명 구분자와 충돌) + 제어문자
_FORBIDDEN_CHARS = re.compile(r'[\\/:*?"<>|\[\]\x00-\x1f]')


def _clean_part(text: str | None) -> str:
    """파일명 구성 요소에서 금지문자를 제거하고 공백을 정돈한다. 빈 값은 '-'로 대체."""
    cleaned = _FORBIDDEN_CHARS.sub("", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "-"


def _deadline_label(job: dict) -> str:
    """마감 표기 문자열을 만든다. '마감일' 타입은 end_date 기준 'YY-MM-DD마감'으로,
    그 외(상시채용·채용시마감)는 유형명 그대로 반환한다.
    """
    dtype = job.get("deadline_type") or ""
    end_date = job.get("end_date")
    if dtype == "마감일" and end_date:
        try:
            return f"{datetime.datetime.fromisoformat(end_date):%y-%m-%d}마감"
        except ValueError:
            pass
    return dtype


def build_filename(job: dict) -> str:
    """`[회사명][경력][채용유형][지역][마감] {title}.md` 형식의 파일명을 만든다.
    복수 지역은 첫 지역만 표기(전체 지역은 frontmatter에 기록).
    마감 자리는 YY-MM-DD마감(마감일 타입) 또는 유형명(상시채용·채용시마감).
    """
    regions = job.get("regions") or []
    prefix = "".join(
        f"[{_clean_part(p)}]"
        for p in (job.get("company"), job.get("career"),
                  job.get("employ_type"), regions[0] if regions else "",
                  _deadline_label(job))
    )
    title = _clean_part(job.get("title"))
    max_title_len = MAX_FILENAME_LEN - len(prefix) - len(" .md")
    if len(title) > max_title_len:
        title = title[:max(max_title_len, 10)].rstrip()
    return f"{prefix} {title}.md"


def _yaml_value(value) -> str:
    """frontmatter 값 직렬화 — JSON 스칼라/배열은 유효한 YAML이므로 json.dumps를 쓴다."""
    return json.dumps(value, ensure_ascii=False)


def build_document(job: dict, content: str, ai: dict | None, now: datetime.datetime) -> str:
    """frontmatter + 마크다운 본문으로 저장 문서 전문을 만든다."""
    collected_at = now.isoformat(timespec="seconds")
    ai = ai or {}
    ai_comment = (ai.get("ai_comment") or "").strip()
    personal_comment = (ai.get("personal_comment") or "").strip()
    ai_status = "done" if ai_comment else "skipped"

    lines = [
        "---",
        f"schema_version: {JOB_SCHEMA_VERSION}",
        f"id: {_yaml_value(job['id'])}",
        f"url: {_yaml_value(job['url'])}",
        f"company: {_yaml_value(job.get('company', ''))}",
        f"title: {_yaml_value(job.get('title', ''))}",
        f"regions: {_yaml_value(job.get('regions', []))}",
        f"career: {_yaml_value(job.get('career', ''))}",
        f"employ_type: {_yaml_value(job.get('employ_type', ''))}",
        f"keywords: {_yaml_value(job.get('keywords', []))}",
        f"deadline: {_yaml_value(_deadline_label(job))}",
        f"end_date: {_yaml_value(job.get('end_date'))}",
        f"collected_at: {_yaml_value(collected_at)}",
    ]
    if ai_comment:
        lines.append(f"ai_comment: {_yaml_value(ai_comment)}")
    if personal_comment:
        lines.append(f"personal_comment: {_yaml_value(personal_comment)}")
    lines.append(f"ai_status: {ai_status}")
    lines.append("---")
    lines.append("")
    lines.append(content.strip())
    lines.append("")
    return "\n".join(lines)


# ── 저장 ──────────────────────────────────────────────────────────────────────

def save_job(job: dict, document: str, now: datetime.datetime) -> pathlib.Path:
    """`jobs/{YYYY-MM-DD}/`에 문서를 저장하고 경로를 반환한다.
    파일명이 겹칠 때 같은 공고(id 일치, 재실행 등)면 덮어쓰고,
    별개 공고(같은 회사·제목)면 id 끝 8자를 붙여 구분한다.
    """
    day_dir = JOBS_DIR / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    path = day_dir / build_filename(job)
    if path.exists() and f"id: {_yaml_value(job['id'])}" not in path.read_text(encoding="utf-8"):
        path = path.with_name(f"{path.stem} ({job['id'][-8:]}).md")

    path.write_text(document, encoding="utf-8", newline="\n")
    return path


# ── 공고 단건 처리 ────────────────────────────────────────────────────────────

def process_job(job: dict, content: str) -> pathlib.Path:
    """공고 1건을 처리한다: (옵션) AI 분석 → frontmatter 구성 → 일별 폴더에 저장."""
    profile = load_user_profile()
    ai = request_ai_analysis(job, content, profile)

    now = datetime.datetime.now(KST)
    document = build_document(job, content, ai, now)
    path = save_job(job, document, now)
    log.info("[저장] %s", path)
    return path


# ── 메인 오케스트레이션 ────────────────────────────────────────────────────────

def main():
    config = load_config()
    log.info("=== 수집 시작 | 필터(depthTwos): %s ===", config.get("depthTwos", []))

    now = datetime.datetime.now(KST)
    since = (now - datetime.timedelta(days=1)).replace(
        hour=COLLECT_START_HOUR, minute=0, second=0, microsecond=0,
    )
    jobs = zighangApi.fetch_jobs(config, since=since, limit=DAILY_LIMIT)

    saved = 0
    skipped = 0
    failed = 0
    for job in jobs:
        content = zighangApi.fetch_zighang_content(job["url"])
        if content is None:
            skipped += 1
            continue
        try:
            process_job(job, content)
            saved += 1
        except Exception as e:
            log.exception("파이프라인 실패 (%s): %s", job["url"], e)
            failed += 1

    log.info(
        "=== 수집 완료 | 저장 %d건 | 본문 없음 건너뜀 %d건 | 실패 %d건 ===",
        saved, skipped, failed,
    )


if __name__ == "__main__":
    main()
