"""
갑자기 아침 브리핑 — 공개 정보만 모아 briefing.json 하나로 만든다.

매일 04:30~21:30(KST) 매시 30분에 GitHub Actions가 실행한다. GitHub 예약 실행은 자주 늦거나 빠지므로
cron-job.org가 같은 시각에 workflow_dispatch로 직접 깨운다(앱의 "매시간 새로 받아요" 문구가 이것에 기댄다).
표준 라이브러리만 쓴다.
앱은 이 파일을 통째로 내려받아 관심 분야·지역 고르기를 폰 안에서 한다 — 사용자 정보는 서버로 가지 않는다.

출처
- 뉴스: 구글 뉴스 RSS(분야별 헤드라인·검색). 제목·언론사·원문 링크만 쓴다(본문 요약 안 함).
- 화제 검색어: 구글 트렌드 Trending now RSS (KR)
- 날씨: 기상청 단기예보(공공데이터포털) — DATA_GO_KR_KEY 가 있을 때만
- 미세먼지: 에어코리아 시도별 실시간(공공데이터포털) — 같은 키
- 해외(세계 도시 날씨·공휴일·영어 뉴스)는 build_world.py 가 world.json 으로 따로 만든다

실행: python build_briefing.py out/briefing.json
"""
import datetime as dt
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

KST = dt.timezone(dt.timedelta(hours=9))
UA = "Mozilla/5.0 (GapjagiBriefing; +https://kimsicheol.github.io/gapjagi-privacy/)"
GN = "hl=ko&gl=KR&ceid=KR:ko"

# 앱의 BriefingCategory.key 와 같아야 한다
CATEGORIES = {
    "society": ("사회·정치", f"https://news.google.com/rss/headlines/section/topic/NATION?{GN}"),
    "economy": ("경제", f"https://news.google.com/rss/headlines/section/topic/BUSINESS?{GN}"),
    "tech": ("IT·과학", f"https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?{GN}"),
    "world": ("세계", f"https://news.google.com/rss/headlines/section/topic/WORLD?{GN}"),
    "sports": ("스포츠", f"https://news.google.com/rss/headlines/section/topic/SPORTS?{GN}"),
    "entertainment": ("연예", f"https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?{GN}"),
    "health": ("건강·생활", f"https://news.google.com/rss/headlines/section/topic/HEALTH?{GN}"),
    "parenting": (
        "육아·교육",
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote("육아 OR 초등학교 OR 학부모 OR 어린이집 OR 입시 when:1d")
        + f"&{GN}",
    ),
}
PER_CATEGORY = 8
# 관심 키워드 알림용 — 분야별 상위에 못 든 기사도 제목만 넉넉히 실어, 키워드 찾기는 폰 안에서 한다
POOL_PER_CATEGORY = 30

# 시·도 대표 지점(시청·도청). 코드는 행정구역 시도 코드, air 는 에어코리아 sidoName
REGIONS = [
    ("11", "서울", 37.5665, 126.9780, "서울"),
    ("26", "부산", 35.1796, 129.0756, "부산"),
    ("27", "대구", 35.8714, 128.6014, "대구"),
    ("28", "인천", 37.4563, 126.7052, "인천"),
    ("29", "광주", 35.1595, 126.8526, "광주"),
    ("30", "대전", 36.3504, 127.3845, "대전"),
    ("31", "울산", 35.5384, 129.3114, "울산"),
    ("36", "세종", 36.4801, 127.2890, "세종"),
    ("41", "경기", 37.2636, 127.0286, "경기"),
    ("51", "강원", 37.8813, 127.7298, "강원"),
    ("43", "충북", 36.6424, 127.4890, "충북"),
    ("44", "충남", 36.6013, 126.6608, "충남"),
    ("52", "전북", 35.8242, 127.1480, "전북"),
    ("46", "전남", 34.8161, 126.4629, "전남"),
    ("47", "경북", 36.5684, 128.7294, "경북"),
    ("48", "경남", 35.2280, 128.6811, "경남"),
    ("50", "제주", 33.4996, 126.5312, "제주"),
]


RETRY_WAITS = (2, 5)  # 일시 장애(503·429 등)면 2초, 5초 쉬고 두 번 더 해 본다
_TRANSIENT_HTTP = {429, 500, 502, 503, 504}


def is_transient(e: Exception) -> bool:
    """잠깐 기다리면 풀릴 수 있는 오류인가 — 404 같은 잘못된 주소는 아니다"""
    if isinstance(e, urllib.error.HTTPError):
        return e.code in _TRANSIENT_HTTP
    return isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError))


def fetch(url: str, timeout: int = 20, ua: str = "") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua or UA})
    for i in range(len(RETRY_WAITS) + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if i == len(RETRY_WAITS) or not is_transient(e):
                raise
            time.sleep(RETRY_WAITS[i])


def log(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- 뉴스

_SUFFIX = re.compile(r"\s+-\s+[^-]{1,30}$")


def clean_title(title: str, source: str) -> str:
    t = html.unescape(title).strip()
    # "제목 - 머니투데이 - 머니투데이" 처럼 언론사가 한두 번 붙어 온다
    for _ in range(2):
        if source and t.endswith(" - " + source):
            t = t[: -len(" - " + source)].rstrip()
        else:
            m = _SUFFIX.search(t)
            if m and source and source in m.group(0):
                t = t[: m.start()].rstrip()
    return t


def parse_news(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    for rank, it in enumerate(root.iter("item")):
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        title = clean_title(it.findtext("title") or "", source)
        desc = html.unescape(it.findtext("description") or "")
        related = max(1, desc.count("<li>"))
        if not title:
            continue
        items.append({
            "title": title,
            "source": source,
            "url": (it.findtext("link") or "").strip(),
            "related": related,
            "rank": rank,
        })
    return items


def norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def build_news(trend_words):
    """(분야별 상위 기사, 키워드용 기사 묶음)"""
    out = {}
    pool = []
    seen = set()
    news_down = False
    for key, (label, url) in CATEGORIES.items():
        if news_down:
            out[key] = []
            continue
        try:
            items = parse_news(fetch(url))
        except Exception as e:  # 한 분야가 실패해도 나머지는 낸다
            log("news fail", key, e)
            out[key] = []
            # 아직 한 분야도 못 받았는데 재시도까지 일시 장애면 구글 뉴스 자체가 막힌 것 — 나머지는 건너뛴다
            if not any(out.values()) and is_transient(e):
                news_down = True
                log("news down — 나머지 분야 건너뜀")
            continue
        scored = []
        for it in items[:40]:
            n = norm(it["title"])
            if n[:24] in seen:  # 다른 분야에 이미 실린 같은 이야기
                continue
            # 핫함 점수: 구글 뉴스 순위(앞일수록) + 같은 이야기를 다룬 기사 수 + 화제 검색어와 겹침
            score = max(0, 40 - it["rank"]) + it["related"] * 3
            if any(w and w in n for w in trend_words):
                score += 15
            scored.append((score, it))
        scored.sort(key=lambda x: -x[0])
        picked = []
        for score, it in scored[:PER_CATEGORY]:
            seen.add(norm(it["title"])[:24])
            picked.append({
                "title": it["title"],
                "source": it["source"],
                "url": it["url"],
                "related": it["related"],
                "score": score,
            })
        out[key] = picked
        picked_urls = {p["url"] for p in picked}
        for it in items[:40]:
            if it["url"] in picked_urls or len([x for x in pool if x["cat"] == key]) >= POOL_PER_CATEGORY:
                continue
            pool.append({"title": it["title"], "source": it["source"], "url": it["url"], "cat": key})
        log("news", key, len(picked))
    return out, pool


# ---------------------------------------------------------------- 화제 검색어

def build_trends():
    try:
        root = ET.fromstring(fetch("https://trends.google.com/trending/rss?geo=KR"))
    except Exception as e:
        log("trends fail", e)
        return []
    ns = {"ht": "https://trends.google.com/trending/rss"}
    out = []
    for it in root.iter("item"):
        word = (it.findtext("title") or "").strip()
        traffic = (it.findtext("ht:approx_traffic", default="", namespaces=ns) or "").strip()
        if word:
            out.append({"keyword": word, "traffic": traffic})
    return out[:10]


# ---------------------------------------------------------------- 날씨·미세먼지

def latlon_to_grid(lat, lon):
    """기상청 동네예보 격자 변환(LCC). 기상청 공개 공식 그대로"""
    re_, grid = 6371.00877, 5.0
    slat1, slat2, olon, olat = 30.0, 60.0, 126.0, 38.0
    xo, yo = 43, 136
    d = math.pi / 180.0
    re_ /= grid
    slat1, slat2, olon, olat = slat1 * d, slat2 * d, olon * d, olat * d
    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re_ * sf / math.pow(ro, sn)
    ra = math.tan(math.pi * 0.25 + lat * d * 0.5)
    ra = re_ * sf / math.pow(ra, sn)
    theta = lon * d - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn
    x = int(ra * math.sin(theta) + xo + 0.5)
    y = int(ro - ra * math.cos(theta) + yo + 0.5)
    return x, y


def base_time(now: dt.datetime):
    """단기예보 발표(02·05·08·11·14·17·20·23시)는 정각 10분 뒤부터 조회된다"""
    t = now - dt.timedelta(minutes=10)
    hours = [2, 5, 8, 11, 14, 17, 20, 23]
    cand = [h for h in hours if h <= t.hour]
    if cand:
        return t.strftime("%Y%m%d"), f"{cand[-1]:02d}00"
    prev = t - dt.timedelta(days=1)
    return prev.strftime("%Y%m%d"), "2300"


def fetch_forecast(key: str, base_date: str, base_t: str, nx: int, ny: int):
    q = urllib.parse.urlencode({
        "pageNo": 1, "numOfRows": 1000, "dataType": "JSON",
        "base_date": base_date, "base_time": base_t, "nx": nx, "ny": ny,
    })
    url = (
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?serviceKey="
        + key + "&" + q
    )
    return json.loads(fetch(url))["response"]["body"]["items"]["item"]


def today_extremes(items, today: str):
    """발표 자료에 든 오늘의 (최저, 최고). 없는 값은 None"""
    tmn = tmx = None
    for it in items:
        if it["fcstDate"] != today:
            continue
        if it["category"] == "TMN":
            tmn = float(it["fcstValue"])
        elif it["category"] == "TMX":
            tmx = float(it["fcstValue"])
    return tmn, tmx


def build_weather(key: str, now: dt.datetime):
    base_date, base_t = base_time(now)
    today = now.strftime("%Y%m%d")
    out = {}
    for code, name, lat, lon, _ in REGIONS:
        nx, ny = latlon_to_grid(lat, lon)
        try:
            items = fetch_forecast(key, base_date, base_t, nx, ny)
        except Exception as e:
            log("weather fail", code, e)
            continue
        pop = [None] * 24
        tmp = [None] * 24
        pty = [None] * 24
        tmn, tmx = today_extremes(items, today)
        # 오늘 최저(06시)는 02시 발표에만, 최고(15시)는 11시 발표까지만 들어 있다.
        # 빠진 값은 오늘 02시 발표에서 채운다 — 안 그러면 화면에 "최저 -"로 나온다.
        if (tmn is None or tmx is None) and base_date == today and base_t != "0200":
            try:
                tmn2, tmx2 = today_extremes(fetch_forecast(key, today, "0200", nx, ny), today)
                tmn = tmn if tmn is not None else tmn2
                tmx = tmx if tmx is not None else tmx2
            except Exception as e:
                log("weather extremes fail", code, e)
        for it in items:
            if it["fcstDate"] != today:
                continue
            h = int(it["fcstTime"][:2])
            v = it["fcstValue"]
            c = it["category"]
            if c == "POP":
                pop[h] = int(v)
            elif c == "TMP":
                tmp[h] = float(v)
            elif c == "PTY":
                pty[h] = int(v)
        out[code] = {"name": name, "pop": pop, "tmp": tmp, "pty": pty, "tmn": tmn, "tmx": tmx}
    log("weather regions", len(out))
    return out


def grade(pm10, pm25):
    """환경부 4단계 — 둘 중 나쁜 쪽. 0 좋음 1 보통 2 나쁨 3 매우나쁨"""
    def g10(v):
        return 0 if v <= 30 else 1 if v <= 80 else 2 if v <= 150 else 3

    def g25(v):
        return 0 if v <= 15 else 1 if v <= 35 else 2 if v <= 75 else 3

    gs = []
    if pm10 is not None:
        gs.append(g10(pm10))
    if pm25 is not None:
        gs.append(g25(pm25))
    return max(gs) if gs else None


def build_air(key: str):
    out = {}
    for code, name, _, _, sido in REGIONS:
        q = urllib.parse.urlencode({
            "returnType": "json", "numOfRows": 300, "pageNo": 1, "sidoName": sido, "ver": "1.0",
        })
        url = (
            "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty?serviceKey="
            + key + "&" + q
        )
        try:
            items = json.loads(fetch(url))["response"]["body"]["items"]
        except Exception as e:
            log("air fail", code, e)
            continue

        def avg(field):
            vals = []
            for it in items:
                try:
                    vals.append(float(it.get(field)))
                except (TypeError, ValueError):
                    pass
            return round(sum(vals) / len(vals)) if vals else None

        pm10, pm25 = avg("pm10Value"), avg("pm25Value")
        out[code] = {"pm10": pm10, "pm25": pm25, "grade": grade(pm10, pm25)}
    log("air regions", len(out))
    return out


# ---------------------------------------------------------------- 묶기

def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "briefing.json"
    now = dt.datetime.now(KST)
    trends = build_trends()
    trend_words = [norm(t["keyword"]) for t in trends if len(norm(t["keyword"])) >= 2]
    briefing = {
        "version": 1,
        "generatedAt": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "categories": {k: v[0] for k, v in CATEGORIES.items()},
        "regions": {code: name for code, name, *_ in REGIONS},
        "trends": trends,
        "news": {},
        "pool": [],
        "weather": {},
        "air": {},
        "sources": {
            "news": "Google 뉴스",
            "trends": "Google 트렌드",
            "weather": "기상청 단기예보",
            "air": "에어코리아(한국환경공단)",
        },
    }
    briefing["news"], briefing["pool"] = build_news(trend_words)
    # 한국 공휴일 — 앱이 "내일은 공휴일, 기상 알람 끌까요?"를 묻는 데 쓴다. 실패해도 브리핑은 낸다
    try:
        import build_world
        briefing["holidays"] = {"KR": build_world.holidays_for("KR")}
    except Exception as e:
        log("holidays fail", e)
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if key:
        briefing["weather"] = build_weather(key, now)
        briefing["air"] = build_air(key)
    else:
        log("DATA_GO_KR_KEY 없음 — 날씨·미세먼지 생략")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(briefing, f, ensure_ascii=False, separators=(",", ":"))
    total = sum(len(v) for v in briefing["news"].values())
    log("wrote", out_path, "news", total, "trends", len(trends))
    # 해외(영어·세계 도시) 브리핑은 따로 world.json — 무엇이 실패해도 briefing.json 은 이미 써 두었다
    try:
        import build_world
        build_world.build(os.path.join(os.path.dirname(out_path) or ".", "world.json"))
    except Exception as e:
        log("world fail", e)
    if total == 0:
        sys.exit(1)  # 뉴스가 하나도 없으면 실패로 끝내 이전 파일을 지킨다


if __name__ == "__main__":
    main()
