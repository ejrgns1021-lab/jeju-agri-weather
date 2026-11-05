// api.js (화면) — 백엔드(Cloud Functions) 호출 전담
// 호출 가능 함수(callable) 규격: POST {data} → {result} | {error}
//
// 경로
//  1) 기본: 웹페이지와 같은 주소의 /api/<함수>  (Firebase Hosting 이 함수로 연결)
//     → 일부 브라우저·네트워크의 보안/광고 차단이 cloudfunctions.net 주소를 막아도 동작
//  2) Hosting 은 요청을 60초에 끊으므로(504), 오래 걸리는 조회는 한 번 더 시도한 뒤
//     함수 주소(cloudfunctions.net)로 직접 호출 (그사이 서버가 받아 둔 자료는 재사용됨)

export const REGION = "asia-northeast3";

const LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);
let directBasePromise = null;

function directBase() {
  if (window.JEJU_FUNCTIONS_BASE) return Promise.resolve(window.JEJU_FUNCTIONS_BASE);
  if (!directBasePromise) {
    directBasePromise = fetch("/__/firebase/init.json")
      .then((r) => {
        if (!r.ok) throw new Error("Firebase 설정(init.json)을 불러오지 못했습니다.");
        return r.json();
      })
      .then((cfg) => (LOCAL
        ? `http://127.0.0.1:5001/${cfg.projectId}/${REGION}` // firebase emulators:start
        : `https://${REGION}-${cfg.projectId}.cloudfunctions.net`))
      .catch((e) => { directBasePromise = null; throw e; });
  }
  return directBasePromise;
}

export class ApiError extends Error {
  constructor(message, status, httpStatus) {
    super(message);
    this.status = status || "UNKNOWN";
    this.httpStatus = httpStatus || 0;
  }
}

const CONNECT_MSG = "서버에 연결할 수 없습니다. 인터넷 연결을 확인하고, 브라우저의 광고·추적 차단 기능이 켜져 있다면 이 사이트에서 꺼 주세요.";

// 이용 현황 집계용 브라우저 번호 (무작위, 개인정보 없음 — 이 브라우저에만 저장)
function clientId() {
  const KEY = "jeju-weather-cid";
  try {
    let v = localStorage.getItem(KEY);
    if (!v) {
      const a = new Uint8Array(12);
      crypto.getRandomValues(a);
      v = Array.from(a, (x) => x.toString(16).padStart(2, "0")).join("");
      localStorage.setItem(KEY, v);
    }
    return v;
  } catch (e) { return ""; }
}

async function post(url, data) {
  data = { ...data, cid: clientId() };
  let resp;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data }),
      cache: "no-store",
    });
  } catch (e) {
    throw new ApiError(CONNECT_MSG, "UNAVAILABLE");
  }
  let body = null;
  try {
    body = await resp.json();
  } catch (e) {
    const timeout = resp.status === 504;
    throw new ApiError(timeout ? "서버 처리 시간이 길어지고 있습니다." : `서버 응답을 읽을 수 없습니다. (HTTP ${resp.status})`,
      timeout ? "DEADLINE_EXCEEDED" : "INTERNAL", resp.status);
  }
  if (body && body.error) throw new ApiError(body.error.message || "알 수 없는 오류", body.error.status, resp.status);
  if (!resp.ok) throw new ApiError(`서버 오류 (HTTP ${resp.status})`, "INTERNAL", resp.status);
  return body.result;
}

export async function callFunction(name, data) {
  if (LOCAL) return post(`${await directBase()}/${name}`, data);
  const sameOrigin = `/api/${name}`;
  try {
    return await post(sameOrigin, data);
  } catch (e) {
    const timedOut = e.status === "DEADLINE_EXCEEDED";
    const unreachable = e.status === "UNAVAILABLE" || e.httpStatus === 404;
    if (!timedOut && !unreachable) throw e;
    if (timedOut) {
      // 서버는 계속 처리하며 자료를 저장해 두므로, 같은 경로로 한 번 더 시도
      try { return await post(sameOrigin, data); } catch (e2) { if (e2.status !== "DEADLINE_EXCEEDED") throw e2; }
    }
    // 마지막 수단: 함수 주소로 직접 호출 (60초 제한 없음)
    return post(`${await directBase()}/${name}`, data);
  }
}

export const login = (password) => callFunction("login", { password });

function newJobId() {
  const a = new Uint8Array(12);
  (window.crypto || window.msCrypto).getRandomValues(a);
  return Array.from(a, (x) => x.toString(16).padStart(2, "0")).join("");
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const MAX_WAIT_MS = 10 * 60 * 1000;   // 서버 함수 제한(9분) + 여유

/**
 * 조회 실행 + 진행 상황 표시
 * - 본 요청(jejuweather)을 보내는 동시에 1.5초마다 진행 상황(jejuprogress)을 받아 onProgress 로 전달
 * - 본 요청이 60초 제한으로 끊겨도(503/504) 서버는 계속 처리하므로, 완료되면 진행 상황 조회로 결과를 받는다
 * (함수 이름에 analyze/analytics/track 등이 들어가면 광고·추적 차단기가 막으므로 중립적인 이름 사용)
 */
export async function analyze(token, query, onProgress = () => {}) {
  const jobId = newJobId();
  const t0 = Date.now();
  let finished = false;
  let mainError = null;
  let mainResult = null;

  const main = post(LOCAL ? `${await directBase()}/jejuweather` : "/api/jejuweather", { token, query, jobId })
    .then((r) => { mainResult = r; })
    .catch((e) => { mainError = e; })
    .finally(() => { finished = true; });

  const cutOff = (e) => e && (e.status === "DEADLINE_EXCEEDED" || e.status === "UNAVAILABLE" && !e.httpStatus || [502, 503, 504].includes(e.httpStatus));

  while (Date.now() - t0 < MAX_WAIT_MS) {
    await Promise.race([main, sleep(1500)]);
    if (finished && mainResult) return mainResult;
    if (finished && mainError && !cutOff(mainError)) throw mainError;   // 입력 오류·기상청 오류 등은 바로 표시
    let p = null;
    try {
      p = await callFunction("jejuprogress", { token, jobId, withResult: finished });
    } catch (e) {
      if (e.status === "UNAUTHENTICATED") throw e;
      continue;   // 진행 상황 조회 실패는 무시하고 계속 대기
    }
    if (p && p.status !== "pending") onProgress({ ...p, waited: (Date.now() - t0) / 1000, cutOff: finished });
    if (p && p.status === "error" && p.error) throw new ApiError(p.error.message, p.error.status);
    if (p && p.status === "done") {
      if (p.result) return p.result;
      if (finished) {
        if (mainResult) return mainResult;
        throw new ApiError(p.too_large ? "결과가 너무 커서 전달하지 못했습니다. 조회 기간을 줄여 주세요." : "결과를 받지 못했습니다. 다시 조회해 주세요.", "INTERNAL");
      }
    }
  }
  throw new ApiError("조회 시간이 너무 오래 걸립니다. 잠시 후 다시 시도해 주세요.", "DEADLINE_EXCEEDED");
}
