#!/usr/bin/env python3
"""Publish non-sensitive update health independently of accepted forecast data."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

def make_status(root, outcome, now=None):
    def read(path):
        try:
            return json.loads((root / path).read_text())
        except (OSError, ValueError):
            return {}
    diagnostics = read('.diagnostics/gefs-analysis.json')
    latest = read('latest_run.json')
    success = outcome == 'success'
    if not success:
        latest = read('.diagnostics/published-latest.json') or latest
    return {
        'lastAttemptAt': now or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'init': diagnostics.get('init'),
        'publishedInit': latest.get('init'),
        'status': 'success' if success else 'failed',
        'failureStage': None if success else ('analysis' if diagnostics.get('downloadedFields') == 651 else 'acquisition_or_validation'),
        'downloadedFields': diagnostics.get('downloadedFields'),
        'message': '更新処理完了。予測の初期時刻と追跡有効本数を確認してください。' if success else '最新更新の取得・解析・検証が完了していません。表示中の予測初期時刻は据え置きです。',
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--outcome', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    (root / 'update_status.json').write_text(json.dumps(make_status(root,args.outcome), ensure_ascii=False,indent=2)+'\n')
if __name__ == '__main__':
    main()
