# 제주도 농업 기상 분석 (v6 · Firebase)

기상청 ASOS 일자료(제주 184 · 고산 185 · 성산 188 · 서귀포 189)를 조회하여
**해당 연도 / 전년 / 최근 5년 평균 / 평년(1991~2020) 평균**과 각 기준 대비 **편차** 및 **비율(%)**을 산출하고,
한글(HWP)에 붙여넣기 좋은 표로 보여주는 웹앱입니다.

지표: 평균 기온 · 최고 기온 · 최저 기온 · 누적 강수량 · 일조 시간 (v6에서 풍속 제외)

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
| `public/js/tables.js` | 표 그리기, 한글용 복사(HTML 표), 탭 텍스트 복사, CSV 저장 |
| `public/css/style.css` | 디자인 (모바일·다크 모드 대응) |
| `functions/main.py` | 백엔드 진입점: `login`(비밀번호 → 접속 토큰), `analyze`(조회·가공) |
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

## 변경 내역
- **v6.1**: 비교표 기본 구성을 "한 칸에 편차 (비율%)"로 변경 (버튼도 맨 앞으로)
- **v6**: Streamlit → Firebase(Hosting + Cloud Functions + Firestore)로 전환, 화면·백엔드 분리, 풍속 지표 제거, 과거 연도 병렬 조회, 확정 자료 Firestore 저장
- **v5**: 전년 자료·전년 대비 열, 3종 비율(%) 열, 표 열 구성 즉시 전환
