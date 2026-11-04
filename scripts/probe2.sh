#!/bin/bash
# 예보 API 403 원인 확인: 이미 되는 일자료 API와 비교 (키는 가림)
K=$(gcloud secrets versions access latest --secret=KMA_API_KEY --project jeju-agri-weather)
D=$(TZ=Asia/Seoul date +%Y%m%d)
for u in "typ01/url/kma_sfcdd3.php?tm1=20251101&tm2=20251101&stn=184&help=0" \
         "typ02/openApi/VilageFcstInfoService_2.0/getVilageFcst?pageNo=1&numOfRows=3&dataType=JSON&base_date=$D&base_time=0500&nx=53&ny=38" \
         "typ02/openApi/MidFcstInfoService/getMidTa?pageNo=1&numOfRows=3&dataType=JSON&regId=11G00201&tmFc=${D}0600" \
         "typ01/url/wrn_now_data.php?fe=f&tm=&disp=0&help=0" \
         "typ01/url/fct_afs_dl.php?reg=11G00201&disp=0&help=0"; do
  echo "== ${u%%\?*}"
  curl -s -A "Mozilla/5.0" -w ' [HTTP %{http_code}]' "https://apihub.kma.go.kr/api/$u&authKey=$K" | head -c 500 | sed "s/$K/***/g"; echo
done
