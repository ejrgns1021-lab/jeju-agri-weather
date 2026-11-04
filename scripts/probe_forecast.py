"""예보 API 확인용 (Cloud Shell에서 KMA_API_KEY 환경변수로 실행). 키는 출력하지 않음."""
import os, json, math, datetime as dt, urllib.request, urllib.parse
K = os.environ["KMA_API_KEY"]
KST = dt.timezone(dt.timedelta(hours=9)); now = dt.datetime.now(KST)
B = "https://apihub.kma.go.kr/api/typ02/openApi"
def get(url, p):
    p = dict(p, authKey=K); u = url + "?" + urllib.parse.urlencode(p)
    try:
        with urllib.request.urlopen(u, timeout=20) as r: t = r.read().decode("utf-8", "replace")
    except Exception as e: t = "ERR " + str(e).replace(K, "***")
    return t.replace(K, "***")
def grid(lat, lon):
    RE, GRID, S1, S2, OLON, OLAT, XO, YO = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    d = math.pi / 180; re = RE / GRID; s1, s2, ol, oa = S1*d, S2*d, OLON*d, OLAT*d
    sn = math.log(math.cos(s1)/math.cos(s2)) / math.log(math.tan(math.pi/4+s2/2)/math.tan(math.pi/4+s1/2))
    sf = math.tan(math.pi/4+s1/2)**sn * math.cos(s1)/sn; ro = re*sf/math.tan(math.pi/4+oa/2)**sn
    ra = re*sf/math.tan(math.pi/4+lat*d/2)**sn; th = lon*d-ol
    th = th - 2*math.pi if th > math.pi else th + 2*math.pi if th < -math.pi else th; th *= sn
    return int(ra*math.sin(th)+XO+0.5), int(ro-ra*math.cos(th)+YO+0.5)
ST = {"제주": (33.5141, 126.5297), "서귀포": (33.2462, 126.5653), "성산": (33.3868, 126.8802), "고산": (33.2938, 126.1628)}
for n, (la, lo) in ST.items(): print("GRID", n, grid(la, lo))
# 단기예보 기준시각
hrs = [2,5,8,11,14,17,20,23]; t = now - dt.timedelta(minutes=15)
c = [h for h in hrs if h <= t.hour]; bd = t if c else t - dt.timedelta(days=1); bh = c[-1] if c else 23
nx, ny = grid(*ST["제주"])
s = get(B+"/VilageFcstInfoService_2.0/getVilageFcst", dict(pageNo=1, numOfRows=50, dataType="JSON", base_date=bd.strftime("%Y%m%d"), base_time=f"{bh:02d}00", nx=nx, ny=ny))
print("VILAGE", s[:900]); print()
tm = now - dt.timedelta(hours=1); tmfc = tm.strftime("%Y%m%d") + ("1800" if tm.hour >= 18 else "0600" if tm.hour >= 6 else None or "")
if tm.hour < 6: tmfc = (tm - dt.timedelta(days=1)).strftime("%Y%m%d") + "1800"
for rid in ["11G00000"]:
    print("MIDLAND", rid, get(B+"/MidFcstInfoService/getMidLandFcst", dict(pageNo=1, numOfRows=10, dataType="JSON", regId=rid, tmFc=tmfc))[:700]); print()
for rid in ["11G00201","11G00401","11G00101","11G00501","11G00302","11G00601"]:
    print("MIDTA", rid, get(B+"/MidFcstInfoService/getMidTa", dict(pageNo=1, numOfRows=10, dataType="JSON", regId=rid, tmFc=tmfc))[:260]); print()
print("PWN", get(B+"/WthrWrnInfoService/getPwnStatus", dict(pageNo=1, numOfRows=10, dataType="JSON"))[:1500]); print()
print("WRNLIST", get(B+"/WthrWrnInfoService/getWthrWrnList", dict(pageNo=1, numOfRows=10, dataType="JSON", stnId=184, fromTmFc=(now-dt.timedelta(days=6)).strftime("%Y%m%d"), toTmFc=now.strftime("%Y%m%d")))[:900]); print()
print("WN_TYP01", get("https://apihub.kma.go.kr/api/typ01/url/wrn_now_data.php", dict(fe="f", tm="", disp=0, help=1))[:1500])
