#!/usr/bin/env python3
"""国土数値情報 密集市街地（A39）を SQLite に取り込む。

  /usr/bin/python3 scripts/load_a39.py

**災害危険区域（A48）とは別のもの。**
  A48 = 建築基準法39条で、条例により**建築が制限される**区域
  A39 = 「地震時等に著しく危険な密集市街地」として国が公表した地区
       （建築を直接制限する線ではなく、**重点的に改善する対象**として挙げられた地区）

配布は**全国1ファイル**（A39-15_GML.zip・228地区）。都道府県別に分かれていない。
Shapefile の文字コードは CP932 なので、読むときに指定する（UTF-8 で読むと落ちる）。

データ基準は**2011年（平成23年）**で古い。画面には必ず時点を出す。
「いま危険な地区の一覧」ではなく「2011年時点でそう公表された地区」として扱う。

利用条件: CC BY 4.0（出典表示のみ）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://nlftp.mlit.go.jp/ksj/gml/data/A39/A39-15/A39-15_GML.zip"
RAW = Path(os.environ.get("KRISKAREA_RAW_DIR", "/mnt/data/kriskarea/raw"))
DB = ROOT / "data" / "kriskarea.sqlite"
VINTAGE = "2015年度版・調査時点 2011年（平成23年）"
ATTRIBUTION = "出典: 国土数値情報（密集市街地）国土交通省 を加工して作成（CC BY 4.0）"
UA = {"User-Agent": "kriskarea/1.0 (kurage.exbridge.jp)"}

# 属性の並びは国土数値情報の仕様（A39_001…）。名前が付いていないので、使う列だけ対応させる。
COLS = dict(pref_code="A39_001", admin_code="A39_002", city="A39_003",
            number="A39_004", name="A39_006", area_ha="A39_007", town="A39_008")


def fetch() -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / "A39-15_GML.zip"
    if path.exists() and path.stat().st_size:
        return path
    req = urllib.request.Request(URL, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r, open(str(path) + ".part", "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    os.replace(str(path) + ".part", path)
    return path


def to_geojson(zip_path: Path) -> dict:
    work = RAW / "A39"
    work.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(work)
    shp = next(work.rglob("*.shp"))
    out = work / "a39.geojson"
    if out.exists():
        out.unlink()
    # Shapefile の属性は CP932。指定しないと ogr2ogr の出力が壊れる。
    subprocess.run(["ogr2ogr", "-f", "GeoJSON", "--config", "SHAPE_ENCODING", "CP932",
                    str(out), str(shp)], check=True, capture_output=True)
    return json.loads(out.read_text(encoding="utf-8"))


def schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dense_areas (
          id INTEGER PRIMARY KEY,
          pref_code TEXT, admin_code TEXT, city TEXT,
          number TEXT, name TEXT, area_ha REAL, town TEXT,
          minx REAL, miny REAL, maxx REAL, maxy REAL,
          geometry TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS dense_bbox ON dense_areas(minx, maxx, miny, maxy);
        CREATE TABLE IF NOT EXISTS dense_datasets (
          id INTEGER PRIMARY KEY CHECK (id = 1), count INTEGER,
          data_vintage TEXT, attribution TEXT, loaded_at TEXT);
        """
    )


def bbox(geom: dict):
    xs, ys = [], []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1]); return
        for x in c:
            walk(x)

    walk(geom["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


def main() -> None:
    data = to_geojson(fetch())
    conn = sqlite3.connect(DB)
    schema(conn)
    conn.execute("DELETE FROM dense_areas")
    kept = 0
    for f in data.get("features", []):
        g, p = f.get("geometry"), f.get("properties") or {}
        if not g or not g.get("coordinates"):
            continue
        minx, miny, maxx, maxy = bbox(g)
        conn.execute(
            """INSERT INTO dense_areas
               (pref_code,admin_code,city,number,name,area_ha,town,minx,miny,maxx,maxy,geometry)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(p.get(COLS["pref_code"]) or ""), str(p.get(COLS["admin_code"]) or ""),
             str(p.get(COLS["city"]) or ""), str(p.get(COLS["number"]) or ""),
             str(p.get(COLS["name"]) or ""), p.get(COLS["area_ha"]),
             str(p.get(COLS["town"]) or ""), minx, miny, maxx, maxy,
             json.dumps(g, ensure_ascii=False)))
        kept += 1
    conn.execute("""INSERT OR REPLACE INTO dense_datasets (id,count,data_vintage,attribution,loaded_at)
                    VALUES (1,?,?,?,?)""", (kept, VINTAGE, ATTRIBUTION, time.strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    cities = conn.execute("SELECT count(DISTINCT admin_code) FROM dense_areas").fetchone()[0]
    conn.close()
    print(f"密集市街地: {kept}地区 / {cities}市区町村 → {DB}")
    if kept < 200:
        print("！ 地区が少なすぎる。配布ファイルが変わった可能性がある", file=sys.stderr)


if __name__ == "__main__":
    main()
