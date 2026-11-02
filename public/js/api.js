// api.js (화면) — 백엔드(Cloud Functions) 호출 전담
// Firebase Hosting 이 자동 제공하는 /__/firebase/init.json 으로 프로젝트 ID 를 알아낸 뒤
// 호출 가능 함수(callable) 규격(POST {data} → {result} | {error})으로 직접 호출합니다.
// (Hosting 경유가 아닌 직접 호출이라 60초 제한 없이 긴 조회도 가능)

export const REGION = "asia-northeast3";

let basePromise = null;

function resolveBase() {
  if (window.JEJU_FUNCTIONS_BASE) return Promise.resolve(window.JEJU_FUNCTIONS_BASE);
  return fetch("/__/firebase/init.json")
    .then((r) => {
      if (!r.ok) throw new Error("Firebase 설정(init.json)을 불러오지 못했습니다.");
      return r.json();
    })
    .then((cfg) => {
      const local = ["localhost", "127.0.0.1"].includes(location.hostname);
      return local
        ? `http://127.0.0.1:5001/${cfg.projectId}/${REGION}` // firebase emulators:start
        : `https://${REGION}-${cfg.projectId}.cloudfunctions.net`;
    });
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status || "UNKNOWN";
  }
}

export async function callFunction(name, data) {
  if (!basePromise) basePromise = resolveBase().catch((e) => { basePromise = null; throw e; });
  const base = await basePromise;
  let resp;
  try {
    resp = await fetch(`${base}/${name}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data }),
    });
  } catch (e) {
    throw new ApiError("서버에 연결할 수 없습니다. 인터넷 연결을 확인해 주세요.", "UNAVAILABLE");
  }
  let body = null;
  try {
    body = await resp.json();
  } catch (e) {
    throw new ApiError(`서버 응답을 읽을 수 없습니다. (HTTP ${resp.status})`, "INTERNAL");
  }
  if (body && body.error) throw new ApiError(body.error.message || "알 수 없는 오류", body.error.status);
  if (!resp.ok) throw new ApiError(`서버 오류 (HTTP ${resp.status})`, "INTERNAL");
  return body.result;
}

export const login = (password) => callFunction("login", { password });
export const analyze = (token, query) => callFunction("analyze", { token, query });
