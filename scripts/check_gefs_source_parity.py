#!/usr/bin/env python3
"""Compare real NOMADS and NOAA S3 PRMSL fields after common-domain normalization."""
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import build_gefs_data as pipeline


def main():
    config = json.loads(pipeline.CONFIG_PATH.read_text())
    latest = json.loads(pipeline.LATEST_PATH.read_text())
    init = datetime.strptime(latest['init'], '%Y%m%d%H').replace(tzinfo=timezone.utc)
    evidence = []
    for member, hour in [('c00', 0), ('c00', 240), ('p30', 0)]:
        nomads = pipeline.request(pipeline.filter_url(init, member, hour, config['domain']), retries=2, timeout=25)
        s3 = pipeline.s3_prmsl_blob(init, member, hour)
        a = pipeline.normalize_domain(*pipeline.decode_prmsl(nomads), config['domain'])
        b = pipeline.normalize_domain(*pipeline.decode_prmsl(s3), config['domain'])
        assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1]), 'Source coordinate mismatch'
        maximum_delta = float(np.max(np.abs(a[2] - b[2])))
        # NOMADS may re-pack a field; this is a numerical parity tolerance,
        # not a relaxation of the independent 25 hPa tracking quality rule.
        assert np.allclose(a[2], b[2], atol=0.05, rtol=0), f'Source pressure mismatch: {maximum_delta} hPa'
        evidence.append({'member': member, 'forecastHour': hour, 'gridPoints': len(a[2]), 'maximumPressureDifferenceHpa': maximum_delta})
    output = pipeline.ROOT / '.diagnostics' / 'source-parity.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'init': latest['init'], 'status': 'matched', 'fields': evidence}, indent=2) + '\n')
    print(output.read_text())


if __name__ == '__main__':
    main()
