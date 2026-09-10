"""災害危険区域の判定。

期待値は国土数値情報 A48 の実データそのもの。
「区域外」と「未収録」を取り違えないことが、この製品でいちばん大事な性質なので固定する。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.codes import COMMERCIAL_USE_DENIED, REASON, SUBJECT
from app.lookup import Index

INDEX = Index()


class LookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / "data" / "kriskarea.sqlite").exists():
            raise unittest.SkipTest("データ未取り込み（scripts/load_a48.py）")
        INDEX.load()

    def test_data_loaded(self):
        self.assertGreater(INDEX.count, 10000)
        self.assertGreater(INDEX.city_count, 400)
        self.assertIn("2021年度", INDEX.vintage)

    def test_inside_returns_ordinance_and_notice(self):
        """名古屋市港区役所あたりは臨海部防災区域。根拠条例と告示が出ること。"""
        r = INDEX.check(35.1076, 136.8859, "名古屋市港区", "23111")
        self.assertEqual(r.status, "inside")
        a = r.areas[0]
        self.assertIn("臨海部防災区域", a.name)
        self.assertEqual(a.ordinance, "名古屋市臨海部防災区域建築条例")
        self.assertTrue(a.notice_no)
        self.assertTrue(a.notice_date)
        # 建築制限の中身（基準高）が空でないこと＝この製品の核心
        self.assertTrue(a.note)

    def test_outside_is_not_uncovered(self):
        """指定のある自治体の中で区域に入っていない場所は outside。uncovered にしない。"""
        # 実測座標（名古屋市瑞穂区内浜町34-9 を国土地理院の住所検索で引いた点）
        r = INDEX.check(35.111877, 136.918625, "名古屋市瑞穂区", "23108")
        self.assertEqual(r.status, "outside")
        self.assertTrue(any("危険が無いという意味ではありません" in n for n in r.notes))

    def test_ward_code_is_normalized_to_city_code(self):
        """政令市は住所検索が区コード(23108)を返すが、A48は市コード(23100)で入っている。
        丸めないと『指定のある市』を『データなし』と誤答する。"""
        self.assertTrue(INDEX.covers_city("23108"))
        self.assertTrue(INDEX.covers_city("23100"))
        # 一般市の 23201 → 23200 のような実在しないコードを作らないこと
        self.assertFalse(INDEX.covers_city("23201"))

    def test_uncovered_when_no_designation(self):
        """指定データが無い自治体は uncovered。黙って「区域外」と答えない。"""
        r = INDEX.check(35.6846, 139.7530, "東京都千代田区", "13101")
        self.assertEqual(r.status, "uncovered")
        self.assertTrue(any("確認してください" in n for n in r.notes))

    def test_near_area_is_flagged(self):
        """代表点が区域の近くなら距離を添える（言い切らない）。"""
        r = INDEX.check(35.111877, 136.918625, "名古屋市瑞穂区", "23108")
        self.assertIsNotNone(r.nearest_m)
        self.assertLess(r.nearest_m, 300)

    def test_denied_municipalities_are_not_loaded(self):
        """商用利用不可・非公開の自治体は1件も入っていないこと。"""
        loaded = {r["admin_code"] for r in INDEX._rows}
        for code in COMMERCIAL_USE_DENIED:
            self.assertNotIn(code, loaded, f"{code} は利用条件により収録してはいけない")

    def test_code_tables(self):
        self.assertEqual(SUBJECT["2"], "市町村")
        self.assertEqual(REASON["2"], "津波・高潮")


if __name__ == "__main__":
    unittest.main()
