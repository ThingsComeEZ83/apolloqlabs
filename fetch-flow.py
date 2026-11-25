#!/usr/bin/env python3
"""Build data/flow.json from Nasdaq's public delayed option-chain API.

Fetches full-chain 15-minute-delayed quotes (volume, open interest, bid, ask, last)
for the default ApolloQLabs watchlist with zero API keys required.

Writes data/flow.json (and site/flow.json when site/ exists). Designed to run both
at build time and on a 15-minute market-hours schedule (scripts/refresh-flow.yml).
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir(os.path.join(HERE, 'data')):
    ROOT = HERE
    DST = os.path.join(ROOT, 'data', 'flow.json')
    SITE_DST = os.path.join(ROOT, 'site', 'flow.json')
elif os.path.basename(HERE) == 'scripts' and os.path.isdir(os.path.join(os.path.dirname(HERE), 'data')):
    ROOT = os.path.dirname(HERE)
    DST = os.path.join(ROOT, 'data', 'flow.json')
    SITE_DST = os.path.join(ROOT, 'site', 'flow.json')
else:
    ROOT = HERE
    DST = os.path.join(ROOT, 'flow.json')
    SITE_DST = None
ET = ZoneInfo('America/New_York')
CT = ZoneInfo('America/Chicago')

WATCHLIST = ['SPY', 'QQQ', 'IWM', 'AAPL', 'NVDA', 'TSLA', 'MSFT', 'AMZN', 'META', 'GOOGL', 'AMD']
ETFS = {'SPY', 'QQQ', 'IWM'}
MIN_KEEP_VOL = 100
TOP_PREM_PER_TICKER = 100


def parse_num(v) -> float:
    if v is None or v == '--' or v == '':
        return 0.0
    try:
        return float(str(v).replace(',', ''))
    except (TypeError, ValueError):
        return 0.0


def parse_exp(group: str | None, url: str | None) -> str:
    if group:
        try:
            return dt.datetime.strptime(group.strip(), '%B %d, %Y').strftime('%Y-%m-%d')
        except ValueError:
            pass
    if url:
        m = re.search(r'--(\d{6})[cp]', url)
        if m:
            raw = m.group(1)
            return f'20{raw[:2]}-{raw[2:4]}-{raw[4:6]}'
    return ''


def parse_last_trade(raw: str | None) -> tuple[float | None, str, str]:
    """Return (spot_price, iso_utc_or_date, human_et_text) from Nasdaq's lastTrade string."""
    if not raw or not isinstance(raw, str):
        return None, '', ''
    spot = None
    mp = re.search(r'\$([0-9,]+(?:\.[0-9]+)?)', raw)
    if mp:
        spot = parse_num(mp.group(1)) or None
    mt = re.search(r'AS OF\s+([A-Z]{3}\s+\d{1,2},\s+\d{4}(?:\s+\d{1,2}:\d{2}\s+[AP]M\s+ET)?)', raw, re.I)
    if not mt:
        return spot, '', ''
    txt = ' '.join(mt.group(1).split())
    for fmt, has_time in (('%b %d, %Y %I:%M %p ET', True), ('%b %d, %Y', False)):
        try:
            parsed = dt.datetime.strptime(txt, fmt)
            if has_time:
                aware = parsed.replace(tzinfo=ET)
                return spot, aware.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), txt
            return spot, parsed.strftime('%Y-%m-%d'), txt
        except ValueError:
            pass
    return spot, '', txt


def fetch_symbol(sym: str) -> dict | None:
    ac_order = ['etf', 'stocks'] if sym in ETFS else ['stocks', 'etf']
    for ac in ac_order:
        url = (
            f'https://api.nasdaq.com/api/quote/{sym}/option-chain'
            f'?assetclass={ac}&limit=9999&money=all&fromdate=all&todate=undefined'
        )
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                j = json.loads(r.read().decode('utf-8'))
        except (urllib.error.URLError, TimeoutError, ValueError):
            continue
        data = j.get('data') or {}
        rows = (data.get('table') or {}).get('rows') or []
        if not rows:
            continue

        spot, as_of_iso, as_of_raw = parse_last_trade(data.get('lastTrade'))
        cur_exp = ''
        all_contracts = []
        totals = {'cv': 0, 'pv': 0, 'cp': 0.0, 'pp': 0.0}

        for row in rows:
            if row.get('expirygroup'):
                cur_exp = parse_exp(row.get('expirygroup'), '')
            if not row.get('strike'):
                continue
            exp = parse_exp('', row.get('drillDownURL')) or cur_exp
            k = round(parse_num(row.get('strike')), 4)
            if not exp or k <= 0:
                continue
            for side, cp in (('c_', 'C'), ('p_', 'P')):
                vol = int(parse_num(row.get(side + 'Volume')))
                if vol <= 0:
                    continue
                oi = int(parse_num(row.get(side + 'Openinterest')))
                bid = parse_num(row.get(side + 'Bid'))
                ask = parse_num(row.get(side + 'Ask'))
                last = parse_num(row.get(side + 'Last'))
                if bid > 0 and ask > 0:
                    px = round((bid + ask) / 2.0, 2)
                elif last > 0:
                    px = round(last, 2)
                elif ask > 0:
                    px = round(ask, 2)
                else:
                    px = 0.0
                prem = vol * px * 100.0
                if cp == 'C':
                    totals['cv'] += vol
                    totals['cp'] += prem
                else:
                    totals['pv'] += vol
                    totals['pp'] += prem
                all_contracts.append((exp, k, cp, vol, oi, px, prem))

        # Keep all potential unusual alerts (vol >= 100 and vol/OI >= 1 or OI == 0)
        # plus the top TOP_PREM_PER_TICKER contracts by estimated premium.
        top_by_prem = sorted(all_contracts, key=lambda c: c[6], reverse=True)[:TOP_PREM_PER_TICKER]
        keep_keys = {(c[0], c[1], c[2]) for c in top_by_prem}
        kept = []
        for c in all_contracts:
            exp, k, cp, vol, oi, px, _prem = c
            is_cand = vol >= MIN_KEEP_VOL and (oi == 0 or vol / oi >= 1.0)
            if is_cand or (exp, k, cp) in keep_keys:
                kept.append([exp, k, cp, vol, oi, px])

        totals['cp'] = round(totals['cp'], 0)
        totals['pp'] = round(totals['pp'], 0)
        return {
            'sym': sym,
            'assetClass': ac,
            'spot': spot,
            'asOf': as_of_iso,
            'asOfRaw': as_of_raw,
            'totals': totals,
            'activeCount': len(all_contracts),
            'kept': kept,
        }
    return None


def main() -> int:
    now_utc = dt.datetime.now(dt.timezone.utc)
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(fetch_symbol, WATCHLIST))

    tickers_meta = {}
    contracts = []
    sym_idx = {}
    ok_syms = []
    failed_syms = []

    for sym, res in zip(WATCHLIST, results):
        if not res:
            failed_syms.append(sym)
            continue
        idx = len(ok_syms)
        ok_syms.append(sym)
        sym_idx[sym] = idx
        tickers_meta[sym] = {
            'spot': res['spot'],
            'asOf': res['asOf'],
            'asOfRaw': res['asOfRaw'],
            'totals': res['totals'],
            'activeCount': res['activeCount'],
        }
        for c in res['kept']:
            # Compact tuple: [tickerIndex, expYYYYMMDD, strike, cp, vol, oi, px]
            contracts.append([idx] + c)

    if not ok_syms:
        print('fetch-flow.py: all watchlist fetches failed', file=sys.stderr)
        return 1

    # Latest intraday timestamp across tickers if present, else fetch time
    ts_candidates = [m['asOf'] for m in tickers_meta.values() if m['asOf'] and 'T' in m['asOf']]
    as_of_utc = max(ts_candidates) if ts_candidates else now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

    payload = {
        'source': 'Nasdaq option-chain API (15-min delayed)',
        'sourceUrl': 'https://www.nasdaq.com/market-activity/stocks',
        'delayMinutes': 15,
        'fetchedAt': now_utc.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'asOf': as_of_utc,
        'watchlist': ok_syms,
        'failed': failed_syms,
        'tickers': tickers_meta,
        # Contracts include all with vol >= 100 and vol/OI >= 1 (or OI == 0) plus top 100 by premium per ticker
        'contracts': contracts,
    }

    text = json.dumps(payload, separators=(',', ':'))
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    with open(DST, 'w', encoding='utf-8') as f:
        f.write(text)
    if SITE_DST and os.path.isdir(os.path.dirname(SITE_DST)):
        with open(SITE_DST, 'w', encoding='utf-8') as f:
            f.write(text)

    ct_str = now_utc.astimezone(CT).strftime('%b %d, %Y %-I:%M %p %Z')
    print(f'ok: {len(ok_syms)}/{len(WATCHLIST)} tickers, {len(contracts)} contracts, {len(text)} bytes -> {DST} ({ct_str})')
    if failed_syms:
        print('warn: failed:', ','.join(failed_syms), file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
