# Kurage 災害危険区域マップ（kriskarea）

住所を入れると、その場所が **建築基準法第39条の「災害危険区域」** に指定されているかを返します。
指定されていれば、根拠条例・告示番号・告示年月日・区域の区分（災害危険基準高など）まで表示します。

- 公開: https://kurage.exbridge.jp/kriskarea.php/
- 常駐: systemd user unit `kriskarea.service` :18311

## ハザードマップとの違い

ハザードマップは「浸水します」を示します。災害危険区域は **「条例で建築が制限されます」** を示します。
家を買う・建てる前に効くのは後者ですが、住所で引ける民間の道具がありませんでした。

例（名古屋市港区）:

> 臨海部防災区域(第3種)／指定理由 高潮,出水／**1階床高N・P+1メートル**
> 根拠条例 名古屋市臨海部防災区域建築条例／告示 2007/10/1 名古屋市告示第362号

## データ

| 項目 | 値 |
| --- | --- |
| 出典 | 国土数値情報 災害危険区域（A48）国土交通省 |
| データ時点 | 2021年度（令和3年度）版・2021年7月時点 |
| 収録 | 21,067区域・588自治体（37都道府県） |
| 根拠法 | 建築基準法第39条 |
| 利用条件 | CC BY 4.0（一部制限）。**自治体ごとに条件が異なる** |

### 収録していない自治体（重要）

配布ページの `R6_Terms_of_Use_DisasterRiskArea.xlsx` で、商用利用不可・再配布不可・非公開と
定められている自治体は取り込みません。買い切り製品として売る以上、ここを通すと利用条件違反になります。
一覧は `app/codes.py` の `COMMERCIAL_USE_DENIED`（2026-09-11 時点で7自治体）。
条件は年度ごとに変わるので、データを更新するときは必ず一覧を取り直してください。

## 設計の芯

1. **「区域外」と「データなし」を必ず区別する。** 指定のない自治体で黙って「区域外」と答えない。
2. 判定には必ず根拠（根拠条例・告示番号）とデータ時点を添える。
3. 住所から求めた座標は町丁目の代表点。近くに区域があるときは距離を添えて言い切らない。
4. **「区域外」は安全という意味ではない。** 災害危険区域は建築制限のための指定で、浸水想定そのものではない。

## なぜ PostGIS ではなく SQLite か

区域は全国で21,067件しかありません（kflood の洪水は2,800万面あるので PostGIS が要ります）。
買い切りキットとして配る以上、docker も postgres も要らない方が導入が速い。判定は shapely の STRtree で足ります。

## 使い方

```bash
python3 -m venv .venv && .venv/bin/pip install fastapi "uvicorn[standard]" jinja2 shapely requests
.venv/bin/python scripts/load_a48.py          # 全国を取り込む（未指定の県は404で飛ばす）
.venv/bin/python scripts/load_a48.py 23 24    # 都道府県コード指定
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 18311
```

## API

```
GET /api/check?q=名古屋市港区港明1-12-20
GET /api/check?lat=35.1076&lon=136.8859
GET /healthz
```

`status` は `inside` / `outside` / `uncovered` の3値です。`uncovered` を `outside` と混ぜないでください。

## 関連

洪水・内水は [kflood](https://kurage.exbridge.jp/kflood.php/)、土砂は khazard、津波は ktsunami、
活断層は [kfault](https://kurage.exbridge.jp/kfault.php/)。
