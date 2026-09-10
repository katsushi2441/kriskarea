# -*- coding: utf-8 -*-
"""住所・座標 → 災害危険区域の判定。

設計の芯（kflood と同じ約束）:
  1. 「区域外」と「未収録」を必ず区別する。指定のない自治体で黙って「区域外」と答えない。
  2. 判定結果には必ず根拠（根拠条例・告示番号・告示年月日）とデータ時点を添える。
  3. 住所から求めた座標は町丁目の代表点なので、近くに区域があるときは言い切らない。
  4. 区域外は「安全」ではない。災害危険区域は建築制限のための指定であって、
     浸水想定そのものではない（そちらは kflood / khazard / ktsunami）。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "kriskarea.sqlite"

#: 代表点がこれより近ければ「区域外」と言い切らない（度。約250m）
NEAR_DEG = 0.0025


@dataclass
class Area:
    id: int
    pref: str
    city: str
    admin_code: str
    subject: str
    name: str
    address: str
    reason: str
    notice_date: str
    notice_no: str
    ordinance: str
    area_ha: str
    note: str


@dataclass
class Result:
    lat: float
    lon: float
    address: str = ""
    status: str = "uncovered"      # inside / outside / uncovered
    areas: list[Area] = field(default_factory=list)
    nearest_m: int | None = None
    notes: list[str] = field(default_factory=list)
    vintage: str = ""
    attribution: str = ""


class Index:
    """区域ポリゴンを一度だけ読んで空間索引に載せる。"""

    def __init__(self, db: Path = DB):
        self.db = db
        self._tree: STRtree | None = None
        self._rows: list[sqlite3.Row] = []
        self._geoms: list = []
        self._admin_codes: set[str] = set()
        self.vintage = ""
        self.attribution = ""

    def load(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        self._rows = conn.execute("SELECT * FROM areas").fetchall()
        self._geoms = [shape(json.loads(r["geometry"])) for r in self._rows]
        self._admin_codes = {r["admin_code"] for r in self._rows}
        meta = conn.execute("SELECT data_vintage, attribution FROM datasets LIMIT 1").fetchone()
        if meta:
            self.vintage, self.attribution = meta["data_vintage"], meta["attribution"]
        conn.close()
        self._tree = STRtree(self._geoms) if self._geoms else None

    @property
    def count(self) -> int:
        return len(self._rows)

    @property
    def city_count(self) -> int:
        return len(self._admin_codes)

    def _area(self, i: int) -> Area:
        r = self._rows[i]
        return Area(r["id"], r["pref"] or "", r["city"] or "", r["admin_code"] or "",
                    r["subject"] or "", r["name"] or "", r["address"] or "", r["reason"] or "",
                    r["notice_date"] or "", r["notice_no"] or "", r["ordinance"] or "",
                    r["area_ha"] or "", r["note"] or "")

    def covers_city(self, admin_code: str) -> bool:
        """その自治体に災害危険区域の指定データがあるか。

        政令指定都市は、住所検索が返すのが区のコード（名古屋市瑞穂区=23108）なのに対し、
        A48 は市のコード（名古屋市=23100）で入っている。区コードのままだと
        「指定のある市」を「データなし」と誤答するので、市コードへ丸めてもう一度見る。
        丸めた先が実在する場合だけ採用する（一般市の 23201 → 23200 のような
        存在しないコードを作らないため）。
        """
        if not admin_code:
            return False
        if admin_code in self._admin_codes:
            return True
        city_code = admin_code[:3] + "00"
        return city_code != admin_code and city_code in self._admin_codes

    def check(self, lat: float, lon: float, address: str = "", admin_code: str = "") -> Result:
        if self._tree is None:
            self.load()
        out = Result(lat=lat, lon=lon, address=address,
                     vintage=self.vintage, attribution=self.attribution)
        pt = Point(lon, lat)
        hit = [i for i in self._tree.query(pt) if self._geoms[i].covers(pt)]
        if hit:
            out.status = "inside"
            out.areas = [self._area(i) for i in hit]
            return out

        # 区域に入っていない。その自治体に指定があるかどうかで「区域外」と「未収録」を分ける。
        covered = self.covers_city(admin_code)
        near = pt.buffer(NEAR_DEG)
        neighbours = [i for i in self._tree.query(near)]
        if not covered and not neighbours:
            out.status = "uncovered"
            out.notes.append(
                "この住所の自治体には、災害危険区域の指定データがありません。"
                "指定が無いのか、国土数値情報に収録されていないのかは、このデータでは区別できません。"
                "自治体の建築指導課に確認してください。")
            return out

        out.status = "outside"
        if neighbours:
            d = min(self._geoms[i].distance(pt) for i in neighbours)
            out.nearest_m = int(d * 111_000)
            out.notes.append(
                f"最も近い災害危険区域まで約{out.nearest_m}mです。"
                "住所から求めた座標は町丁目のおおよその位置なので、実際の敷地が区域内である可能性があります。"
                "地番で確認してください。")
        out.notes.append(
            "災害危険区域は、建築基準法第39条にもとづき自治体が条例で指定する区域です。"
            "区域外であることは、水害や土砂災害の危険が無いという意味ではありません。"
            "浸水や土砂の想定は、洪水・内水・津波・土砂それぞれのハザードマップで確認してください。")
        return out
