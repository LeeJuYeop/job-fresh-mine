"""
직행(zighang) API 어댑터 — 공식 API에서 당일 등록된 채용공고 목록과 본문을 가져온다.
오케스트레이션(설정 로드, AI, 저장)은 pipeline.py 담당이며, 본 모듈은 직행 API
호출과 응답 정규화만 책임진다. 향후 플랫폼 추가 시 같은 형태의 어댑터를 병렬로 둔다.

당일 등록분만 조회하므로 실행 간 중복이 구조적으로 발생하지 않는다(별도 dedup 없음).
본문 추출에 실패한 공고는 폴백 없이 건너뛴다(None 반환 — 호출부가 건너뜀).
"""

import datetime
import logging
import re
import zoneinfo

import requests

log = logging.getLogger(__name__)

KST = zoneinfo.ZoneInfo("Asia/Seoul")

# 사이트 요청 시 봇 차단을 줄이기 위한 브라우저 헤더
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ── 본문 추출 (ProseMirror → 마크다운) ─────────────────────────────────────────

def _pm_node_to_lines(node: dict) -> list[str]:
    """ProseMirror 노드를 마크다운 줄 목록으로 재귀 변환한다."""
    ntype = node.get("type", "")
    children = node.get("content") or []

    if ntype == "text":
        return [node.get("text", "")]
    if ntype == "hardBreak":
        return ["\n"]
    if ntype in ("doc",):
        return [line for c in children for line in _pm_node_to_lines(c)]
    if ntype == "paragraph":
        text = "".join(line for c in children for line in _pm_node_to_lines(c)).strip()
        return [text] if text else [""]
    if ntype == "heading":
        level = node.get("attrs", {}).get("level", 2)
        text = "".join(line for c in children for line in _pm_node_to_lines(c)).strip()
        return [f"{'#' * level} {text}"]
    if ntype == "bulletList":
        lines = []
        for item in children:
            text = " ".join(t for c in (item.get("content") or []) for t in _pm_node_to_lines(c)).strip()
            if text:
                lines.append(f"- {text}")
        return lines
    if ntype == "orderedList":
        lines = []
        for i, item in enumerate(children, 1):
            text = " ".join(t for c in (item.get("content") or []) for t in _pm_node_to_lines(c)).strip()
            if text:
                lines.append(f"{i}. {text}")
        return lines
    if ntype == "image":
        return []
    # 알 수 없는 노드: 자식 재귀
    return [line for c in children for line in _pm_node_to_lines(c)]


def prosemirror_to_markdown(doc: dict) -> str:
    """직행 summary 필드의 ProseMirror JSON을 마크다운 문자열로 변환한다."""
    lines = [line for node in (doc.get("content") or []) for line in _pm_node_to_lines(node)]
    text = "\n".join(lines)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _zighang_career(career_min: int, career_max: int) -> str:
    """직행 careerMin/careerMax 값을 경력 레이블로 변환한다.
    min=0, max=0 → 신입 / min=0, max>0 → 무관 / min>0 → 경력
    """
    if career_min == 0 and career_max == 0:
        return "신입"
    if career_min == 0:
        return "무관"
    return "경력"


def fetch_zighang_content(url: str) -> str | None:
    """직행 공고 URL에서 ID를 추출해 상세 API를 호출하고 본문을 마크다운으로 반환한다.
    상세 API 호출 실패 또는 본문 없음 시 None을 반환한다(폴백 없음 — 호출부가 건너뜀).
    """
    match = re.search(r'/recruitment/([a-f0-9-]+)', url)
    if not match:
        return None
    recruitment_id = match.group(1)
    try:
        resp = requests.get(
            f"https://api.zighang.com/api/recruitments/{recruitment_id}",
            headers=BROWSER_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        # 본문 ProseMirror 문서: 현행 응답은 data.content, 구버전 응답은 data.summary
        doc = data.get("content") or data.get("summary")
        if not doc or not doc.get("content"):
            log.warning("[직행] 상세 API 본문 없음 (%s) — 건너뜀", url)
            return None
        return prosemirror_to_markdown(doc)
    except Exception as e:
        log.warning("[직행] 상세 API 실패 (%s): %s — 건너뜀", url, e)
        return None


# ── 당일 공고 목록 수집 ────────────────────────────────────────────────────────

def fetch_today_jobs(config: dict, limit: int) -> list[dict]:
    """직행 공개 API에서 당일(KST 00:00 이후) 등록 공고 메타데이터를 최신순으로 수집한다.

    API: https://api.zighang.com/api/recruitments/v3
    당일 조회: sortCondition=LATEST + startDate={KST 당일 00시, LocalDateTime 형식}
    지원 필터: depthTwos(직무), regions(지역), employeeTypes(채용유형),
              careerMin/careerMax(경력), educations(학력) — keywords.json 최상위 필드.

    반환값: 공고 메타데이터 dict 목록 (최대 limit건)
      {"id": "zighang-{UUID}", "url", "company", "title", "regions", "career", "employ_type"}
    """
    today_start = datetime.datetime.now(KST).strftime("%Y-%m-%dT00:00:00")

    params: list[tuple] = [
        ("page", 0),
        ("size", limit),
        ("sortCondition", "LATEST"),
        ("orderCondition", "DESC"),
        ("startDate", today_start),
    ]

    for key in ("depthTwos", "regions", "employeeTypes", "educations"):
        for val in config.get(key, []):
            params.append((key, val))

    career_min = config.get("careerMin")
    career_max = config.get("careerMax")
    if career_min is not None:
        params.append(("careerMin", career_min))
    if career_max is not None:
        params.append(("careerMax", career_max))

    jobs: list[dict] = []
    try:
        resp = requests.get(
            "https://api.zighang.com/api/recruitments/v3",
            params=params,
            headers=BROWSER_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()

        data = resp.json()
        for item in data.get("data", {}).get("content", []):
            item_id = item.get("id")
            if not item_id:
                continue
            raw_regions = item.get("regions", [])
            if isinstance(raw_regions, str):
                raw_regions = [raw_regions]
            else:
                # API가 중첩 리스트나 비문자열 요소를 반환할 경우 평탄화
                flat = []
                for r in raw_regions:
                    if isinstance(r, str):
                        flat.append(r)
                    elif isinstance(r, list):
                        flat.extend(x for x in r if isinstance(x, str))
                raw_regions = flat
            employ_types = item.get("employeeTypes") or []
            jobs.append({
                "id": f"zighang-{item_id}",
                "url": f"https://zighang.com/recruitment/{item_id}",
                "company": item.get("company", {}).get("name", ""),
                "title": item.get("title", ""),
                "regions": raw_regions,
                "career": _zighang_career(
                    item.get("careerMin", 0),
                    item.get("careerMax", 0),
                ),
                "employ_type": employ_types[0] if employ_types else "",
            })

        log.info("[직행] 당일(%s~) 공고 수집 → %d건", today_start, len(jobs))
    except Exception as e:
        log.warning("[직행] API 수집 실패: %s", e)

    return jobs[:limit]
