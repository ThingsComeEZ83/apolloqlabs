#!/usr/bin/env python3
"""Build data/markets-strip.json for the Markets tab "market summary" strip.

Free source, no API key: Yahoo Finance v8 chart endpoint (the same one scripts/fetch-sectors.py already uses).
One request per symbol: range=1d, interval=5m. That gives the latest session's 5-minute bars (the sparkline),
the last price and the previous close, so the point and percent change are computed here, never invented.
Quotes are about 15 minutes delayed (futures and crypto may differ by venue). Each tile carries its own as-of time.
A symbol that fails is left out of the file; if too many fail the file is NOT replaced (exit 2).

Writes data/markets-strip.json (and site/markets-strip.json when site/ exists), or ./markets-strip.json when run
from the Pages repo root by scripts/refresh-markets.yml.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir(os.path.join(HERE, 'data')):
    ROOT = HERE
    DST, SITE_DST = os.path.join(ROOT, 'data', 'markets-strip.json'), os.path.join(ROOT, 'site', 'markets-strip.json')
elif os.path.basename(HERE) == 'scripts' and os.path.isdir(os.path.join(os.path.dirname(HERE), 'data')):
    ROOT = os.path.dirname(HERE)
    DST, SITE_DST = os.path.join(ROOT, 'data', 'markets-strip.json'), os.path.join(ROOT, 'site', 'markets-strip.json')
else:
    ROOT = HERE
    DST, SITE_DST = os.path.join(ROOT, 'markets-strip.json'), None

UA = {'User-Agent': 'Mozilla/5.0 (compatible; ApolloQLabs market strip; +https://thingscomeez83.github.io/apolloqlabs/)', 'Accept': 'application/json'}
MAX_SPARK = 72

# kind: idx = index points, yld = yield in percent, fx = currency pair, px = futures price in dollars, cry = crypto dollars, stk = stock
# 4th field = the app ticker a tile click sets (an ETF for an index, the stock itself for a stock)
GROUPS = [
    ('us', 'US Markets', [
        ('^GSPC', 'S&P 500', 'idx', 'SPY'), ('^DJI', 'Dow 30', 'idx', 'DIA'), ('^IXIC', 'Nasdaq', 'idx', 'QQQ'),
        ('^NDX', 'Nasdaq 100', 'idx', 'QQQ'),
        ('^RUT', 'Russell 2000', 'idx', 'IWM'), ('^TNX', '10-Yr Bond', 'yld', None), ('^VIX', 'VIX', 'idx', None)]),
    ('stocks', 'Popular Stocks', [
        ('AAPL', 'Apple', 'stk', 'AAPL'), ('MSFT', 'Microsoft', 'stk', 'MSFT'), ('NVDA', 'Nvidia', 'stk', 'NVDA'),
        ('AMZN', 'Amazon', 'stk', 'AMZN'), ('GOOGL', 'Alphabet', 'stk', 'GOOGL'), ('META', 'Meta', 'stk', 'META'),
        ('TSLA', 'Tesla', 'stk', 'TSLA'), ('AVGO', 'Broadcom', 'stk', 'AVGO'), ('AMD', 'AMD', 'stk', 'AMD'),
        ('NFLX', 'Netflix', 'stk', 'NFLX'), ('JPM', 'JPMorgan Chase', 'stk', 'JPM'), ('V', 'Visa', 'stk', 'V'),
        ('UNH', 'UnitedHealth', 'stk', 'UNH'), ('WMT', 'Walmart', 'stk', 'WMT'), ('XOM', 'Exxon Mobil', 'stk', 'XOM')]),
    ('eu', 'Europe', [
        ('^FTSE', 'FTSE 100', 'idx', None), ('^GDAXI', 'DAX', 'idx', None), ('^FCHI', 'CAC 40', 'idx', None),
        ('^STOXX50E', 'Euro Stoxx 50', 'idx', None), ('^IBEX', 'IBEX 35', 'idx', None)]),
    ('asia', 'Asia', [
        ('^N225', 'Nikkei 225', 'idx', None), ('^HSI', 'Hang Seng', 'idx', None), ('000001.SS', 'Shanghai', 'idx', None),
        ('^KS11', 'KOSPI', 'idx', None), ('^AXJO', 'ASX 200', 'idx', None)]),
    ('rates', 'Rates & Dollar', [
        ('^IRX', '13-Wk Bill', 'yld', None), ('^FVX', '5-Yr Yield', 'yld', None), ('^TNX', '10-Yr Bond', 'yld', None),
        ('^TYX', '30-Yr Bond', 'yld', None), ('DX-Y.NYB', 'US Dollar Index', 'idx', None), ('EURUSD=X', 'EUR/USD', 'fx', None)]),
    ('cmd', 'Commodities', [
        ('GC=F', 'Gold', 'px', None), ('SI=F', 'Silver', 'px', None), ('CL=F', 'Crude Oil (WTI)', 'px', None),
        ('BZ=F', 'Brent Crude', 'px', None), ('NG=F', 'Natural Gas', 'px', None), ('HG=F', 'Copper', 'px', None)]),
    ('crypto', 'Crypto', [
        ('BTC-USD', 'Bitcoin', 'cry', None), ('ETH-USD', 'Ethereum', 'cry', None), ('SOL-USD', 'Solana', 'cry', None),
        ('XRP-USD', 'XRP', 'cry', None)]),
]
REQUIRED_US = 5  # the US group must have at least this many tiles or the file is not replaced


def get_json(url: str, tries: int = 3):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return json.loads(r.read().decode('utf-8', 'replace'))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(str(last))


def fetch_one(sym: str):
    u = 'https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(sym) + '?range=1d&interval=5m'
    res = get_json(u)['chart']['result'][0]
    meta = res.get('meta') or {}
    ts = res.get('timestamp') or []
    cl = ((res.get('indicators') or {}).get('quote') or [{}])[0].get('close') or []
    pts = [(int(t), float(c)) for t, c in zip(ts, cl) if c is not None and c > 0]
    px = meta.get('regularMarketPrice')
    t_px = meta.get('regularMarketTime')
    prev = meta.get('chartPreviousClose') or meta.get('previousClose')
    if px is None and pts:
        px, t_px = pts[-1][1], pts[-1][0]
    if px is None or not prev or prev <= 0 or not t_px:
        raise RuntimeError('no price or previous close')
    px, prev = float(px), float(prev)
    if len(pts) < 2:
        raise RuntimeError('no intraday bars')
    series = [c for _, c in pts]
    if len(series) > MAX_SPARK:  # thin evenly, keep the last bar
        step = (len(series) - 1) / (MAX_SPARK - 1)
        series = [series[int(round(i * step))] for i in range(MAX_SPARK)]
    dig = 4 if px < 10 and meta.get('instrumentType') == 'CURRENCY' else 2
    return {
        'price': round(px, dig), 'prev': round(prev, dig), 'change': round(px - prev, dig),
        'pct': round((px / prev - 1) * 100, 2),
        'asOf': dt.datetime.fromtimestamp(int(t_px), dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'spark': [round(v, dig) for v in series],
        'sparkFrom': dt.datetime.fromtimestamp(pts[0][0], dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'tz': meta.get('exchangeTimezoneName') or '',
    }


def main() -> int:
    syms = sorted({s for _, _, items in GROUPS for s, *_ in items})
    out, bad = {}, {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for sym, fut in [(s, ex.submit(fetch_one, s)) for s in syms]:
            try:
                out[sym] = fut.result()
            except Exception as e:  # noqa: BLE001
                bad[sym] = str(e)
    groups = []
    for gid, label, items in GROUPS:
        tiles = []
        for sym, name, kind, tick in items:
            q = out.get(sym)
            if not q:
                continue
            t = {'sym': sym, 'name': name, 'kind': kind}
            if tick:
                t['ticker'] = tick
            t.update(q)
            tiles.append(t)
        if tiles:
            groups.append({'id': gid, 'label': label, 'items': tiles})
    us = next((g for g in groups if g['id'] == 'us'), None)
    if not us or len(us['items']) < REQUIRED_US:
        print('markets-strip: too few US tiles; leaving the existing file alone. Failed: ' + json.dumps(bad), file=sys.stderr)
        return 2
    now = dt.datetime.now(dt.timezone.utc)
    doc = {
        'built': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source': 'Yahoo Finance chart data (free, no key). Quotes are delayed about 15 minutes; futures and crypto follow their own venues.',
        'groups': groups,
    }
    text = json.dumps(doc, separators=(',', ':'))
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    with open(DST, 'w', encoding='utf-8') as f:
        f.write(text)
    if SITE_DST and os.path.isdir(os.path.dirname(SITE_DST)):
        with open(SITE_DST, 'w', encoding='utf-8') as f:
            f.write(text)
    n = sum(len(g['items']) for g in groups)
    print(f'ok: {n} tiles in {len(groups)} groups, {len(text)} bytes -> {DST}' + (f' (skipped: {", ".join(sorted(bad))})' if bad else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
