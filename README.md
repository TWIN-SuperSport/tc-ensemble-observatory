# 西太平洋アンサンブル観測所

気象モデルのアンサンブル進路を、単純平均で一本化せず、**物理的に近い進路シナリオへ分解**し、tracker jumpなどの疑わしいノイズを別枠表示する実験的な可視化サイトです。

## ⚠️ 重要な注意

これは気象モデルのアンサンブルを観察して楽しむための**非公式・実験的な可視化**です。正確な予報や防災判断には、気象庁、JTWC、各国気象機関などの公式情報を確認してください。

This is an **unofficial, experimental visualization** made for exploring weather-model ensembles. For accurate forecasts and safety decisions, consult official information from JMA, JTWC, and the relevant national meteorological agencies.

## 現在の監視状態

- JTWC ABPW: 90Wは消散し監視対象から除外
- 西太平洋にほかの疑わしい領域なし（NO OTHER SUSPECT AREAS）
- TCFAなし / JTWC警報なし
- 90W個別監視を終了し、西太平洋全域の新規Invest監視モードへ移行
- 90W監視時の進路データと解析セッションは履歴として保存

## 実装済み機能

- Raw spaghetti
- Clustered scenarios
- Noise / tracker jump diagnostics
- クラスター比率と代表進路
- メンバー個別診断
- 予報時間スライダー
- モバイル対応
- AI解析室（結論・検討経緯・未確定事項・使用素材）
- 現在は合成デモデータを表示

## データ更新

サイト本体はリポジトリ直下の `data.json` を読み込みます。

```bash
python tools/scripts/build_scenario_site.py tracks.csv \
  --site-dir . \
  --init 2026071518 \
  --storm WP90 \
  --model GEFS
```

入力CSVは少なくとも次の列を持ちます。

```text
member,fhour,lat,lon
```

任意列:

```text
mslp_hpa,vmax_kt
```

## AI解析室

`analysis.html` は、人間向けダッシュボードへ全資料を詰め込まず、AIが複数ラン・複数モデル・スクリーンショットを比較して言語化するための別室です。

ページ上部から順に以下を表示します。

1. 解析の結論
2. 結論に至った経緯
3. 未確定事項と反証条件
4. 使用素材の証拠カード

解析セッション一覧は `analysis/index.json`、各セッション本体は `analysis/sessions/*.json` に保存します。過去セッションを上書きせず、新しいJSONを追加してindexの `latest` を更新します。

素材画像はセッションJSONから相対パスで参照できます。画像だけでなく、対応する解析JSONや元データも `data` フィールドへ登録してください。

```json
{
  "id": "A-01",
  "title": "ECMWF 500hPa",
  "role": "リッジ再建の確認",
  "image": "../materials/ecmwf-500-f180.png",
  "data": "../materials/ecmwf-500-f180.json",
  "model": "ECMWF",
  "validTime": "2026-07-31T00:00:00Z",
  "tags": ["500hPa", "ridge-rebuild"]
}
```

この構造により、同じvalid timeを予測した複数ランの比較、特定タグが初めて現れた時刻の検索、過去の分岐判断と実況の照合をGit履歴込みで行えます。

## ローカル確認

```bash
python -m http.server 8000
```

ブラウザで以下を開きます。

- 観測所: `http://localhost:8000/`
- AI解析室: `http://localhost:8000/analysis.html`

---

GitHub Pages deployment re-triggered after Pages was enabled.
