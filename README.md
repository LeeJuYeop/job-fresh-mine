# job-fresh

직행(zighang)에 **당일 등록된 채용공고**를 매일 자동 수집해, 내 GitHub 저장소에 마크다운 파일로 쌓아주는 파이프라인입니다.

- 이 저장소를 **Fork**하면 내 계정의 GitHub Actions가 매일 아침(KST 07:00) 공고를 수집합니다.
- 공고는 `jobs/{YYYY-MM-DD}/` 일별 폴더에 저장되고, **14일**이 지난 폴더는 자동 삭제됩니다(Git 히스토리에는 남음).
- 별도 서버·시크릿 등록이 필요 없습니다. 워크플로 기본 토큰만으로 동작합니다.

## 동작 방식

```
GitHub Actions 크론 (매일 KST 07:00)
  → 직행 공식 API에서 전날 07:00 이후 등록 공고 조회 (filters.json 필터 적용, 최대 20건)
  → 공고 본문을 마크다운으로 변환
  → jobs/{오늘 날짜}/ 에 저장 후 자동 커밋/푸시
```

## 시작하기

1. **Fork** — 우측 상단 Fork 버튼으로 내 계정에 복사합니다.
2. **필터 설정** — 루트의 `filters.json`을 수정합니다. [필터 생성기 웹페이지](https://leejuyeop.github.io/job-fresh/)에서 클릭만으로 JSON을 만들어 붙여넣을 수 있습니다.
3. **Actions 활성화** — Fork 저장소는 Actions가 기본 비활성입니다. **Actions 탭 → "I understand my workflows, go ahead and enable them"** 클릭.
4. **셀프테스트(권장)** — Actions 탭 → `job-fresh pipeline` → **Run workflow**로 즉시 1회 실행해 `jobs/` 폴더에 커밋이 생기는지 확인합니다.

## filters.json 사용법

수집할 공고의 조건을 정의하는 파일입니다. 배열 필드를 **빈 배열로 두면 해당 조건은 전체 허용**입니다.

```json
{
  "schema_version": 1,
  "depthTwos": ["서버_백엔드", "DevOps_SRE"],
  "regions": [],
  "employeeTypes": [],
  "careerMin": 0,
  "careerMax": 0,
  "includeCareerOpen": true,
  "educations": [],
  "companyTypes": [],
  "deadlineTypes": []
}
```

| 필드 | 의미 |
|------|------|
| `depthTwos` | 직무 카테고리 (예: `서버_백엔드`, `DevOps_SRE`, `시스템_네트워크`) |
| `regions` | 지역 (예: `서울`, `경기`) |
| `employeeTypes` | 채용 유형 (예: `정규직`, `인턴`) |
| `careerMin` / `careerMax` | 경력 범위(년). `0, 0` = 신입만 |
| `includeCareerOpen` | **경력무관 공고 포함 여부**. 경력 범위와 무관하게 "경력무관" 공고를 함께 수집할지 결정 (기본 `true`) |
| `educations` | 학력 조건 |
| `companyTypes` | 기업 규모 |
| `deadlineTypes` | 마감 유형 (예: `상시채용`, `마감일`) |

유효한 태그 값을 직접 외울 필요 없이 [필터 생성기](https://leejuyeop.github.io/job-fresh/)를 사용하는 것을 권장합니다.

## 저장 형식

공고 1건 = 파일 1개. 파일명은 훑어보기 좋게 핵심 정보를 담습니다.

```
jobs/2026-07-16/[회사명][경력][채용유형][지역][마감] 공고 제목.md
```

- 경력 표기: `신입` / `경력무관` / `N년 이하` / `N년 이상` / `N년~M년`
- 파일 내부는 YAML frontmatter(회사·제목·지역·경력·키워드·마감일·수집 시각 등) + 마크다운 본문입니다. frontmatter에는 `schema_version`과 공고 고유 ID(`zighang-{UUID}`)가 포함됩니다.

## 로컬 실행 (선택)

```bash
pip install -r requirements.txt
python src/pipeline.py
```

Python 3.12 기준. 실행하면 로컬에도 동일하게 `jobs/` 폴더가 생성됩니다(커밋은 하지 않음).

## 트러블슈팅

- **아무 일도 일어나지 않아요** — Fork 저장소의 Actions가 비활성 상태면 에러 없이 조용히 실패합니다. Actions 탭에서 활성화됐는지 먼저 확인하세요.
- **수집 0건** — 필터가 너무 좁거나, 해당 날짜에 조건에 맞는 신규 공고가 없는 경우입니다. `jobs/` 폴더가 없으면 커밋도 생기지 않습니다(정상 동작).
- **크론이 정확히 07:00에 안 돌아요** — GitHub Actions 크론은 부하에 따라 수 분~수십 분 지연될 수 있습니다.

## 로드맵

- **AI 한줄요약 / 개인화 분석 (준비 중)**: 공고당 AI 요약 코멘트와 내 프로필(`resume.json`) 기반 매칭 분석을 frontmatter에 추가하는 기능을 준비하고 있습니다. 현재는 비활성 상태로, 모든 공고가 `ai_status: skipped`로 저장됩니다.

## 라이선스 · 주의사항

- 수집 데이터는 직행 공식 API를 통해 얻은 것으로, 개인적인 구직 활동 용도로만 사용하세요.
- 저장 파일 스키마는 개선 과정에서 변경될 수 있습니다(`schema_version`으로 구분).
