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

async function post(url, data) {
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
// 함수 이름에 analyze/analytics/track 등이 들어가면 광고·추적 차단기가 요청을 막으므로 중립적인 이름 사용
export const analyze = (token, query) => callFunction("jejuweather", { token, query });
