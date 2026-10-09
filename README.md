# 西太平洋アンサンブル観測所

気象モデルのアンサンブル進路を、単純平均で一本化せず、**物理的に近い進路シナリオへ分解**し、tracker jumpなどの疑わしいノイズを別枠表示する実験的な可視化サイトです。

## ⚠️ 重要な注意

これは気象モデルのアンサンブルを観察して楽しむための**非公式・実験的な可視化**です。正確な予報や防災判断には、気象庁、JTWC、各国気象機関などの公式情報を確認してください。

This is an **unofficial, experimental visualization** made for exploring weather-model ensembles. For accurate forecasts and safety decisions, consult official information from JMA, JTWC, and the relevant national meteorological agencies.

## 現在の監視状態

- 個別監視: **Invest 95W（2026年10月）**
- 2026年10月9日JST、TWINの指示に基づき台風29号コグマ（94W / JTWC 27W / KOGUMA）の個別監視を終了し、別の擾乱95Wへ切替
- JTWC ABPW 10月9日19:30 JST: 13.5N・144.5E、推定13–18 kt、約1007 hPa、24時間以内の発達評価LOW
- 原文取得は10月9日20:42 JST。有効区間は10月9日19:30〜10日19:30 JST
- 95WのGEFS 2026100906は31メンバー取得・初期同一性照合後、正常10本・中心喪失による打切り21本で品質条件（正常20本以上）未達。解析は判定保留。定時更新周期と品質閾値は維持
- 旧対象の最終保存済み解析はGEFS 2026100506。既定画面では旧進路図・集計を隠し、明示的に履歴を開く操作でのみ表示。過去ラン・履歴・解析セッションを保持し、95Wの予測へ再ラベルしない

現行監視状態の正本は `monitor_status.json`、追跡設定は `tracking_config.json` です。
切替記録は `analysis/sessions/2026-10-09-95w-monitoring-started.json`、
公式原文と取得記録は `analysis/materials/2026-10-09-1030z-jtwc-abpw*` に保存しています。

`trackingTargetId: 2026-10-invest-95w` で対象世代を区別し、正式昇格後も同じ世代IDを保ちます。
旧94W / 27W / KOGUMAの名称・警報番号・予測中心を95Wへ継承しません。
監視解除は台風29号の消滅や影響終了を意味しません。

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

