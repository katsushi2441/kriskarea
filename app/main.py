# -*- coding: utf-8 -*-
"""Kurage 災害危険区域マップ（内部の略称 kriskarea）

住所を入れると、その場所が建築基準法39条の「災害危険区域」に指定されているかを返す。
指定されていれば、根拠条例・告示番号・告示年月日・区域の区分（災害危険基準高など）まで出す。

ハザードマップとの違い:
  ハザードマップは「浸水します」。災害危険区域は「条例で建築が制限されます」。
  家を買う・建てる前に効くのは後者で、住所で引ける民間の道具が無かった。

構成:
  ジオコーディング: 国土地理院 AddressSearch API（無料・キー不要）
  判定            : SQLite + shapely（区域は全国2万件なので PostGIS は要らない）
  データ          : 国土数値情報 災害危険区域（A48）。商用利用不可・非公開の自治体は取り込まない
"""
import json
import os
import re
from datetime import date

import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.lookup import Index

PORT = int(os.environ.get("KRISKAREA_PORT", "18311"))
SITE = os.environ.get("KRISKAREA_SITE_NAME", "Kurage 災害危険区域マップ")
PUBLIC_BASE = os.environ.get("KRISKAREA_PUBLIC_BASE", "https://kurage.exbridge.jp/kriskarea.php").rstrip("/")
GSI = "https://msearch.gsi.go.jp/address-search/AddressSearch"
UA = {"User-Agent": "kriskarea/1.0 (kurage.exbridge.jp)"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(ROOT, "app", "templates"))
app = FastAPI(title=SITE)
app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "app", "static")), name="static")
INDEX = Index()

LINKS = {
    # 買い切り版の商品ページ。デモから商品へ必ず導線を張る（全製品そろえる）
    "kappstore": "https://kappstore.exbridge.jp/app.php?id=23c57241bd8df841&ref=kriskarea",
    "kflood": "https://kurage.exbridge.jp/kflood.php/",
    "khazard": "https://kurage.exbridge.jp/khazard.php/",
    "kmorido": "https://kurage.exbridge.jp/kmorido.php/",
    "ktsunami": "https://kurage.exbridge.jp/ktsunami.php/",
    "kfault": "https://kurage.exbridge.jp/kfault.php/",
    "portal": "https://disaportal.gsi.go.jp/maps/",
}


@app.on_event("startup")
def _startup() -> None:
    INDEX.load()


def geocode(q: str):
    """住所→座標。国土地理院の住所検索API。見つからなければ None。"""
    r = requests.get(GSI, params={"q": q}, headers=UA, timeout=10)
    r.raise_for_status()
    items = r.json()
    if not items:
        return None
    top = items[0]
    lon, lat = top["geometry"]["coordinates"]
    return float(lat), float(lon), top["properties"].get("title") or q


def admin_code_of(title: str) -> str:
    """住所文字列から行政コードは引けないので、市町村名で突き合わせる。
    データ側の city と一致する自治体があれば、その行政コードを返す。"""
    m = re.match(r"(.+?[都道府県])(.+?[市区町村])", title or "")
    if not m:
        return ""
    pref, city = m.group(1), m.group(2)
    for row in INDEX._rows:  # 読み込み済みの区域から引く
        if row["pref"] == pref and row["city"] and city.startswith(row["city"]):
            return row["admin_code"] or ""
    return ""


FAQ = [
    ("災害危険区域とは何ですか",
     "建築基準法第39条にもとづき、市町村や都道府県が条例で指定する区域です。津波・高潮・出水・崖崩れなどの"
     "危険が著しいと認められる区域で、条例により住宅の建築が禁止されたり、居室の床の高さなどの条件が付きます。"),
    ("ハザードマップと何が違うのですか",
     "ハザードマップは「その場所が浸水する想定か」を示す図で、建築の可否は決めません。災害危険区域は条例による"
     "建築規制そのもので、指定されていると家が建てられない、あるいは条件付きになります。家を買う・建てる前に"
     "効くのは災害危険区域のほうです。"),
    ("不動産取引で説明されますか",
     "災害危険区域は宅地建物取引業法の重要事項説明の対象です（施行規則第16条の4の3ほか、法令に基づく制限として"
     "説明されます）。ただし説明されるのは契約の直前です。土地を探している段階で自分で確かめられるように作りました。"),
    ("このサイトの判定は公的な証明になりますか",
     "なりません。住所から求めた代表点による参考情報です。正確な区域の境界は、その自治体の建築指導課（建築主事）で"
     "確認してください。データを取り込んでいない自治体では「区域外」ではなく「未収録」と表示します。"),
]


def jsonld_for(path: str) -> str:
    """構造化データ。AI検索・検索エンジンに「何を答えるサイトか」を機械可読で渡す。"""
    graph = [{
        "@type": "WebSite",
        "@id": PUBLIC_BASE + "/#website",
        "name": SITE,
        "url": PUBLIC_BASE + "/",
        "inLanguage": "ja",
        "publisher": {"@type": "Organization", "name": "株式会社エクスブリッジ", "url": "https://exbridge.jp/"},
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint", "urlTemplate": PUBLIC_BASE + "/?q={search_term_string}"},
            "query-input": "required name=search_term_string",
        },
    }]
    if path in ("/", "/about"):
        graph.append({
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q,
                 "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in FAQ
            ],
        })
    if path != "/":
        graph.append({
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": SITE, "item": PUBLIC_BASE + "/"},
                {"@type": "ListItem", "position": 2,
                 "name": "地図で見る" if path.startswith("/map") else "このデータについて",
                 "item": PUBLIC_BASE + path},
            ],
        })
    return json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)


def root_prefix(path: str) -> str:
    """画面内のリンクに付ける相対プレフィックス。

    公開時は heteml の /kriskarea.php/ 配下に置かれるので、リンクを "/about" と
    絶対で書くとサイト直下（404）へ飛ぶ。置き場所が変わっても壊れないよう、
    ページの深さから "../" を組み立てる。末尾スラッシュの有無で深さが変わる。
    """
    segs = [s for s in path.split("/") if s]
    depth = len(segs) if path.endswith("/") else max(0, len(segs) - 1)
    return "../" * depth


def page(request: Request, name: str, **kw):
    # canonical / og:url は公開URL（heteml のプロキシ経由）で出す。バックエンドの
    # 127.0.0.1:18311 を書くと検索エンジンにもAIにも届かないURLになる。
    path = request.url.path
    kw.update(site=SITE, links=LINKS, year=date.today().year,
              count=INDEX.count, city_count=INDEX.city_count,
              vintage=INDEX.vintage, attribution=INDEX.attribution,
              public_base=PUBLIC_BASE, canonical=PUBLIC_BASE + path,
              terms=TERMS, root=root_prefix(path), jsonld=jsonld_for(path))
    return templates.TemplateResponse(request, name, kw)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = ""):
    result = None
    error = ""
    if q.strip():
        try:
            found = geocode(q.strip())
            if not found:
                error = "住所が見つかりませんでした。市区町村から入れ直してください。"
            else:
                lat, lon, title = found
                result = INDEX.check(lat, lon, title, admin_code_of(title))
        except requests.RequestException:
            error = "住所検索に接続できませんでした。時間をおいて試してください。"
    return page(request, "index.html", q=q, result=result, error=error)


@app.get("/api/check")
def api_check(q: str = "", lat: float = None, lon: float = None):
    if lat is not None and lon is not None:
        r = INDEX.check(lat, lon, "", "")
    elif q.strip():
        found = geocode(q.strip())
        if not found:
            return JSONResponse({"error": "住所が見つかりません"}, status_code=404)
        lat, lon, title = found
        r = INDEX.check(lat, lon, title, admin_code_of(title))
    else:
        return JSONResponse({"error": "q または lat/lon が要ります"}, status_code=400)
    return {
        "address": r.address, "lat": r.lat, "lon": r.lon, "status": r.status,
        "areas": [a.__dict__ for a in r.areas], "nearest_m": r.nearest_m,
        "notes": r.notes, "data_vintage": r.vintage, "attribution": r.attribution,
        # 地震時等に著しく危険な密集市街地（A39）。災害危険区域とは別の指定なので別キーで返す。
        "dense_area": {
            "status": r.dense_status,
            "areas": [d.__dict__ for d in r.dense],
            "data_vintage": r.dense_vintage,
            "attribution": r.dense_attribution,
        },
    }


@app.get("/api/areas.geojson")
def areas_geojson(bbox: str = "", limit: int = 4000):
    """表示中の範囲にある災害危険区域を GeoJSON で返す。

    区域は全国で2万件しかないので、ベクタータイルを焼かずに範囲で切って返す。
    bbox は "minlon,minlat,maxlon,maxlat"。
    """
    try:
        minx, miny, maxx, maxy = [float(v) for v in bbox.split(",")]
    except ValueError:
        return JSONResponse({"error": "bbox は minlon,minlat,maxlon,maxlat の形で渡してください"}, status_code=400)
    if INDEX._tree is None:
        INDEX.load()
    feats = []
    for i, row in enumerate(INDEX._rows):
        # 矩形が重ならないものを先に落とす（SQLiteに入れておいた外接矩形で判定）
        if row["maxx"] < minx or row["minx"] > maxx or row["maxy"] < miny or row["miny"] > maxy:
            continue
        feats.append({
            "type": "Feature",
            "geometry": json.loads(row["geometry"]),
            "properties": {
                "id": row["id"], "name": row["name"] or "", "city": row["city"] or "",
                "pref": row["pref"] or "", "reason": row["reason"] or "",
                "ordinance": row["ordinance"] or "", "notice": f'{row["notice_date"] or ""} {row["notice_no"] or ""}'.strip(),
                "note": row["note"] or "",
            },
        })
        if len(feats) >= limit:
            break
    return {"type": "FeatureCollection", "features": feats, "truncated": len(feats) >= limit}


@app.get("/map/", response_class=HTMLResponse)
def map_page(request: Request, lat: float = None, lon: float = None, q: str = ""):
    # 住所が来たら座標に直してから地図に渡す（地図側で住所検索を持たない）
    if q.strip() and lat is None:
        try:
            found = geocode(q.strip())
            if found:
                lat, lon = found[0], found[1]
        except requests.RequestException:
            pass
    return page(request, "map.html", lat=lat, lon=lon, q=q[:100])


@app.get("/healthz")
def healthz():
    return {"ok": True, "areas": INDEX.count, "cities": INDEX.city_count, "vintage": INDEX.vintage}


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return page(request, "about.html")



# 検索する人の言い方と、法令・行政の用語はずれている。両方の語で拾えるようにする。
# 実例: 名古屋市は「内水ハザードマップ」を「雨水出水浸水想定区域」へ改称し、URLも変えた
# （旧URLは404。2026-09-14 実測）。
TERMS = [
    ('災害危険区域', '建築基準法39条にもとづき自治体の条例で指定する区域'),
    ('家が建てられない土地', '災害危険区域（居住用建築物の建築が制限・禁止されます）'),
    ('がけ条例', '自治体の建築基準法施行条例。災害危険区域とは別の制限のことがあります'),
    ('津波で流された場所', '指定理由が「津波」の災害危険区域（東日本大震災後の指定が多い）'),
    ('急傾斜地', '指定理由の「急傾斜地崩壊」。土砂災害警戒区域とは別の制度です'),
    ('土砂災害警戒区域との違い', 'あちらは土砂災害防止法で都道府県が指定。こちらは建築基準法で自治体が条例で指定'),
]

MUNI = {}
MUNI_BY_PREF = {}


def _load_muni():
    """muni_stats（scripts/build_muni_stats.py が作る）を起動時に読む。588件。"""
    import sqlite3
    out = {}
    try:
        c = sqlite3.connect(os.path.join(ROOT, "data", "kriskarea.sqlite"))
        c.row_factory = sqlite3.Row
        for r in c.execute("SELECT * FROM muni_stats"):
            d = dict(r)
            d["reasons"] = json.loads(d["reasons"] or "[]")
            d["ordinances"] = json.loads(d["ordinances"] or "[]")
            d["samples"] = json.loads(d["samples"] or "[]")
            d["top_reason"] = d["reasons"][0][0] if d["reasons"] else ""
            out[d["admin_code"]] = d
        c.close()
    except Exception as e:  # noqa: BLE001
        print("muni_stats を読めません（地域ページは出ません）:", e)
    return out


MUNI = _load_muni()
for _d in sorted(MUNI.values(), key=lambda x: -x["areas"]):
    MUNI_BY_PREF.setdefault(_d["pref_code"], []).append(_d)


@app.get("/area/", response_class=HTMLResponse)
@app.get("/area", response_class=HTMLResponse)
def area_index(request: Request):
    return page(request, "area_index.html",
                prefs=sorted(MUNI_BY_PREF.items(), key=lambda x: x[0]),
                muni_count=len(MUNI), total=sum(d["areas"] for d in MUNI.values()))


@app.get("/area/pref/{pref_code}", response_class=HTMLResponse)
def area_pref(request: Request, pref_code: str):
    """都道府県ごとの一覧。市区町村ページをクロールさせる内部リンクの束ね役。"""
    lst = MUNI_BY_PREF.get(pref_code)
    if not lst:
        return JSONResponse({"error": "その都道府県のページはありません"}, status_code=404)
    return page(request, "area_pref.html", pref=lst[0]["pref"], rows=lst,
                total=sum(d["areas"] for d in lst))


@app.get("/area/{code}", response_class=HTMLResponse)
def area(request: Request, code: str):
    m = MUNI.get(code)
    if not m:
        return JSONResponse({"error": "その市区町村のページはありません"}, status_code=404)
    sib = [x for x in MUNI_BY_PREF.get(m["pref_code"], []) if x["admin_code"] != code][:40]
    reason_txt = "・".join(f"{r}{n:,}区域" for r, n in m["reasons"][:3])
    return page(request, "area.html", m=m, siblings=sib, reasons=m["reasons"][:4],
                ordinances=m["ordinances"], reason_txt=reason_txt)


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return f"User-agent: *\nAllow: /\n\nSitemap: {PUBLIC_BASE}/sitemap.xml\n"


@app.get("/sitemap.xml")
def sitemap():
    # 588市区町村＋都道府県。枚数を出さないと検索の入口が増えない（2026-09-13 実測の結論）
    paths = (["/", "/map/", "/about", "/area/"]
             + [f"/area/pref/{pc}" for pc in sorted(MUNI_BY_PREF)]
             + [f"/area/{c}" for c in sorted(MUNI)])
    urls = "".join(
        f"<url><loc>{PUBLIC_BASE}{p}</loc><changefreq>monthly</changefreq></url>"
        for p in paths
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(content=xml, media_type="application/xml")


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms():
    """AI検索（ChatGPT/Claude/Perplexity 等）向けの要約。何を答えられる道具かを最初に書く。"""
    prefs = sorted({r["pref"] for r in INDEX._rows if r["pref"]})
    return f"""# {SITE}

> 住所を入れると、その場所が建築基準法第39条にもとづく「災害危険区域」に指定されているかを返すサイト。
> 指定されている場合は、根拠となる条例名・告示番号・告示年月日・区域の区分（災害危険基準高など）と、
> その条例で禁止・制限されている建築行為の内容まで表示する。

## ハザードマップとの違い（よく混同される）
- ハザードマップ: その場所が「浸水する想定か」を示す。建築の可否は決めない。
- 災害危険区域: 市町村・都道府県が条例で指定し、住宅の建築が「禁止」されたり、床の高さなどの条件が付く。
  家を買う・建てる前に効くのはこちら。重要事項説明の対象（宅地建物取引業法施行規則 第16条の4の3）。

## 収録
- 区域数: {INDEX.count:,}
- 自治体数: {INDEX.city_count}
- 都道府県: {", ".join(prefs)}
- データ時点: {INDEX.vintage}
- {INDEX.attribution}
- 商用利用を認めていない自治体のデータは収録していない（そのため「未収録」と「区域外」を区別して返す）


## 買い切り版
- 商品ページ: https://kappstore.exbridge.jp/app.php?id=23c57241bd8df841
- 税込55,000円。ソースコード（MIT）・データ取り込みスクリプト・設置手順書を同梱。自社サーバーで動かせる。

## 使い方
- 住所で調べる: {PUBLIC_BASE}/?q=<住所>
- 地図で見る: {PUBLIC_BASE}/map/
- データの説明: {PUBLIC_BASE}/about
- API: {PUBLIC_BASE}/api/check?q=<住所> （JSON。status は inside / outside / uncovered の3値）

## 注意
判定は住所から求めた代表点による参考情報で、公的な証明ではない。
正確な区域は、その自治体の建築指導課（建築主事）で確認すること。

## 関連（同じ運営の防災ツール）
- 洪水・内水ハザードマップ: {LINKS['kflood']}
- 重ねるハザードマップ（国土交通省）: {LINKS['portal']}

運営: 株式会社エクスブリッジ https://exbridge.jp/
"""
