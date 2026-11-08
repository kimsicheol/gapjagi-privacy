"""
갑자기 해외 브리핑 — 세계 도시 날씨·공휴일·영어 뉴스를 world.json 하나로 만든다.

build_briefing.py 가 briefing.json 을 쓴 뒤 이어서 부른다. 여기서 무엇이 실패해도 briefing.json(한국 사용자용,
출시된 앱이 읽는 파일)에는 영향이 없어야 한다 — 호출부가 예외를 삼킨다.
표준 라이브러리만 쓴다.

출처
- 날씨: MET Norway Locationforecast 2.0 (CC BY 4.0, 상업 이용 가능 — 출처 표시와 식별 가능한 User-Agent 필요).
  한국 밖에서는 강수 "확률"을 주지 않아, 시간별 강수량(mm)과 날씨 기호(symbol_code)를 싣는다.
- 공휴일: Nager.Date NextPublicHolidays (전국 공휴일만, 앞으로 1년)
- 영어 뉴스·화제 검색어: 구글 뉴스 RSS·구글 트렌드 RSS, 나라별 판(US·GB·CA·AU)

실행(단독): python build_world.py out/world.json
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import build_briefing as kb  # fetch·parse_news·norm 재사용

UA = "GapjagiBriefing/1.0 (+https://kimsicheol.github.io/gapjagi-privacy/)"

# (id, 한국어 이름, 영어 이름, 나라, 위도, 경도, 시간대) — 영어권 주요 도시 + 한국인이 많이 사는 도시.
# id 는 앱이 설정에 "w:<id>" 로 저장하므로 한 번 정하면 바꾸지 않는다.
CITIES = [
    # 미국
    ("nyc", "뉴욕", "New York", "US", 40.7128, -74.0060, "America/New_York"),
    ("nj", "뉴저지(포트리)", "Fort Lee, NJ", "US", 40.8509, -73.9701, "America/New_York"),
    ("bos", "보스턴", "Boston", "US", 42.3601, -71.0589, "America/New_York"),
    ("phl", "필라델피아", "Philadelphia", "US", 39.9526, -75.1652, "America/New_York"),
    ("dc", "워싱턴 D.C.", "Washington, D.C.", "US", 38.9072, -77.0369, "America/New_York"),
    ("atl", "애틀랜타", "Atlanta", "US", 33.7490, -84.3880, "America/New_York"),
    ("mia", "마이애미", "Miami", "US", 25.7617, -80.1918, "America/New_York"),
    ("orl", "올랜도", "Orlando", "US", 28.5383, -81.3792, "America/New_York"),
    ("chi", "시카고", "Chicago", "US", 41.8781, -87.6298, "America/Chicago"),
    ("hou", "휴스턴", "Houston", "US", 29.7604, -95.3698, "America/Chicago"),
    ("dal", "댈러스", "Dallas", "US", 32.7767, -96.7970, "America/Chicago"),
    ("aus", "오스틴", "Austin", "US", 30.2672, -97.7431, "America/Chicago"),
    ("den", "덴버", "Denver", "US", 39.7392, -104.9903, "America/Denver"),
    ("phx", "피닉스", "Phoenix", "US", 33.4484, -112.0740, "America/Phoenix"),
    ("las", "라스베이거스", "Las Vegas", "US", 36.1699, -115.1398, "America/Los_Angeles"),
    ("la", "로스앤젤레스", "Los Angeles", "US", 34.0522, -118.2437, "America/Los_Angeles"),
    ("irv", "어바인", "Irvine", "US", 33.6846, -117.8265, "America/Los_Angeles"),
    ("sd", "샌디에이고", "San Diego", "US", 32.7157, -117.1611, "America/Los_Angeles"),
    ("sf", "샌프란시스코", "San Francisco", "US", 37.7749, -122.4194, "America/Los_Angeles"),
    ("sj", "새너제이", "San Jose", "US", 37.3382, -121.8863, "America/Los_Angeles"),
    ("sea", "시애틀", "Seattle", "US", 47.6062, -122.3321, "America/Los_Angeles"),
    ("pdx", "포틀랜드", "Portland", "US", 45.5152, -122.6784, "America/Los_Angeles"),
    ("anc", "앵커리지", "Anchorage", "US", 61.2181, -149.9003, "America/Anchorage"),
    ("hnl", "호놀룰루", "Honolulu", "US", 21.3069, -157.8583, "Pacific/Honolulu"),
    ("gum", "괌", "Guam", "GU", 13.4443, 144.7937, "Pacific/Guam"),
    ("spn", "사이판", "Saipan", "MP", 15.1778, 145.7510, "Pacific/Saipan"),
    # 캐나다
    ("tor", "토론토", "Toronto", "CA", 43.6532, -79.3832, "America/Toronto"),
    ("van", "밴쿠버", "Vancouver", "CA", 49.2827, -123.1207, "America/Vancouver"),
    ("mtl", "몬트리올", "Montreal", "CA", 45.5017, -73.5673, "America/Toronto"),
    ("cgy", "캘거리", "Calgary", "CA", 51.0447, -114.0719, "America/Edmonton"),
    ("ott", "오타와", "Ottawa", "CA", 45.4215, -75.6972, "America/Toronto"),
    # 영국·아일랜드
    ("lon", "런던", "London", "GB", 51.5074, -0.1278, "Europe/London"),
    ("man", "맨체스터", "Manchester", "GB", 53.4808, -2.2426, "Europe/London"),
    ("edi", "에든버러", "Edinburgh", "GB", 55.9533, -3.1883, "Europe/London"),
    ("dub", "더블린", "Dublin", "IE", 53.3498, -6.2603, "Europe/Dublin"),
    # 호주·뉴질랜드
    ("syd", "시드니", "Sydney", "AU", -33.8688, 151.2093, "Australia/Sydney"),
    ("mel", "멜버른", "Melbourne", "AU", -37.8136, 144.9631, "Australia/Melbourne"),
    ("bne", "브리즈번", "Brisbane", "AU", -27.4698, 153.0251, "Australia/Brisbane"),
    ("per", "퍼스", "Perth", "AU", -31.9505, 115.8605, "Australia/Perth"),
    ("akl", "오클랜드", "Auckland", "NZ", -36.8485, 174.7633, "Pacific/Auckland"),
    ("chc", "크라이스트처치", "Christchurch", "NZ", -43.5321, 172.6362, "Pacific/Auckland"),
    # 유럽
    ("par", "파리", "Paris", "FR", 48.8566, 2.3522, "Europe/Paris"),
    ("fra", "프랑크푸르트", "Frankfurt", "DE", 50.1109, 8.6821, "Europe/Berlin"),
    ("ber", "베를린", "Berlin", "DE", 52.5200, 13.4050, "Europe/Berlin"),
    ("ams", "암스테르담", "Amsterdam", "NL", 52.3676, 4.9041, "Europe/Amsterdam"),
    ("mad", "마드리드", "Madrid", "ES", 40.4168, -3.7038, "Europe/Madrid"),
    ("rom", "로마", "Rome", "IT", 41.9028, 12.4964, "Europe/Rome"),
    ("waw", "바르샤바", "Warsaw", "PL", 52.2297, 21.0122, "Europe/Warsaw"),
    ("prg", "프라하", "Prague", "CZ", 50.0755, 14.4378, "Europe/Prague"),
    ("bud", "부다페스트", "Budapest", "HU", 47.4979, 19.0402, "Europe/Budapest"),
    # 아시아·중동
    ("tyo", "도쿄", "Tokyo", "JP", 35.6762, 139.6503, "Asia/Tokyo"),
    ("osa", "오사카", "Osaka", "JP", 34.6937, 135.5023, "Asia/Tokyo"),
    ("fuk", "후쿠오카", "Fukuoka", "JP", 33.5904, 130.4017, "Asia/Tokyo"),
    ("pek", "베이징", "Beijing", "CN", 39.9042, 116.4074, "Asia/Shanghai"),
    ("sha", "상하이", "Shanghai", "CN", 31.2304, 121.4737, "Asia/Shanghai"),
    ("tao", "칭다오", "Qingdao", "CN", 36.0671, 120.3826, "Asia/Shanghai"),
    ("hkg", "홍콩", "Hong Kong", "HK", 22.3193, 114.1694, "Asia/Hong_Kong"),
    ("tpe", "타이베이", "Taipei", "TW", 25.0330, 121.5654, "Asia/Taipei"),
    ("uln", "울란바토르", "Ulaanbaatar", "MN", 47.8864, 106.9057, "Asia/Ulaanbaatar"),
    ("sgn", "호찌민", "Ho Chi Minh City", "VN", 10.8231, 106.6297, "Asia/Ho_Chi_Minh"),
    ("han", "하노이", "Hanoi", "VN", 21.0278, 105.8342, "Asia/Ho_Chi_Minh"),
    ("dad", "다낭", "Da Nang", "VN", 16.0544, 108.2022, "Asia/Ho_Chi_Minh"),
    ("bkk", "방콕", "Bangkok", "TH", 13.7563, 100.5018, "Asia/Bangkok"),
    ("mnl", "마닐라", "Manila", "PH", 14.5995, 120.9842, "Asia/Manila"),
    ("ceb", "세부", "Cebu", "PH", 10.3157, 123.8854, "Asia/Manila"),
    ("sin", "싱가포르", "Singapore", "SG", 1.3521, 103.8198, "Asia/Singapore"),
    ("kul", "쿠알라룸푸르", "Kuala Lumpur", "MY", 3.1390, 101.6869, "Asia/Kuala_Lumpur"),
    ("jkt", "자카르타", "Jakarta", "ID", -6.2088, 106.8456, "Asia/Jakarta"),
    ("del", "뉴델리", "New Delhi", "IN", 28.6139, 77.2090, "Asia/Kolkata"),
    ("bom", "뭄바이", "Mumbai", "IN", 19.0760, 72.8777, "Asia/Kolkata"),
    ("dxb", "두바이", "Dubai", "AE", 25.2048, 55.2708, "Asia/Dubai"),
    ("ala", "알마티", "Almaty", "KZ", 43.2220, 76.8512, "Asia/Almaty"),
    ("tas", "타슈켄트", "Tashkent", "UZ", 41.2995, 69.2401, "Asia/Tashkent"),
    # 중남미
    ("mex", "멕시코시티", "Mexico City", "MX", 19.4326, -99.1332, "America/Mexico_City"),
    ("sao", "상파울루", "São Paulo", "BR", -23.5505, -46.6333, "America/Sao_Paulo"),
    ("bue", "부에노스아이레스", "Buenos Aires", "AR", -34.6037, -58.3816, "America/Argentina/Buenos_Aires"),
]

HOURS = 48

# 영어 뉴스 판 — 나라 코드 → 구글 뉴스 지역. 여기 없는 나라의 도시는 US 판을 본다(앱이 정한다)
EDITIONS = {"US": "US", "GB": "GB", "CA": "CA", "AU": "AU"}

TOPICS = {
    "society": "NATION",
    "economy": "BUSINESS",
    "tech": "TECHNOLOGY",
    "world": "WORLD",
    "sports": "SPORTS",
    "entertainment": "ENTERTAINMENT",
    "health": "HEALTH",
}
PARENTING_Q = "parenting OR schools OR \"school district\" OR childcare when:1d"
PER_CATEGORY = 8
POOL_PER_CATEGORY = 15


def fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def log(*a):
    print("[world]", *a, file=sys.stderr)


# ---------------------------------------------------------------- 날씨

def build_weather():
    out = {}
    for cid, _, _, _, lat, lon, _ in CITIES:
        url = (
            "https://api.met.no/weatherapi/locationforecast/2.0/compact?"
            + urllib.parse.urlencode({"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"})
        )
        try:
            series = json.loads(fetch(url))["properties"]["timeseries"]
        except Exception as e:
            log("weather fail", cid, e)
            continue
        start = None
        tmp, rain, sym = [], [], []
        for t in series:
            ts = dt.datetime.fromisoformat(t["time"].replace("Z", "+00:00"))
            if start is None:
                start = ts
            # 앞쪽은 1시간 간격, 뒤로 가면 6시간 간격이라 1시간 칸이 끊기는 곳에서 멈춘다
            if ts != start + dt.timedelta(hours=len(tmp)) or len(tmp) >= HOURS:
                break
            d = t["data"]
            n1 = d.get("next_1_hours")
            if n1 is None:
                break
            tmp.append(round(d["instant"]["details"].get("air_temperature", 0.0), 1))
            rain.append(round(n1.get("details", {}).get("precipitation_amount", 0.0), 1))
            sym.append(n1.get("summary", {}).get("symbol_code", ""))
        if start is None or not tmp:
            continue
        out[cid] = {"start": int(start.timestamp()), "tmp": tmp, "rain": rain, "sym": sym}
        time.sleep(0.05)  # MET 이용 수칙: 짧은 시간에 몰아서 부르지 않는다
    log("weather cities", len(out))
    return out


# ---------------------------------------------------------------- 공휴일

def holidays_for(cc: str):
    """한 나라의 앞으로 1년 전국 공휴일. ko 는 한국이면 현지 이름, 아니면 영어 이름. 다루지 않는 나라면 []"""
    body = fetch(f"https://date.nager.at/api/v3/NextPublicHolidays/{cc}")
    if not body.strip():
        return []  # Nager.Date가 다루지 않는 나라(인도·태국·대만 등)는 빈 응답
    seen = set()
    days = []
    for h in json.loads(body):
        if not h.get("global", True) or h.get("counties"):
            continue  # 일부 주·지역만 쉬는 날은 뺀다
        if h["date"] in seen:
            continue
        seen.add(h["date"])
        days.append({"date": h["date"], "ko": h["localName"] if cc == "KR" else h["name"], "en": h["name"]})
    return days


def build_holidays():
    """나라 → 공휴일 목록"""
    out = {}
    for cc in sorted({c[3] for c in CITIES} | {"KR"}):
        try:
            days = holidays_for(cc)
        except Exception as e:
            log("holiday fail", cc, e)
            continue
        if days:
            out[cc] = days
    log("holiday countries", len(out))
    return out


# ---------------------------------------------------------------- 영어 뉴스

def build_edition(cc: str):
    gn = f"hl=en-{cc}&gl={cc}&ceid={cc}:en"
    try:
        root_trends = kb.ET.fromstring(fetch(f"https://trends.google.com/trending/rss?geo={cc}"))
        ns = {"ht": "https://trends.google.com/trending/rss"}
        trends = []
        for it in root_trends.iter("item"):
            word = (it.findtext("title") or "").strip()
            traffic = (it.findtext("ht:approx_traffic", default="", namespaces=ns) or "").strip()
            if word:
                trends.append({"keyword": word, "traffic": traffic})
        trends = trends[:10]
    except Exception as e:
        log("trends fail", cc, e)
        trends = []
    trend_words = [kb.norm(t["keyword"]) for t in trends if len(kb.norm(t["keyword"])) >= 2]

    urls = {k: f"https://news.google.com/rss/headlines/section/topic/{v}?{gn}" for k, v in TOPICS.items()}
    urls["parenting"] = "https://news.google.com/rss/search?q=" + urllib.parse.quote(PARENTING_Q) + f"&{gn}"

    news, pool, seen = {}, [], set()
    for key, url in urls.items():
        try:
            items = kb.parse_news(fetch(url))
        except Exception as e:
            log("news fail", cc, key, e)
            news[key] = []
            continue
        scored = []
        for it in items[:40]:
            n = kb.norm(it["title"])
            if n[:24] in seen:
                continue
            score = max(0, 40 - it["rank"]) + it["related"] * 3
            if any(w and w in n for w in trend_words):
                score += 15
            scored.append((score, it))
        scored.sort(key=lambda x: -x[0])
        picked = []
        for score, it in scored[:PER_CATEGORY]:
            seen.add(kb.norm(it["title"])[:24])
            picked.append({
                "title": it["title"], "source": it["source"], "url": it["url"],
                "related": it["related"], "score": score,
            })
        news[key] = picked
        picked_urls = {p["url"] for p in picked}
        added = 0
        for it in items[:40]:
            if it["url"] in picked_urls or added >= POOL_PER_CATEGORY:
                continue
            pool.append({"title": it["title"], "source": it["source"], "url": it["url"], "cat": key})
            added += 1
    log("edition", cc, sum(len(v) for v in news.values()), "trends", len(trends))
    return {"news": news, "pool": pool, "trends": trends}


# ---------------------------------------------------------------- 묶기

def build(out_path: str):
    now = dt.datetime.now(dt.timezone.utc)
    world = {
        "version": 1,
        "generatedAt": now.isoformat(timespec="seconds"),
        "cities": {
            cid: {"ko": ko, "en": en, "country": cc, "lat": lat, "lon": lon, "tz": tz}
            for cid, ko, en, cc, lat, lon, tz in CITIES
        },
        "weather": build_weather(),
        "holidays": build_holidays(),
        "editions": {cc: build_edition(gl) for cc, gl in EDITIONS.items()},
        "sources": {
            "weather": "MET Norway (CC BY 4.0)",
            "holidays": "Nager.Date",
            "news": "Google News",
            "trends": "Google Trends",
        },
    }
    if not world["weather"] and not any(e["news"] for e in world["editions"].values()):
        raise RuntimeError("world: 날씨·뉴스가 모두 비었다 — 이전 파일을 지킨다")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(world, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out_path)
    log("wrote", out_path, os.path.getsize(out_path), "bytes")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "world.json")
