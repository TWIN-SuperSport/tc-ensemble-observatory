# 西太平洋アンサンブル観測所

気象モデルのアンサンブル進路を、単純平均で一本化せず、**物理的に近い進路シナリオへ分解**し、tracker jumpなどの疑わしいノイズを別枠表示する実験的な可視化サイトです。

## ⚠️ 重要な注意

これは気象モデルのアンサンブルを観察して楽しむための**非公式・実験的な可視化**です。正確な予報や防災判断には、気象庁、JTWC、各国気象機関などの公式情報を確認してください。

This is an **unofficial, experimental visualization** made for exploring weather-model ensembles. For accurate forecasts and safety decisions, consult official information from JMA, JTWC, and the relevant national meteorological agencies.

## 現在の監視状態

- 個別監視: **Invest 94W（2026年10月）**
- 2026年10月4日JST、TWINの承認に基づき台風27号チョーイワン（93W / JTWC 26W / CHOI-WAN）の個別監視を終了し、10月の94Wへ切替
- JTWC ABPW 10月3日22:00 JST: 8.3N・167.4E、推定18–23 kt、約1008 hPa、24時間以内の発達評価MEDIUM
- 公式原文は10月4日12:07 JST取得。有効期間は10月3日22:00〜4日15:00 JST。発表時点の評価と現在の実況を区別
- JTWCのモデル要約: 今後24時間はほぼ停滞しつつまとまる見通し（独自のGEFS解析結果ではない）
- GEFS自動取得対象を94Wへ変更し、解析実行時に最新のJTWC公式位置から追跡・検証
- 10月4日12:30 JST、GEFS 10月3日18Zの31メンバー取得・初期同一性検証に合格。27本を+240hまで追跡し、4本は中心喪失時点で打切り・別枠保存
- 初期中心は公式位置から約35〜315km、中央値123km、中央気圧1007.3hPa。閾値は緩めず、4シナリオ群を抽出
- [実データ検証run](https://github.com/TWIN-SuperSport/tc-ensemble-observatory/actions/runs/37174197376)は10月4日12:30 JSTに合格（初期値: 10月3日18Z）
- 過去のGEFSラン、`history/`、解析セッションは保存

現行監視状態の正本は `monitor_status.json`、追跡設定は `tracking_config.json` です。
検証結果は `analysis/sessions/2026-10-04-94w-gefs-validated.json`、
解析待ちだった切替時点の記録は `analysis/sessions/2026-10-04-94w-monitoring-started.json`、
公式原文と取得記録は `analysis/materials/2026-10-03-1300z-jtwc-abpw*` に保存しています。
検証実行時の公式再取得は10月4日12:29 JST。元の発表・初回取得記録は保持しています。
検証候補の公開先は `data.json`、`latest_run.json`、`history/2026100318.json` です。

**10月の94Wは8月の94Wとは別の擾乱です。** Invest番号は再利用されるため、
`trackingTargetId: 2026-10-invest-94w` で対象の世代を区別し、正式な昇格後も同じ世代IDを保ちます。
過去の94Wや93Wの対象ID・名称・警報番号・予測中心を今回の94Wへ流用しません。
過去の解析を削除せず、現在の監視対象と区別して参照できます。
監視解除は、台風の消滅や影響終了を意味しません。

### 海盆をまたぐInvestの扱い

Invest番号の末尾は海盆を表すため、日付変更線を越えても `92C` を
機械的に `92W` へ置換しません。自動解析はJTWC警報から対象IDの最新座標を
取得し、ABPW要約で照合します。前回GEFS解析の中央値へフォールバックできるのは、
その前回ラン自体が公式位置で検証済みの場合だけです。さらに、f000の全メンバーの
中心距離と気圧を検査し、公式位置から離れた背景低気圧を31本まとめて追う事故を
公開前に停止します。

過去のJSONは削除しません。現在の検証規則より前の保存済みランは、画面上で
「追跡ID未検証／要照合」と明示し、最新ランと同じ確度では扱いません。

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
  --storm CP92 \
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

## 追跡品質と再現検証（2026-10-04）

- NOMADS切出しとNOAA S3全球場は、復号後に同一領域・同一格子順へ正規化します
- 領域端、中心喪失、開いた気圧の谷では追跡を打ち切り、有効な前半だけをNOISEとして保存します。打切りは台風の消滅を意味しません
- シナリオ集計には+240hまで揃う正常メンバーだけを使います。正常20本以上、12時間25hPa、85km/hの品質条件は緩和していません
- 海面気圧の局所極小は閉じた循環や熱帯低気圧の証明ではありません。気圧急変は保守的な品質フラグであり、物理的急変と誤追跡を診断素材で区別します
- PR検証はread-only権限で実際の最新GEFSを解析し、公開・commit・Pages dispatchは行いません。既存の定時更新間隔は変更しません
- Actions artifactsに追跡点、符号付き気圧差、終了理由、取得元とSHA256を14日保存します。失敗runも旧公開データを置き換えず証拠を残します
- ローカル検証: `python scripts/run_gefs_analysis.py --self-test` と `python -m unittest discover -s scripts -p 'test_*.py'`
- 任意の再現用rawキャッシュは `GEFS_CACHE_DIR`、診断出力先は `GEFS_DIAGNOSTICS_PATH` で指定できます。`--force-init YYYYMMDDHH` は既存同一ランでも再解析します
