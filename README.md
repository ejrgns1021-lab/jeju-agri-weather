# 제주 농업 기상계산기 (v6 · Firebase)

기상청 ASOS 일자료(제주 184 · 서귀포 189 · 성산 188 · 고산 185)를 조회하여
**해당 연도 / 전년 / 최근 5년 평균 / 평년(1991~2020) 평균**과 각 기준 대비 **편차** 및 **비율(%)**을 산출하고,
한글(HWP)에 붙여넣기 좋은 표로 보여주는 웹앱입니다.

지표: 평균 기온 · 최고 기온 · 최저 기온 · 누적 강수량 · 일조 시간 · 강우일수 · 열대야 일수

## 구조

```
브라우저 (public/)  ──호출──▶  Cloud Functions (functions/)  ──▶  기상청 API허브 / 공공데이터포털
  화면: 로그인·설정·표           백엔드: 인증·조회·가공                    │
  Firebase Hosting              Python 3.12, 서울 리전          Firestore ◀─┘ (확정 자료 저장·재사용)
```

| 위치 | 역할 |
|---|---|
| `public/index.html` | 화면 뼈대 (로그인, 조회 설정, 결과) |
| `public/js/app.js` | 화면 동작: 입력 확인, 조회 요청, 결과 표·탭 표시 |
| `public/js/api.js` | 백엔드 호출 (Firebase 호출 가능 함수 규격) |
| `public/js/forecast.js` | 대시보드 상단 예보 줄·지역별 접는 칸 |
| `public/manage.html`, `public/js/manage.js` | 관리자 화면 |
| `functions/weather/settings.py` | 관리자 화면 설정: AI 모델 선택, AI Studio 키 보관(v7.4) |
| `functions/weather/usage.py` | 이용 현황 기록·집계, 저장분 비우기 |
| `functions/weather/ai.py` | AI 기상분석: 분석 자료 만들기·사건 구간 탐지·제미나이 호출 |
| `public/js/brief.js` | [AI 기상분석] 버튼·분석 카드·복사 |
| `functions/weather/forecast.py` | 기상청 단기·중기예보 수집과 지점별 정리 |
| `public/js/dekad.js` | 순별(상·중·하순) 집계 표시·토글·엑셀 저장 |
| `public/js/excel.js` | 표 → 엑셀(.xlsx) 저장 (SheetJS, 필요할 때만 불러옴) |
| `functions/weather/dekad.py` | 순별 구간 계산·순별 비교표 (기존 집계 함수 재사용) |
| `scripts/deploy-preview.sh` | 테스트(프리뷰) 배포 — 라이브와 분리 |
| `public/js/icons.js` | 화면 아이콘(Lucide, SVG 코드 내장 — 이미지 파일 없음) |
| `public/js/tables.js` | 표 그리기, 한글용 복사(HTML 표), 탭 텍스트 복사, CSV 저장 |
| `public/css/style.css` | 디자인 (모바일·다크 모드 대응) |
| `functions/main.py` | 백엔드 진입점: `login`(비밀번호 → 접속 토큰), `jejuweather`(조회·가공), `jejuprogress`(진행 상황·결과 전달) |
| `functions/weather/kma_api.py` | 기상청 API 호출, 응답 파싱, 결측 처리 |
| `functions/weather/processing.py` | 4개 지점 평균, 기간 집계, 비교표·비율 산출, 보고서 포맷 |
| `functions/weather/pipeline.py` | 조회 조건 검증 → 전체 계산 → 화면용 표(JSON) 묶음 |
| `functions/weather/store.py` | 메모리 캐시 + Firestore 저장(종료일이 3일 이상 지난 확정 자료·평년값) |
| `functions/weather/auth.py` | 공용 비밀번호 확인, 12시간 접속 토큰 발급·검증 |
| `functions/tests/test_backend.py` | 가짜 기상청 응답으로 백엔드 전체 흐름 검사 |
| `firebase.json`, `firestore.rules` | Firebase 배포 설정, Firestore 외부 접근 차단 규칙 |

- 표 계산은 모두 백엔드에서 하고, 화면은 받은 표를 보여주기만 합니다. 표 열 구성 4가지와 지표별 일별 자료는 한 번에 받아 두므로 재조회 없이 바로 전환됩니다.
- 화면은 백엔드를 직접 호출하므로(Hosting 경유 X) 오래 걸리는 "과거 30년 실측 계산"도 끊기지 않습니다(최대 9분).
- 과거 연도 자료는 4개씩 동시에 받아 와 기존보다 빠르고, 한 번 받은 확정 자료는 Firestore 에서 바로 꺼내 씁니다.

## 처음 배포하기

준비물: [Firebase 콘솔](https://console.firebase.google.com)에서 만든 프로젝트(**Blaze 요금제** 필요 — Cloud Functions 가 외부 기상청 API 를 호출하기 때문, 소규모 사용은 대부분 무료 한도 안), 그리고 Firestore 데이터베이스(콘솔 → Firestore Database → 만들기, 위치 `asia-northeast3`).

PC 터미널이나 **Firebase 콘솔의 Cloud Shell** 에서:

```bash
git clone https://github.com/ejrgns1021-lab/jeju-agri-weather.git
cd jeju-agri-weather

npm install -g firebase-tools        # Cloud Shell 은 생략 가능
firebase login                       # Cloud Shell 은: firebase login --no-localhost
firebase use --add                   # 만든 프로젝트 선택

# 백엔드 실행 환경 (Firebase 배포 도구가 이 폴더를 사용)
cd functions && python3.12 -m venv venv && ./venv/bin/pip install -r requirements.txt && cd ..

# 비밀값 저장 (입력창에 값 붙여넣기)
firebase functions:secrets:set APP_PASSWORD
firebase functions:secrets:set KMA_API_KEY

firebase deploy
```

끝나면 `https://<프로젝트ID>.web.app` 으로 접속합니다.

### 다시 배포 · 관리
- 코드 수정 후: `firebase deploy` (화면만: `firebase deploy --only hosting`, 백엔드만: `--only functions`)
- 비밀번호 변경: `firebase functions:secrets:set APP_PASSWORD` 후 `firebase deploy --only functions` (기존 로그인은 자동 만료)
- 저장된 자료 초기화: Firestore 콘솔에서 `kma_daily`, `kma_normals` 컬렉션 삭제

## 내 PC에서 실행해 보기 (에뮬레이터)

```bash
cp functions/.secret.local.example functions/.secret.local   # 비밀번호·API 키 입력
firebase emulators:start --only functions,hosting
```
브라우저에서 http://localhost:5000 접속. (에뮬레이터에서는 Firestore 저장이 꺼진 채로 동작합니다.)

백엔드 검사: `cd functions && python3 tests/test_backend.py`

## 개발·배포 절차 (v6.8부터)
1. 모든 작업은 `dev` 브랜치에서 진행 (`main` 직접 수정·배포 금지)
2. 테스트 배포: `dev`에 올리면 GitHub Actions가 자동 실행 (수동: Cloud Shell에서 `bash scripts/deploy-preview.sh`)
   - 서버 함수는 `logindev / jejuweatherdev / jejuprogressdev`(codebase `dev`)로 따로 배포, 웹페이지는 Hosting 임시 채널 `preview-test`(7일 만료)
   - **라이브 사이트·라이브 함수는 변경되지 않음**
3. 프리뷰 주소에서 확인·승인 후에만 `dev` → `main` 병합, 그 다음 Actions의 '라이브 배포 (main, 수동)' 실행

## 자동 배포 (GitHub Actions) — Cloud Shell 없이 배포
- **프리뷰**: `dev` 브랜치에 올리면 자동 실행 → 저장소 **Actions** 탭의 '프리뷰 배포 (dev)' 결과 화면에 프리뷰 주소 표시
- **라이브**: 자동 실행 없음. 승인 후 `main` 병합 → Actions 탭 → '라이브 배포 (main, 수동)' → Run workflow → `DEPLOY` 입력
- 두 작업 모두 백엔드 테스트가 실패하면 배포하지 않음

### 처음 한 번: 배포용 서비스 계정 키 등록
1. [Google Cloud 콘솔 > 서비스 계정 만들기](https://console.cloud.google.com/iam-admin/serviceaccounts/create?project=jeju-agri-weather)
   - 이름: `github-deploy`
   - 역할 추가: **편집자**, **Cloud Functions 관리자**, **Cloud Run 관리자**, **Secret Manager 관리자**, **서비스 계정 사용자**
2. 만든 계정 클릭 → **키** 탭 → 키 추가 → 새 키 만들기 → **JSON** → 파일이 내려받아짐
3. [깃허브 저장소 > Settings > Secrets and variables > Actions](https://github.com/ejrgns1021-lab/jeju-agri-weather/settings/secrets/actions) → New repository secret
   - Name: `FIREBASE_SERVICE_ACCOUNT` / Secret: 내려받은 JSON 파일 내용 전체를 붙여넣기
4. 내려받은 JSON 파일은 PC에서 삭제 (깃허브에 암호화되어 저장됨)

## 변경 내역
- **v7.4**: **AI 모델 2종** — [AI 기상분석]에 젬마(Gemma 4 31B, `gemma-4-31b-it`, Google AI Studio 무료 키) 추가. 관리자 화면 **AI 모델** 카드에서 사용할 모델 선택·응답 시험·AI Studio 키 저장(Firestore `settings/ai_keys`, 브라우저 직접 접근 차단, 화면에는 끝 4자리만 표시; 환경 변수 `AISTUDIO_API_KEY`가 있으면 우선). 선택은 `settings/ai`(프리뷰는 `settings/ai_dev`라 라이브에 영향 없음)에 저장, 서버는 1분간 기억. 분석 자료·지시문은 모델과 무관하게 같고 연결부(`ai.py` `_client`)만 다름 — 새 모델은 `ai.MODELS`에 한 줄 추가. 저장된 분석은 모델별로 따로 보관, 분석 아래에 사용한 모델 표시. 연결 점검에 젬마 추가. 계산 로직 변경 없음
- **v7.3**: **보고서 표** 탭 — 지표 × 지점 × (본년 + 고른 비교 기준) 표, 제목('□ 기상현황 (기간)')·출처 자동, 지점 평균 열·차이 표시 선택, 한글용 복사·엑셀. **비교 기준 칩**(전년·최근 3년·최근 5년·평년) — 고른 기준만 카드·비교표·순별표·엑셀에 표시(다시 조회 불필요, 선택 기억). 서버: 최근 3년 평균 열 추가, 지점별 본년·전년·3년·5년·평년(`stationReport`) 계산(받아 둔 자료 재사용). 결과 저장 형식 q5
- **v7.3.1**: 사이트 이름을 "제주 농업 기상계산기"로 변경(브라우저 탭 제목, 로그인 화면, 상단 바, 관리 화면)
- **v7.2**: **관리자 화면** `/manage.html` (관리자 비밀번호 `ADMIN_PASSWORD`, 공용 비밀번호와 별개) — 날짜별 접속 브라우저 수·로그인·조회·AI 분석 횟수(브라우저별 무작위 번호만 해시해 기록, 개인정보 없음, Firestore `usage`), 연결 점검(기상청 일자료·단기예보·제미나이), 저장분 비우기, 콘솔 바로가기. 비밀번호 설정: Cloud Shell에서 `bash scripts/set-admin-password.sh`
- **v7.1**: AI 기상분석 기준 — 기온·일조·열대야는 최근 5년과만(온난화 반영), 강수량은 지점별 최근 5년·평년을 함께 보고 두 기준 모두의 50% 미만이면 부족, 모두의 200% 초과면 과다, 그 밖은 적정(문제로 쓰지 않음)
- **v7.0.2**: 대시보드 "핵심 기상 지표" 제목을 "핵심 기상 지표(평균)"으로 바꾸고 아래 설명 줄 삭제
- **v7.0.1**: AI 기상분석 답변이 중간에 잘리던 문제 수정
- **v7.0**: **AI 기상분석** — 조회 후 기간 제목 오른쪽 [AI 기상분석] 버튼을 누를 때만 제미나이(Vertex AI, gemini-3.8-flash)가 **지점별(제주·서귀포·성산·고산)로 그 지점의 최근 5년 평균과 비교**해 특이 기상 요인을 발생 기간 중심으로 찾고 종합(제주 전체 평균의 착시 방지). 지점별 순별 비교(`stationBrief`, 조회 시 이미 받은 자료로 계산)와 지점별 일별 사건(무강수/폭염/열대야/호우/저온/일조 부족 구간)만 전달. 서버 함수 `jejubrief`, Firestore `ai_analysis`에 저장해 같은 조건은 재사용. 프로젝트에서 Vertex AI API 사용 설정 필요(API 키 없음). 기존 계산 로직 변경 없음. 강우일수·열대야 일수를 선택하지 않았는데도 '평년값을 산출하지 못했습니다' 경고가 뜨던 문제 수정
- **v6.9.2**: 사이트 이름을 "제주 기상계산기"로 변경(브라우저 탭 제목, 로그인 화면, 상단 바)
- **v6.9.1**: 예보 — 기상청 연결 실패 시 재시도·지점별 동시 요청, 마지막 정상 예보로 대체 표시
- **v6.9**: 대시보드 맨 위 **기상 예보** — 오늘·내일 한 줄 요약, 접는 칸에 지점별 단기예보(오전→오후 날씨·최저/최고·강수확률)와 중기예보(제주도 날씨·지점별 기온). 서버 함수 `jejuforecast`, Firestore `forecast`에 20분 저장. 기상청 API허브 단기예보(getVilageFcst)·중기예보(getMidLandFcst, getMidTa) 활용신청 필요. 로그인 화면에 **업데이트 내역**(접는 칸, 새 버전마다 `index.html`의 목록 맨 위에 한 줄 추가). 관측 자료 계산 로직 변경 없음
- **v6.8.2**: 지점 표시 순서를 제주 → 서귀포 → 성산 → 고산으로 통일(지점 선택·지점별 표·일별 표·원자료·엑셀). 계산 로직 변경 없음
- **v6.8.1**: 누적 강수량·일조 시간 카드도 편차 수치를 위에, 비율(%)을 아래에 표시해 다른 지표와 양식 통일
- **v6.8**: **열대야 일수** 지표(일 최저기온 25.0℃ 이상인 날, 평년은 30년 실측), **순별(상·중·하순) 집계** 토글 — 순마다 전년·최근 5년·평년 비교, 부분 순 표시, **순별 엑셀(.xlsx) 다운로드**, 전체 결과 엑셀 저장, 프리뷰 배포 스크립트. 기존 지표·일별 계산 로직 변경 없음
- **v6.7**: 기상 지표에 **강우일수** 추가(일강수량 0.1mm 이상인 날을 1일로 집계, 제주 전체는 4개 지점 평균, 평년은 30년 실측으로 계산), 핵심 기상 지표 카드에 "전년 대비 / 최근 5년 대비 / 평년 대비"와 기준값 표기
- **v6.6**: 기상청 연결 대기 20초×3회 → 연결 5초×6회(응답 대기 20초 유지)로 간헐적 연결 실패에 빠르게 재시도, 조회 결과 전체를 Firestore(`results`)에 저장해 같은 조건 재조회 시 즉시 표시(확정 기간 영구·최근 기간 6시간), 최근 일자료도 6시간 저장, 진단용 원문 조회가 본 조회를 지연시키지 않도록 분리. 계산 로직 변경 없음
- **v6.5**: 조회 중 로딩창에 진행 단계·세부 내용·진행률 표시(어느 연도·지점 자료를 받는 중인지), 60초가 넘는 조회도 결과를 받아오도록 작업 상태를 Firestore(`jobs`)에 기록, 오류 문구·로그에서 기상청 API 키 가림. 계산 로직 변경 없음
- **v6.4**: 백엔드 호출을 웹페이지와 같은 주소(`/api/...`, Hosting 연결)로 변경 — 일부 브라우저·네트워크가 cloudfunctions.net 주소를 차단해 로그인·조회가 안 되던 문제 해결. 60초가 넘는 조회는 자동 재시도 후 함수 주소로 직접 호출. 계산 로직 변경 없음
- **v6.3**: 조회 함수 이름 `analyze` → `jejuweather` (브라우저 광고·추적 차단기가 'analyze' 주소를 막아 "서버에 연결할 수 없습니다"가 뜨던 문제 해결). 계산 로직 변경 없음
- **v6.2**: 화면 전면 개편 — 지표별 비교 카드(기준 대비 증감·비율 막대), 모바일 바텀 시트 조회 조건·상단 요약 바·원탭 조회, Lucide 아이콘(코드 내장), 차분한 그린/블루 톤. 백엔드·계산·API 호출 코드는 변경 없음
- **v6.1**: 비교표 기본 구성을 "한 칸에 편차 (비율%)"로 변경 (버튼도 맨 앞으로)
- **v6**: Streamlit → Firebase(Hosting + Cloud Functions + Firestore)로 전환, 화면·백엔드 분리, 풍속 지표 제거, 과거 연도 병렬 조회, 확정 자료 Firestore 저장
- **v5**: 전년 자료·전년 대비 열, 3종 비율(%) 열, 표 열 구성 즉시 전환
