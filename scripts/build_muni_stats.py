#!/usr/bin/env python3
"""市区町村ごとの災害危険区域の統計を作る（地域ページの中身）。

A48 は admin_code（全国地方公共団体コード）と city を持っているので住所の解析は要らない。
災害危険区域は建築基準法39条にもとづき**自治体の条例**で指定されるので、
根拠条例・告示番号・指定理由（急傾斜地崩壊/津波/出水など）が市区町村ごとに違う。
そこが地域ページに載せる価値のある実データ。

  cd /home/kojima/work/kriskarea && /usr/bin/python3 scripts/build_muni_stats.py
"""
import os, json, sqlite3, collections

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "kriskarea.sqlite")

DDL = """
CREATE TABLE IF NOT EXISTS muni_stats (
  admin_code TEXT PRIMARY KEY, pref_code TEXT, pref TEXT, city TEXT,
  areas INTEGER, reasons TEXT, subject TEXT, ordinances TEXT,
  first_notice TEXT, last_notice TEXT, area_ha REAL, samples TEXT
);
CREATE INDEX IF NOT EXISTS muni_stats_pref ON muni_stats(pref_code);
"""


def _d(s):
    """'2000/10/3' を並べ替えできる形に。9999 は不明を表す欠測コード。"""
    try:
        y, m, d = (int(x) for x in (s or "").split("/"))
        return None if y >= 9999 else f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:  # noqa: BLE001
        return None


def main():
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    agg = {}
    for code, pc, pref, city, subj, reason, ordi, nd, no, ha, name, addr in con.execute(
            "SELECT admin_code,pref_code,pref,city,subject,reason,ordinance,notice_date,"
            "notice_no,area_ha,name,address FROM areas"):
        a = agg.get(code)
        if a is None:
            a = agg[code] = dict(pref_code=pc, pref=pref, city=city, areas=0,
                                 reasons=collections.Counter(), subjects=collections.Counter(),
                                 ordinances=collections.Counter(), dates=set(), ha=0.0, samples=[])
        a["areas"] += 1
        if reason: a["reasons"][reason] += 1
        if subj: a["subjects"][subj] += 1
        if ordi: a["ordinances"][ordi.replace("　", " ").strip()] += 1
        dd = _d(nd)
        if dd: a["dates"].add(dd)
        try: a["ha"] += float(ha or 0)
        except (TypeError, ValueError): pass
        if len(a["samples"]) < 5 and (name or addr):
            s = {"name": name or "", "address": addr or "", "reason": reason or "", "notice_no": no or ""}
            if s not in a["samples"]:
                a["samples"].append(s)
    con.execute("DELETE FROM muni_stats")
    con.executemany(
        "INSERT INTO muni_stats (admin_code,pref_code,pref,city,areas,reasons,subject,"
        "ordinances,first_notice,last_notice,area_ha,samples) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(code, a["pref_code"], a["pref"], a["city"], a["areas"],
          json.dumps(a["reasons"].most_common(), ensure_ascii=False),
          (a["subjects"].most_common(1) or [("", 0)])[0][0],
          json.dumps([o for o, _ in a["ordinances"].most_common(4)], ensure_ascii=False),
          min(a["dates"]) if a["dates"] else None, max(a["dates"]) if a["dates"] else None,
          round(a["ha"], 2), json.dumps(a["samples"], ensure_ascii=False))
         for code, a in agg.items()])
    con.commit()
    print(f"市区町村 {len(agg):,} / 区域 {sum(a['areas'] for a in agg.values()):,}")
    for r in con.execute("SELECT pref,city,areas,reasons,subject FROM muni_stats ORDER BY areas DESC LIMIT 5"):
        print(f"   {r[0]}{r[1]}: 区域{r[2]:,} 理由={r[3][:50]} 指定者={r[4]}")
    con.close()


if __name__ == "__main__":
    main()
