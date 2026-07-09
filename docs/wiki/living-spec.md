# Living Spec

job-fresh 프로젝트의 현재 시스템 상태를 반영하는 단일 진실원(single source of truth) 문서.
과거 이력은 기록하지 않으며, 각 카테고리 섹션은 가장 최신 결정 사항만 담는다.
변경 이력은 이 문서가 아닌 해당 변경을 발생시킨 UL의 "Living Spec Update Log" 섹션에서 추적한다.

## 개념

(아직 등록된 내용 없음)

## 기능

- **자동 API 수집**: 직행(공식 API)에서 공고 수집. Git 저장소 내 기존 저장 항목을 기준으로 중복 자동 방지.
- **AI 구조화(옵션)**: 공고 본문에서 기술스택을 추출하고 한줄 요약 코멘트를 생성. 파이프라인 필수 코어에서 분리된 옵션 기능이며, AWS Lambda 공용 프록시를 통해 Gemini 2.5 Flash를 호출한다(사용자별 Gemini API 키 발급·등록 불필요).
- **개인화 분석(선택)**: `profile.json`에 보유 기술·프로젝트를 작성하면 공고와의 매칭도를 산출. 결과는 Git 파일에 함께 기록(과거 Notion 페이지 첨부 방식에서 저장 매체만 전환, 기능 자체는 유지).
- **Git 저장**: 수집·분석된 공고를 각 fork 저장소 내부에 파일로 저장(Notion DB 저장 폐기). 저장 파일 포맷은 분산된 각 fork에 대한 사실상의 API이므로 스키마 버전을 명시하고, 각 공고 레코드에 안정적 고유 ID(공고 URL 해시 등)를 부여해 dedup·재처리의 전제로 삼는다.
- **설정 파일 기반**: `keywords.json` 수정만으로 직무·지역·경력 필터 변경 (코드 수정 불필요). 원본 태그 문자열(`depthTwos`/`regions`/`employeeTypes`)은 온보딩 스크립트가 자동 기입하므로 직접 손으로 입력할 필요 없음.
- **온보딩 스크립트(`scripts/setup.py`)**: Fork → Clone 후 1회 실행하는 로컬 CLI. (a) 직행 API를 실시간 조회해 현재 유효한 태그 전체를 수집하고 체크박스 picklist로 보여줘 `keywords.json`을 생성, (b) opt-in 질문 이후 대화형 입력(쉼표 리스트, projects 반복 루프)으로 `profile.json`을 생성(`json.dump` 후 자기 검증), (c) 생성된 파일을 `git add/commit/push`로 자동 반영(사용자의 기존 git 자격증명 재사용, push 전 origin이 본인 fork인지 확인). Actions 활성화·시크릿 등록·셀프테스트는 스크립트가 하지 않고 사용자가 웹 UI에서 수동으로 처리(아래 아키텍쳐 절 참고).

## 아키텍쳐

**실행 모델**: 분산형 fork 구조 유지 — 각 사용자가 저장소를 Fork하여 자신의 GitHub Actions 크론으로 실행(중앙 실행 모델로 전환하지 않음).

GitHub Actions 크론(화·수·토 07:00 KST 기준, 온보딩 스크립트가 ±0~25분 랜덤 오프셋 자동 배정, 커스터마이징 가능) → `zighangApi.py` 실행.
- 직행: 공고 목록 수집 → ProseMirror 본문 추출 → (옵션) AI 프록시로 기술스택 추출 → Git 저장.
- 회사명·공고명·지역은 API 응답에서 직접 주입하여 AI 할루시네이션 방지.
- API 본문 추출 실패 시 폴백 없이 해당 공고를 건너뜀.

### AI 프록시 및 레이트리밋 대응
- AI 처리(구조화·한줄요약·개인화 매칭)는 사용자 fork가 직접 Gemini를 호출하지 않고, 운영자가 소유한 AWS Lambda 공용 프록시를 경유한다(운영자의 Gemini 키는 Lambda 환경변수에만 존재).
- 다수 사용자의 크론이 몰려 Gemini 무료 티어 레이트리밋(분당 15회)에 걸리는 문제를 다음 조합으로 대응: 크론 지터(자동 랜덤 오프셋) + Lambda 호출 직전 0~5분 랜덤 sleep + Lambda Reserved Concurrency 1~2로 전역 직렬화 + Lambda 내부 토큰버킷(분당 12회 자체 상한)과 429 시 지수 백오프 재시도(1s→2s→4s, 2~3회) + 여러 공고를 배치로 묶어 호출 수 절감.
- 재시도까지 실패하면 요약 없이 진행(최후 폴백).
- 이 규모(5명 내외, 약 1개월 운영)에서 SQS 등 큐 인프라는 도입하지 않음.
- 인증: GitHub OIDC(Actions `id-token: write` + API Gateway JWT Authorizer로 issuer/repository claim 검증)로 확정. 사용자 fork에 AI 관련 시크릿을 요구하지 않는다.
- Lambda 프록시는 시크릿 부담 제거 수단일 뿐 아니라, 분산 fork 구조에서 운영자가 유일하게 관찰·개입 가능한 지점(운영 시임)이므로 유지한다. 레이트리밋 대응 인프라(토큰버킷·Reserved Concurrency 등)의 경량화 여부는 미확정(Pending, UL-0004).

### AI 가공 대안 (선택, 비공식)
- 사용자가 Claude 유료 구독을 보유한 경우, Lambda 프록시 대신 자신의 Claude 루틴(Claude Code 자체 스케줄 실행)으로 Git에 저장된 1차 소스를 읽어 개인 AI 가공을 수행할 수도 있다는 안내를 README에 참고용으로 남긴다.
- 이는 코드·인프라 지원이 없는 문서 레시피 수준이며, 프로젝트가 공식 제공하는 AI 경로는 여전히 Lambda 프록시 단일 경로다.
- 이 안내에는 1차 소스 Git 파일 스키마가 호환성 비보장(best-effort)임을 반드시 명시한다.

### 온보딩 자동화 원칙
- 온보딩 스크립트(`scripts/setup.py`)는 `gh` CLI에 의존하지 않는다 — 스크립트가 사용자 GitHub 계정 쓰기 권한(Secrets 등록, Actions 상태 변경)을 갖는 자동화는 결함 하나가 여러 사용자 계정에 반복 영향을 미치는 위험이 있기 때문. plain git(커밋/푸시)만 사용한다.
- Actions 활성화는 GitHub 웹 UI에서 사용자가 수동 2클릭으로 처리(Fork 저장소는 기본 비활성).
- 셀프테스트도 자동화하지 않고, 필요 시 사용자가 Actions 탭에서 수동으로 "Run workflow" 클릭. "Actions 비활성화" 상태는 에러 로그 없는 침묵 실패이므로 README 트러블슈팅 섹션과 setup.py 종료 시 "다음 할 일 체크리스트" 출력으로 보완.

### 파일 구성
- `zighangApi.py` — 직행 API 공고 수집, 중복 확인, 오케스트레이션
- `pipeline.py` — 본문 추출 → (옵션) AI 프록시 호출 → Git 저장 공통 파이프라인
- `scripts/setup.py` — 온보딩 CLI(keywords.json·profile.json 자동 생성 및 git 반영)
- `keywords.json` — 직무·지역·경력 필터 설정(setup.py가 생성)
- `profile.json` — 개인화 분석용 프로필 (선택, setup.py가 생성)
- `profile.json.example` — 주석 달린 샘플(setup.py 없이 직접 편집하려는 사용자를 위한 스키마 안내)
- `requirements.txt` — Python 의존성
- `.github/workflows/job-fresh-pipeline.yml`(예정) — 크론 스케줄(자동 지터 포함), Secrets 주입 없음(OIDC). 파일명 변경 반영 전까지는 `legacy_crawler.yml`로 보존.

## 기술스택

- Python 3.12
- GitHub Actions (크론 스케줄링)
- Git (파일 기반 저장소 — 각 fork 내부)
- AWS Lambda (AI 공용 프록시, Gemini 호출 대행)
- Gemini 2.5 Flash (AI 구조화·요약, Lambda 경유)
- 직행 공식 API (API 소스)

사용자 fork 측 필수 환경변수(Secrets): 없음 — GitHub OIDC 인증으로 시크릿 등록 자체가 불필요.
운영자(Lambda) 측 환경변수: `GEMINI_API_KEY`.
