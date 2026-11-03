# 임시 네트워크 진단 함수 (서울·대만 리전에서 기상청 서버 접속 가능 여부 확인) — 확인 후 삭제
import json, time, requests
from firebase_functions import https_fn

def probe():
    out = {}
    for name, url in [("ip", "https://ifconfig.me/ip"), ("country", "https://ipinfo.io/country"),
                      ("kma_hub", "https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd3.php?tm1=20261027&tm2=20261027&stn=184"),
                      ("datagokr", "http://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList")]:
        t = time.time()
        try:
            r = requests.get(url, timeout=12)
            out[name] = f"{r.status_code} {time.time()-t:.1f}s {r.text[:40].strip()!r}"
        except Exception as e:
            out[name] = f"FAIL {time.time()-t:.1f}s {type(e).__name__}"
    return https_fn.Response(json.dumps(out, ensure_ascii=False), mimetype="application/json")

@https_fn.on_request(region="asia-northeast3", invoker="public")
def diagseoul(req): return probe()

@https_fn.on_request(region="asia-east1", invoker="public")
def diagtaiwan(req): return probe()

@https_fn.on_request(region="asia-northeast1", invoker="public")
def diagtokyo(req): return probe()
