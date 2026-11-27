# CLAUDE.md — 제주 농업 기상계산기

이 문서는 Claude Code가 이 저장소에서 작업을 이어받을 때 먼저 읽는 안내서입니다. 사용자는 한국어로 소통합니다. 답변과 커밋 메시지는 한국어로 작성합니다.

## 0. 반드시 지킬 작업 규칙 (사용자 지시, 예외 없음)

1. `main` 브랜치에 **직접 푸시하지 말 것**. 로컬에서 `firebase deploy`로 라이브에 **직접 배포하지 말 것**.
2. 모든 작업은 `dev` 브랜치에서 합니다: `git switch dev && git pull`.
3. `dev`에 푸시하면 GitHub Actions(`.github/workflows/preview.yml`)가 **프리뷰**에 자동 배포합니다.
4. 사용자가 프리뷰에서 테스트한 뒤 **"승인"이라고 말하기 전에는** main 병합과 라이브 배포를 하지 않습니다.
5. "승인"을 받으면 dev→main PR을 만들고 병합합니다. 병합하면 `deploy-live.yml`이 라이브에 자동 배포합니다.
   - `gh`의 GraphQL은 막혀 있을 수 있으므로 REST를 씁니다.
     - PR 생성: `gh api repos/ejrgns1021-lab/jeju-agri-weather/pulls -f title=... -f head=dev -f base=main -f body=...`
     - 병합: `gh api -X PUT repos/ejrgns1021-lab/jeju-agri-weather/pulls/<N>/merge -f merge_method=merge`
     - 배포 확인: `gh api repos/.../commits/<SHA>/check-runs`
6. 비밀번호와 API 키는 직접 입력하지 않습니다. 사용자가 직접 넣습니다.
7. 관리자 페이지는 사용자 UI에 노출하지 않습니다.
8. 설계 질문이 걸린 큰 작업은 **먼저 방안·장단점·수정 범위를 제시**하고, 사용자가 고른 뒤에 코드를 작성합니다. 이 원칙은 전국 확대와 AWS 이전에 특히 해당합니다.
9. 정상 운영 중인 메인 기능은 훼손하지 않습니다. 새 기능은 프리뷰에서 먼저 확인합니다.

## 1. 주소·배포

- 라이브: https://jeju-agri-weather.web.app (Firebase 프로젝트 `jeju-agri-weather`)
- 프리뷰: https://jeju-agri-weather--preview-test-sbptx43y.web.app
  - 채널 `preview-test`, 30일 만료이며 `deploy-preview.sh`가 연장합니다.
  - 주소가 바뀌면 Actions 요약에서 새 주소를 확인합니다.
- 프리뷰 백엔드는 이름 끝에 `dev`가 붙은 함수(8개, `jejusync` 포함)를 씁니다. `scripts/deploy-preview.sh`가 이름을 바꿔 배포하므로 **라이브 함수와 섞이지 않습니다**.
- 화면에 표시되는 버전과 변경 이력은 `public/index.html`의 changelog(`현재 vX.Y`)와 `README.md`에서 관리합니다. 기능을 바꾸면 두 곳을 함께 갱신합니다.

## 2. 구조 (빌드 단계 없음)

- **프론트**: `public/`에 바닐라 JS ES 모듈로 되어 있습니다.
  - `js/app.js`: 화면·조회·탭(대시보드/순별/일별/월별/연도별), 설정 기억(localStorage)
  - 그 밖의 모듈: `api.js`(`/api/<fn>` 호출), `tables.js`, `dekad.js`, `excel.js`, `forecast.js`, `brief.js`, `statsheet.js`, `manage.js`(관리자), `bases.js`, `icons.js`
- **백엔드**: `functions/`에 있으며 Python 3.12 Cloud Functions gen2(asia-northeast3)와 Firestore를 씁니다. `firebase.json` rewrites로 `/api/<fn>`에 연결됩니다.
  - `main.py`: 함수 진입점. login, jejuweather, jejuprogress, jejumanage, `jejusync`(스케줄) 등이 있습니다.
  - `weather/archive.py`(v7.5): 관측 아카이브입니다.
    - Firestore `obs_daily/asos_{stn}_{year}`에 일별 관측을 저장하고, `obs_meta/asos`에 동기화 상태를 둡니다.
    - `jejusync`는 매일 09~18시 매시 5분(Asia/Seoul)에 돌며 최근 10일을 덮어씁니다.
    - 월요일에는 당해 연도 전체를 덮어씁니다(1월에는 전년도 포함). 비어 있는 연도는 백필합니다.
    - **조회는 아카이브만 읽습니다.** 기상청이 Google IP에서 오는 연결을 자주 거부하기 때문입니다. `warm()`으로 필요한 연도를 일괄 로드합니다.
  - `weather/kma_api.py`: 기상청 API허브와 평년값을 다룹니다. 평년은 1년치 문서를 캐시합니다.
  - `weather/pipeline.py`: 조회 전체 흐름입니다. 지점 일평균을 구하고, 기간을 집계(평균/합)하고, 비교 기준(전년·최근3년·최근5년·평년)과 순별·월별·연도별(본년 포함) 표를 만듭니다.
  - `weather/station_dekad.py`: `fill_station_normals`는 평년값 API에 없는 지점(성산·고산 일조 등)을 1991–2020 같은 날짜 실측 평균(20년 이상)으로 채운 **뒤에** 평균합니다.
  - `weather/dekad.py`: 순별·월별 표(`month_periods`, `build_station_month_tables`, `build_dekad_tables`)를 만듭니다.
  - `weather/store.py`: 결과 캐시입니다.
    - `results` 컬렉션, 키 `q{RESULT_SCHEMA}_…`. **계산 방식이 바뀌면 `RESULT_SCHEMA`를 올립니다**(현재 9).
    - `FINALIZED_AFTER_DAYS` = 11
  - 그 밖의 모듈: `ai.py`(AI 기상분석: Gemini/Gemma, 설정은 관리자 페이지), `forecast.py`, `auth.py`, `settings.py`, `usage.py`, `processing.py`
- `scripts/`: Cloud Shell에서 실행하는 짧은 점검·운영 스크립트입니다(archive-*, live-errors, query-timing, check-* 등). 긴 명령은 스크립트로 만들어 두고 실행합니다.

## 3. 테스트

- 백엔드: `cd functions && python tests/test_backend.py`
  - CI에서 실패하면 배포가 중단됩니다.
  - 로컬에 google-genai가 없으면 `test_ai_models_and_settings`만 실패합니다. 이 실패는 무시해도 됩니다.
  - **사용자 PC(Windows, ROG Ally)**: 저장소는 `C:\Users\ejrgn\jeju-agri-weather`(OneDrive 밖)에 있습니다. 시스템 `python`은 Microsoft Store 바로가기라서 실행되지 않습니다. 그래서 `uv`로 만든 `functions\.venv`(Python 3.12, git에 올라가지 않음)를 씁니다.
    - 처음 한 번: `cd functions; uv venv --python 3.12 .venv; uv pip install --python .venv\Scripts\python.exe -r requirements.txt`
    - 실행: `cd functions; .venv\Scripts\python.exe tests/test_backend.py`
- 프론트: `node --check public/js/*.js`
  - 화면 확인은 Playwright로 합니다. `public/`을 `http://jeju.test`로 라우팅하고 `/api/*`는 목(mock) 응답으로 대신합니다.
  - 데스크톱(1280)과 모바일(375)에서 확인하고, 가로 스크롤(scrollWidth)이 생기지 않는지 봅니다.
- 실데이터 확인은 라이브나 프리뷰 화면에서 하거나, Cloud Shell에서 `scripts/` 점검 스크립트로 합니다.
  - 사무실 PC 브라우저는 Cloud Shell이 차단되어 있습니다.

## 4. 현재 상태 (2026-11-27 기준)

- **안정 운영(유지보수) 단계**입니다. 2026-11-27부터 Claude(채팅)에서 Claude Code로 옮겨 유지보수합니다. 새 기능보다 기존 기능을 지키는 것을 우선합니다.
- 라이브는 **v7.6.1**입니다(PR #31까지 반영). main과 dev가 같은 상태입니다.
  - v7.6: 월별 탭, 평년 평균 보정, 정상 처리일 때 평년 안내 문구 숨김(실패 경고만 유지), 연도별 동기간 표에 본년 포함
  - v7.6.1: 지표·지점·평년 방식·고급 옵션을 바꾸는 즉시 브라우저(localStorage `jeju-weather-prefs`, `PREFS_VERSION=3`)에 저장하고 다음 접속 때 복원합니다. 기간은 매번 최근 7일로 시작합니다. 조회 설정 아래에 안내 문구를 넣었습니다.
- 보류·취소된 작업 (사용자가 다시 요청하기 전에는 손대지 않습니다):
  - 그래프 기능: 브랜치 `graph-wip`. 사용자가 원하는 방식이 정해지지 않아 취소했습니다.
  - AI 사용자 프롬프트 분석: 브랜치 `ai-prompt-wip`. "분석할 때마다 직접 입력" 방식으로 만들다가 보류했습니다.
- 사용자 답변을 기다리는 설계 질문:
  - 전국 17개 시·도 확대: ASOS 우선 여부와 AWS 포함 여부, 지역 선택 UI 위치, "전국 보기"의 의미
  - AWS 이전 아키텍처: 방안 A/B/C 중 선택
- 참고: 프리뷰의 AI 설정은 Gemma 모델과 유효하지 않은 AI Studio 키로 되어 있어 프리뷰에서 AI 분석이 실패합니다. 라이브는 Gemini로 정상입니다. 설정은 바꾸지 않았습니다.

## 5. 작업 순서 체크리스트

1. `git switch dev && git pull`
2. 수정한 뒤 테스트합니다(위 3절).
3. 계산이 바뀌었으면 `RESULT_SCHEMA`를 올립니다.
4. 화면 기능이 바뀌었으면 changelog와 README를 갱신합니다.
5. dev에 커밋하고 푸시한 뒤, 프리뷰 Actions 완료를 확인합니다.
6. 사용자에게 프리뷰 주소와 확인할 점을 알리고 **"승인"을 기다립니다**.
7. 승인을 받으면 dev→main PR을 만들고 병합한 뒤, 라이브 배포 완료와 화면의 버전 표시를 확인합니다.
