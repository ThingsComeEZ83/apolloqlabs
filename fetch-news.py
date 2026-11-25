#!/usr/bin/env python3
"""Build data/news.json from official U.S. government RSS/Atom feeds.

Sources are U.S. Government works (17 U.S.C. §105). The SEC states that
information on sec.gov "may be copied or further distributed, without first
obtaining permission, for noncommercial or commercial purposes"
(https://www.sec.gov/privacy). We do not scrape HTML. Every item keeps a
link back to the official page. Attribution is shown in the app.
"""
from __future__ import annotations
import datetime as dt, email.utils, json, os, re, sys, urllib.request, xml.etree.ElementTree as ET

SCRIPT = os.path.abspath(__file__)
HERE = os.path.dirname(SCRIPT)
# Source tree: scripts/fetch-news.py -> data/news.json (and site/news.json).
# GitHub Pages repo: fetch-news.py next to news.json at the repo root.
if os.path.isdir(os.path.join(HERE, 'data')):
    ROOT = HERE
    DST = os.path.join(ROOT, 'data', 'news.json')
    SITE_DST = os.path.join(ROOT, 'site', 'news.json')
elif os.path.basename(HERE) == 'scripts' and os.path.isdir(os.path.join(os.path.dirname(HERE), 'data')):
    ROOT = os.path.dirname(HERE)
    DST = os.path.join(ROOT, 'data', 'news.json')
    SITE_DST = os.path.join(ROOT, 'site', 'news.json')
else:
    ROOT = HERE
    DST = os.path.join(ROOT, 'news.json')
    SITE_DST = None
CONTACT = os.environ.get('SEC_CONTACT') or 'admin@apolloqlabs.example'
UA = 'ApolloQLabs news %s' % CONTACT

# Generic current 8-K Atom titles look like "8-K - Firm (0001234567) (Filer)"
# and crowd out real market headlines. Company filings belong on the SEC
# filings card, not in Market News. BEA news releases are official U.S.
# Government works, same as the Fed / FDIC / BLS feeds already used.
FEEDS = [
    ('Federal Reserve', 'press releases',
     'https://www.federalreserve.gov/feeds/press_all.xml',
     'https://www.federalreserve.gov/newsevents/pressreleases.htm'),
    ('FDIC', 'press releases',
     'https://www.fdic.gov/rss.xml',
     'https://www.fdic.gov/news/'),
    ('BLS', 'economic releases',
     'https://www.bls.gov/feed/bls_latest.rss',
     'https://www.bls.gov/'),
    ('BEA', 'economic releases',
     'https://apps.bea.gov/rss/rss.xml',
     'https://www.bea.gov/news'),
]

def is_generic_filing(title, src=''):
    """True for raw EDGAR current-filing lines, e.g. '8-K - Acme (0001234567) (Filer)'."""
    t = title or ''
    if re.search(r'^\s*8-K(?:/A)?\b', t, re.I) and re.search(r'\(\s*\d{6,}\s*\)|\(\s*Filer\s*\)', t, re.I):
        return True
    if src == 'SEC EDGAR' and re.search(r'^\s*8-K(?:/A)?\b', t, re.I):
        return True
    return False

def get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def txt(el):
    if el is None:
        return ''
    return re.sub(r'\s+', ' ', ''.join(el.itertext())).strip()

def first(el, names):
    for n in names:
        got = el.find(n)
        if got is not None:
            return got
    return None

def iso(s):
    s = (s or '').strip()
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        pass
    try:
        d = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        return None

def parse(blob):
    root = ET.fromstring(blob)
    items = []
    # Atom
    for e in list(root.findall('{http://www.w3.org/2005/Atom}entry')) + list(root.findall('entry')):
        title = txt(first(e, ['{http://www.w3.org/2005/Atom}title', 'title']))
        link = first(e, ['{http://www.w3.org/2005/Atom}link', 'link'])
        href = ''
        if link is not None:
            href = (link.get('href') or txt(link)).strip()
        if not href:
            href = txt(first(e, ['{http://www.w3.org/2005/Atom}id', 'id']))
        when = iso(txt(first(e, [
            '{http://www.w3.org/2005/Atom}updated', 'updated',
            '{http://www.w3.org/2005/Atom}published', 'published',
        ])))
        items.append((title, href, when))
    # RSS
    for e in root.findall('.//item'):
        title = txt(first(e, ['title']))
        link = first(e, ['link', 'guid'])
        href = ''
        if link is not None:
            href = (link.get('href') or txt(link)).strip()
        when = iso(txt(first(e, ['pubDate', 'updated', '{http://purl.org/dc/elements/1.1/}date'])))
        items.append((title, href, when))
    return items

def main():
    out, errors = [], []
    seen = set()
    for src, kind, url, home in FEEDS:
        timeout = 45 if 'treasury' in url else 25
        try:
            rows = parse(get(url, timeout=timeout))
            n = 0
            for title, href, when in rows:
                title = re.sub(r'\s+', ' ', title or '').strip()
                href = (href or '').strip()
                if not title or not href.startswith('http'):
                    continue
                if is_generic_filing(title, src):
                    continue
                key = href.split('?')[0].rstrip('/')
                if key in seen:
                    continue
                seen.add(key)
                out.append({'t': title[:220], 'url': href, 'src': src, 'kind': kind, 'at': when, 'home': home})
                n += 1
            print('%s: %d items' % (src, n))
        except Exception as e:
            errors.append('%s: %s' % (src, e))
            print('FAIL', src, e, file=sys.stderr)
    out.sort(key=lambda x: x['at'] or '', reverse=True)
    # Keep the feed mixed so one agency (usually the Fed) does not fill the list.
    CAP = {'Federal Reserve': 12, 'BEA': 8, 'FDIC': 5, 'BLS': 6, 'SEC EDGAR': 0}
    picked, rest = [], []
    counts = {}
    for item in out:
        src = item.get('src') or ''
        cap = CAP.get(src)
        if cap is None or counts.get(src, 0) < cap:
            picked.append(item)
            counts[src] = counts.get(src, 0) + 1
        else:
            rest.append(item)
    out = (picked + rest)[:40]
    out.sort(key=lambda x: x['at'] or '', reverse=True)
    doc = {
        'built': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'items': out,
        'errors': errors,
        'credit': 'Official U.S. government public information. Federal Reserve, FDIC, BLS and BEA releases are U.S. Government works (17 U.S.C. §105). Each headline links to its official page.',
    }
    os.makedirs(os.path.dirname(DST) or '.', exist_ok=True)
    with open(DST, 'w') as f:
        json.dump(doc, f, indent=1)
    if SITE_DST:
        os.makedirs(os.path.dirname(SITE_DST), exist_ok=True)
        with open(SITE_DST, 'w') as f:
            json.dump(doc, f, indent=1)
    newest = next((x['at'] for x in out if x.get('at')), None)
    print('wrote', DST, len(out), 'headlines; newest', newest)

if __name__ == '__main__':
    main()
