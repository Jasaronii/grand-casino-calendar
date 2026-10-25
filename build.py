"""Generate a subscribed iCalendar feed from Grand Casino Arena's public calendar."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE = 'https://www.grandcasinoarena.com/events'
TZ = ZoneInfo('America/Chicago')
OUT = Path('events.ics')


def esc(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')


def fold(line):
    b = line.encode('utf-8')
    result = []
    while len(b) > 73:
        n = 73
        while n and (b[n] & 0xC0) == 0x80:
            n -= 1
        result.append(b[:n].decode('utf-8'))
        b = b[n:]
    result.append(b.decode('utf-8'))
    return '\r\n '.join(result)


def collect_jsonld(soup):
    events = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
            elif isinstance(obj, dict):
                stack.extend(v for k, v in obj.items() if k in ('@graph', 'itemListElement', 'event', 'events'))
                typ = obj.get('@type', [])
                if isinstance(typ, str):
                    typ = [typ]
                if any(t.endswith('Event') for t in typ) and obj.get('startDate'):
                    events.append(obj)
    return events


def normalize(obj):
    start_raw = obj.get('startDate')
    if not start_raw:
        return None
    try:
        start = datetime.fromisoformat(str(start_raw).replace('Z', '+00:00'))
        if start.tzinfo is None:
            start = start.replace(tzinfo=TZ)
        start = start.astimezone(TZ)
        end_raw = obj.get('endDate')
        end = datetime.fromisoformat(str(end_raw).replace('Z', '+00:00')) if end_raw else start + timedelta(hours=3)
        if end.tzinfo is None:
            end = end.replace(tzinfo=TZ)
        end = end.astimezone(TZ)
    except ValueError:
        return None
    name = obj.get('name', '').strip()
    if not name or start < datetime.now(TZ) - timedelta(days=1):
        return None
    url = obj.get('url') or BASE
    if isinstance(url, dict):
        url = url.get('@id') or BASE
    desc = BeautifulSoup(str(obj.get('description') or ''), 'html.parser').get_text(' ', strip=True)
    return (name, start, max(end, start + timedelta(minutes=1)), str(url), desc)


def main():
    events = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent='Mozilla/5.0 (compatible; GrandCasinoCalendar/1.0)')
        page.goto(BASE, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(1800)
        # Load the full list if the site offers a Load More button.
        for _ in range(35):
            button = page.get_by_text('Load More', exact=False)
            if not button.count() or not button.first.is_visible():
                break
            button.first.click(timeout=10000)
            page.wait_for_timeout(600)
        soup = BeautifulSoup(page.content(), 'html.parser')
        links = set()
        for a in soup.select('a[href]'):
            href = a.get('href', '')
            if '/event/' in href or '/events/detail/' in href:
                from urllib.parse import urljoin
                links.add(urljoin(BASE, href))
        # Prefer structured Event data: dates and times are not inferred from titles.
        for obj in collect_jsonld(soup):
            e = normalize(obj)
            if e:
                events[(e[0], e[1].isoformat())] = e
        for url in sorted(links):
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=20000)
                detail = BeautifulSoup(page.content(), 'html.parser')
                for obj in collect_jsonld(detail):
                    e = normalize(obj)
                    if e:
                        events[(e[0], e[1].isoformat())] = e
            except Exception as ex:
                print('Skipped', url, str(ex)[:100])
        browser.close()
    if not events:
        raise RuntimeError('No structured events found. Refusing to overwrite existing feed. Website selectors may need updating.')
    now = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Grand Casino Arena Community Calendar//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'X-WR-CALNAME:Grand Casino Arena - All Events', 'X-WR-TIMEZONE:America/Chicago', 'REFRESH-INTERVAL;VALUE=DURATION:PT6H']
    for name, start, end, url, desc in sorted(events.values(), key=lambda x: x[1]):
        uid = hashlib.sha256((name + start.isoformat()).encode()).hexdigest()[:24] + '@grandcasinoarena-community-calendar'
        lines += ['BEGIN:VEVENT', f'UID:{uid}', f'DTSTAMP:{now}', f'DTSTART:{start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}', f'DTEND:{end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}', f'SUMMARY:{esc(name)}', 'LOCATION:Grand Casino Arena\\, 199 W Kellogg Blvd\\, Saint Paul\\, MN 55102', f'DESCRIPTION:{esc(desc + " | Event details: " + url)}', f'URL:{url}', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    OUT.write_bytes(('\r\n'.join(fold(x) for x in lines) + '\r\n').encode('utf-8'))
    print(f'Wrote {len(events)} events to {OUT}')


if __name__ == '__main__':
    main()
