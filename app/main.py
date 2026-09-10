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
import os
import re
from datetime import date

import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app.lookup import Index

PORT = int(os.environ.get("KRISKAREA_PORT", "18311"))
SITE = os.environ.get("KRISKAREA_SITE_NAME", "Kurage 災害危険区域マップ")
GSI = "https://msearch.gsi.go.jp/address-search/AddressSearch"
UA = {"User-Agent": "kriskarea/1.0 (kurage.exbridge.jp)"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(ROOT, "app", "templates"))
app = FastAPI(title=SITE)
INDEX = Index()

LINKS = {
    "kflood": "https://kurage.exbridge.jp/kflood.php/",
    "khazard": "https://kurage.exbridge.jp/khazard.php/",
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


def page(request: Request, name: str, **kw):
    kw.update(site=SITE, links=LINKS, year=date.today().year,
              count=INDEX.count, city_count=INDEX.city_count,
              vintage=INDEX.vintage, attribution=INDEX.attribution)
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
    }


@app.get("/healthz")
def healthz():
    return {"ok": True, "areas": INDEX.count, "cities": INDEX.city_count, "vintage": INDEX.vintage}


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return page(request, "about.html")


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nAllow: /\n"
