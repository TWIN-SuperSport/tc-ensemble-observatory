# 西太平洋アンサンブル観測所

気象モデルのアンサンブル進路を、単純平均で一本化せず、**物理的に近い進路シナリオへ分解**し、tracker jumpなどの疑わしいノイズを別枠表示する実験的な可視化サイトです。

## ⚠️ 重要な注意

これは気象モデルのアンサンブルを観察して楽しむための**非公式・実験的な可視化**です。正確な予報や防災判断には、気象庁、JTWC、各国気象機関などの公式情報を確認してください。

This is an **unofficial, experimental visualization** made for exploring weather-model ensembles. For accurate forecasts and safety decisions, consult official information from JMA, JTWC, and the relevant national meteorological agencies.

## 現在の監視状態

- **2026年11月6日JSTから監視休止中**。台風32号への切替は行いません。
- `monitor_status.json` の `state: standby` が現行の休止制御です。GitHub Actions の定時処理は状態確認のみ行い、GEFS の新規取得・解析をスキップします。
- 保存済みの台風31号インファ（JTWC30W / WP302026）の解析と、旧対象の全履歴を閲覧用に保持します。データの再ラベルや削除は行いません。
- `tracking_config.json` は最後の対象の解析設定として保持します。自動取得の実行可否は上記の休止制御で判断します。
- 休止は台風の消滅や日本への影響がないことを意味しません。最新予報・防災判断には気象庁などの公式情報を確認してください。
- 再開時は対象と公式位置を改めて確認して設定します。

直前の対象切替記録は `analysis/sessions/2026-10-26-31-monitoring-started.json`、公式原文と SHA-256 は `analysis/materials/2026-10-25-31-switch-sources.json` に保持します。

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


### Track coverage and update health (2026-10-19)

The GRIB acquisition requirement remains all 31 members × 21 forecast times.
An enclosed pressure minimum leaving the analysis domain or becoming unresolved
now ends that member's track at its last valid point. That is **censoring**, not
a diagnosis of dissipation or extratropical transition. No missing tail is
extrapolated. Speed, pressure-jump, equator-crossing and initial-identity guards
are unchanged; genuine quality-flagged members stay excluded as NOISE.

At least 20 quality-screened prefixes are required. Clusters compare only a
common lead-time window: the latest lead with at least 20 valid members. The
UI displays that horizon and counts remaining at each lead. Cluster shares use
the original 31-member denominator; they are not landfall probabilities.
`cleanMembers` counts full-length tracks, `terminatedMembers` valid prefixes,
and `validMembers` their sum. This differs from old v2 payloads, where terminated
tracks were all classified as noise and fewer than 20 full tracks blocked the
entire update. Historical payloads remain unchanged.

`update_status.json` reports the most recent attempt separately from the last
published forecast initialization. Failed runs retain their previous forecast
and publish a failure status rather than relabeling old tracks as new. Detailed
raw errors remain in Actions diagnostics, not public status messages.
