# 제주도 농업 기상 분석 대시보드 (v5)

기상청 ASOS 일자료(제주 184 · 고산 185 · 성산 188 · 서귀포 189)를 조회하여
**해당 연도 / 전년 / 최근 5년 평균 / 평년(1991~2020) 평균**과 각 기준 대비 **편차(대비)** 및 **비율(전년도 대비(비율), 최근5년평균대비(비율), 평년대비(비율))**을 산출하고, 한글(HWP)에 붙여넣기 좋은 표로 보여주는 Streamlit 앱입니다.

## 파일 구성
| 파일 | 역할 |
|---|---|
| `app.py` | 인증(Phase 1) → 사이드바(Phase 2) → 조회·가공(Phase 3) → HWP용 표·예외 처리(Phase 4) |
| `api_handler.py` | 기상청 API허브 / 공공데이터포털 호출, 응답 파싱, `st.cache_data` 캐싱, 결측 처리 |
| `data_processor.py` | 4개 지점 결측 제외 평균, 기간 집계, 전년·최근5년·평년 비교표 및 비율 산출, 보고서 포맷 |
| `firestore_cache.py` | 확정된 과거 일자료·평년값을 Firestore 에 저장해 재사용 (Firestore 가 없으면 자동으로 꺼짐) |
| `Dockerfile` | Cloud Run 용 컨테이너 |
| `deploy.sh` | Firebase 프로젝트에 한 번에 배포 (Cloud Run + Hosting + Firestore) |
| `firebase.template.json`, `firestore.rules`, `public/` | Firebase Hosting 주소 연결 · Firestore 보안 규칙 |
| `.streamlit/config.toml` | Streamlit 서버 설정 |
| `.streamlit/secrets.toml` | (로컬 실행용, 저장소에 올리지 않음) 접속 비밀번호 `password`, API 키 `api_key` |

## 주요 변경 내역 (v5)
1. **전년 자료(`전년(YYYY-1)`) 및 `전년 대비` 열 추가**: 최근 5년 조회 자료 중 직전 연도(전년도) 동일 기간의 제주 전체 집계값을 추출해 비교표와 산출 근거에 함께 표시합니다(추가 API 호출 없음).
2. **`전년도 대비(비율)`, `최근5년평균대비(비율)`, `평년대비(비율)` 추가 및 기온 지표 반영**:
   - 기존 강수량·일조시간뿐 아니라 **평균 기온, 최고 기온, 최저 기온(및 풍속)**까지 모두 `(해당 연도 ÷ 비교 기준 × 100)` 비율(%)을 기본 산출합니다.
   - 표에 표시된 소수 첫째 자리 숫자를 기준으로 편차와 비율을 계산하므로 계산기 검산 결과와 100% 일치합니다.
3. **표 열 구성 즉시 전환**: 재조회 없이 화면 상단 라디오 버튼으로 4가지 표 구성(`편차+비율 전체`, `기준별 나란히`, `비율 열만`, `편차(비율%) 한 칸 병기`)을 즉시 전환할 수 있습니다.

## Firebase 배포 (v6)

구조: **Firebase Hosting 주소(`https://<프로젝트>.web.app`) → Cloud Run(Streamlit 앱) → Firestore(조회 결과 저장)**

- Streamlit 은 화면을 실시간 연결(WebSocket)로 유지하는데, Firebase Hosting 을 거치면 요청이 60초에 끊기므로
  Hosting 은 Cloud Run 주소로 **이동(리다이렉트)** 만 합니다.
- 비밀번호와 API 키는 Secret Manager 에 저장되어 `APP_PASSWORD`, `KMA_API_KEY` 환경변수로 앱에 전달됩니다.
- Firestore 저장: 종료일이 3일 이상 지난(확정된) 일자료와 평년값만 저장합니다. 최근 자료는 매번 새로 조회합니다.
  “과거 30년 실측 계산” 모드도 두 번째부터는 대부분 저장된 자료로 빠르게 처리됩니다.

### 처음 배포하기
1. [Firebase 콘솔](https://console.firebase.google.com)에서 프로젝트를 만들고 **Blaze(종량제)** 요금제로 전환합니다.
2. 콘솔 오른쪽 위의 **Cloud Shell** 을 열고 저장소를 받습니다.
   ```bash
   git clone https://github.com/ejrgns1021-lab/jeju-agri-weather.git && cd jeju-agri-weather
   ./deploy.sh <프로젝트_ID>
   ```
   (처음 한 번 `firebase login --no-localhost` 가 필요할 수 있습니다.)
3. 실행 중 접속 비밀번호와 기상청 API 키를 물어보면 입력합니다. 끝나면 접속 주소가 표시됩니다.

코드를 고친 뒤 다시 배포할 때도 `./deploy.sh <프로젝트_ID>` 만 실행하면 됩니다.

- 비밀번호 변경: `printf '새비밀번호' | gcloud secrets versions add app-password --data-file=-` 후 다시 배포
- 로컬 실행: 지금처럼 `.streamlit/secrets.toml` 을 만들고 `streamlit run app.py` (Firestore 저장은 자동으로 꺼짐)
