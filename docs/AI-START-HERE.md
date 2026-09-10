# kriskarea をこれから触るAIへ

FastAPI ＋ SQLite ＋ shapely。データは国土数値情報 災害危険区域（A48）。

## 先に読むもの

- `README.md` の「設計の芯」4項目。ここを崩すと製品の意味が無くなる。
- `app/codes.py` の `COMMERCIAL_USE_DENIED`。**商用利用不可の自治体を取り込むと利用条件違反**になる。

## よくある誤り

- **「区域外」と「データなし」を混ぜる。** 指定のない自治体は37都道府県以外に多数ある。
  黙って「区域外」と答えると、利用者は安全だと誤解する。
- **政令指定都市のコード。** 住所検索は区のコード（名古屋市瑞穂区=23108）を返すが、
  A48 は市のコード（名古屋市=23100）で入っている。`Index.covers_city()` が丸めている。
  丸めた先が実在する場合だけ採用する（一般市の 23201 → 23200 を作らないため）。
- **PostGIS を持ち込む。** 2万件しかないので要らない。導入の手間が増えるだけ。

## データ更新

A48 は不定期更新（2020年度版・2021年度版がある）。更新するときは:

1. 配布ページで最新年度を確認する（`https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A48-2021.html` は2021年度版）。
   **旧年度のページは「最新」と書いたまま更新されない**ので、必ず一覧 `https://nlftp.mlit.go.jp/ksj/index.html` から辿る。
2. `R6_Terms_of_Use_DisasterRiskArea.xlsx` を取り直し、`COMMERCIAL_USE_DENIED` を更新する。
3. `scripts/load_a48.py` の `BASE` と `VINTAGE` を新年度に合わせて再取り込み。
4. `.venv/bin/python -m pytest tests` が通ることを確認する。
