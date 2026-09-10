#!/usr/bin/env python3
"""国土数値情報 災害危険区域（A48）を SQLite に取り込む。

  python3 scripts/load_a48.py            # 未取得の都道府県をダウンロードして全部入れる
  python3 scripts/load_a48.py 23 24      # 都道府県コードを指定

なぜ PostGIS ではなく SQLite か:
  区域は全国で数千件しかない（kflood の洪水は2,800万面あるので PostGIS が要る）。
  買い切りキットとして配る以上、docker も postgres も要らない方が導入が速い。
  判定は shapely の STRtree で足りる。

利用条件（重要）:
  自治体ごとに条件が違う。商用利用不可・再配布不可・非公開の自治体は
  app/codes.py の COMMERCIAL_USE_DENIED で取り込みから外す。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.codes import COMMERCIAL_USE_DENIED, REASON, SUBJECT  # noqa: E402

BASE = "https://nlftp.mlit.go.jp/ksj/gml/data/A48/A48-21"
RAW = Path(os.environ.get("KRISKAREA_RAW_DIR", "/mnt/data/kriskarea/raw"))
DB = ROOT / "data" / "kriskarea.sqlite"
VINTAGE = "2021年度（令和3年度）版・2021年7月時点"
ATTRIBUTION = "出典: 国土数値情報（災害危険区域）国土交通省 を加工して作成"
UA = {"User-Agent": "kriskarea/1.0 (kurage.exbridge.jp)"}


def fetch(pref: str) -> Path | None:
    RAW.mkdir(parents=True, exist_ok=True)
    name = f"A48-21_{pref}_GML.zip"
    path = RAW / name
    if path.exists() and path.stat().st_size:
        return path
    try:
        req = urllib.request.Request(f"{BASE}/{name}", headers=UA)
        with urllib.request.urlopen(req, timeout=180) as r, open(str(path) + ".part", "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        os.replace(str(path) + ".part", path)
        return path
    except Exception:
        # 指定のない都道府県は配布されていない（404）。異常ではない。
        for leftover in RAW.glob(f"{name}.part"):
            leftover.unlink(missing_ok=True)
        return None


def schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS areas (
          id INTEGER PRIMARY KEY,
          pref_code TEXT NOT NULL,
          pref TEXT, city TEXT, admin_code TEXT,
          subject TEXT, name TEXT, address TEXT,
          reason_code TEXT, reason TEXT,
          notice_date TEXT, notice_no TEXT, ordinance TEXT,
          area_ha TEXT, scale TEXT, note TEXT,
          minx REAL, miny REAL, maxx REAL, maxy REAL,
          geometry TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS areas_bbox ON areas(minx, maxx, miny, maxy);
        CREATE INDEX IF NOT EXISTS areas_admin ON areas(admin_code);
        CREATE TABLE IF NOT EXISTS datasets (
          pref_code TEXT PRIMARY KEY, pref TEXT, count INTEGER,
          data_vintage TEXT, attribution TEXT, loaded_at TEXT);
        CREATE TABLE IF NOT EXISTS excluded (
          admin_code TEXT PRIMARY KEY, note TEXT);
        """
    )


def bbox(geom: dict) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(c):
        if isinstance(c, (int, float)):
            return
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1]); return
        for x in c:
            walk(x)

    walk(geom["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


def load_pref(conn: sqlite3.Connection, pref: str) -> int:
    path = fetch(pref)
    if not path:
        return -1
    with zipfile.ZipFile(path) as z:
        target = next((n for n in z.namelist() if n.endswith(".geojson")), None)
        if not target:
            return -1
        data = json.loads(z.read(target).decode("utf-8"))
    conn.execute("DELETE FROM areas WHERE pref_code=?", (pref,))
    kept = 0
    skipped: dict[str, str] = {}
    for f in data.get("features", []):
        p = f.get("properties") or {}
        admin = str(p.get("A48_003") or "")
        if admin in COMMERCIAL_USE_DENIED:
            skipped[admin] = COMMERCIAL_USE_DENIED[admin]
            continue
        g = f.get("geometry")
        if not g or not g.get("coordinates"):
            continue
        minx, miny, maxx, maxy = bbox(g)
        conn.execute(
            """INSERT INTO areas
               (pref_code,pref,city,admin_code,subject,name,address,reason_code,reason,
                notice_date,notice_no,ordinance,area_ha,scale,note,minx,miny,maxx,maxy,geometry)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pref, p.get("A48_001"), p.get("A48_002"), admin,
             SUBJECT.get(str(p.get("A48_004") or ""), ""), p.get("A48_005"), p.get("A48_006"),
             str(p.get("A48_007") or ""), p.get("A48_008"),
             p.get("A48_009"), p.get("A48_010"), p.get("A48_011"),
             str(p.get("A48_012") or ""), p.get("A48_013"), p.get("A48_014"),
             minx, miny, maxx, maxy, json.dumps(g, ensure_ascii=False)),
        )
        kept += 1
    for code, note in skipped.items():
        conn.execute("INSERT OR REPLACE INTO excluded(admin_code,note) VALUES(?,?)", (code, note))
    pref_name = next((f["properties"].get("A48_001") for f in data.get("features", []) if f.get("properties")), "")
    conn.execute(
        """INSERT OR REPLACE INTO datasets(pref_code,pref,count,data_vintage,attribution,loaded_at)
           VALUES(?,?,?,?,?,?)""",
        (pref, pref_name, kept, VINTAGE, ATTRIBUTION, time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    if skipped:
        print(f"  {pref} {pref_name}: {kept}件（利用条件により除外 {len(skipped)}自治体）")
    else:
        print(f"  {pref} {pref_name}: {kept}件")
    return kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prefs", nargs="*", help="都道府県コード(2桁)。省略で全国")
    a = ap.parse_args()
    prefs = a.prefs or [f"{i:02d}" for i in range(1, 48)]
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    schema(conn)
    total = 0
    missing = []
    for pref in prefs:
        n = load_pref(conn, pref)
        if n < 0:
            missing.append(pref)
        else:
            total += n
    conn.commit()
    cities = conn.execute("SELECT COUNT(DISTINCT admin_code) FROM areas").fetchone()[0]
    conn.close()
    print(f"\n合計 {total:,}件 / {cities}自治体")
    if missing:
        print(f"配布なし（災害危険区域の指定が無い都道府県）: {' '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
