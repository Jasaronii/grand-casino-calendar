"""Build an unofficial subscribed calendar from Grand Casino Arena event listings."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE = 'https://www.grandcasinoarena.com/events'
TZ = ZoneInfo('America/Chicago')
OUTPUT = Path('events.ics')
MONTHS = r'January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec'
DATE_RE = re.compile(rf'\b(?:{MONTHS})\s+\d{{1,2}},?\s+20\d{{2}}\b', re.I)
TIME_RE = re.compile(r'\b\d{1,2}:\d{2}\s*(?:AM|PM)\b', re.I)


def escape(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')


def fold(line):
    raw = line.encode('utf-8')
    parts = []
    while len(raw) > 73:
        end = 73
        while end and (raw[end] & 0xc0) == 0x80:
            end -= 1
        parts.append(raw[:end].decode('utf-8'))
        raw = raw[end:]
    parts.append(raw.decode('utf-8'))
    return '\r\n '.join(parts)


def jsonld_events(soup):
    found = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            stack = [json.loads(tag.string or tag.get_text())]
        except (ValueError, TypeError):
            continue
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                typ = item.get('@type', [])
                if isinstance(typ, str):
                    typ = [typ]
                if any(isinstance(t, str) and t.endswith('Event') for t in typ) and item.get('startDate'):
                    found.append(item)
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
    return found


def make_event(name, start, url, end=None, desc=''):
    if start.tzinfo is None:
        start = start.replace(tzinfo=TZ)
    start = start.astimezone(TZ)
    if start < datetime.now(TZ) - timedelta(days=1):
        return None
    if end is None:
        end = start + timedelta(hours=3)  # Estimated duration, not an official end time
    if end.tzinfo is None:
        end = end.replace(tzinfo=TZ)
    if end <= start:
        end = start + timedelta(hours=3)
    return {'name': name, 'start': start, 'end': end, 'url': url, 'desc': desc}


def parse_structured(item, fallback_url):
    try:
        start = datetime.fromisoformat(str(item['startDate']).replace('Z', '+00:00'))
        end = datetime.fromisoformat(str(item['endDate']).replace('Z', '+00:00')) if item.get('endDate') else None
        name = str(item.get('name') or '').strip()
        if not name:
            return None
        url = item.get('url') or fallback_url
        if isinstance(url, dict):
            url = url.get('@id') or fallback_url
        desc = BeautifulSoup(str(item.get('description') or ''), 'html.parser').get_text(' ', strip=True)
        return make_event(name, start, str(url), end, desc)
    except (ValueError, TypeError, KeyError):
        return None


def parse_visible(soup, url):
    """Extract Date and Event Starts fields, and separately listed showtimes."""
    title = soup.select_one('h1')
    if not title:
        return []
    name = title.get_text(' ', strip=True)
    lines = [x.strip() for x in soup.stripped_strings if x.strip()]
    # Limit extraction to content before the common venue footer.
    for idx, line in enumerate(lines):
        if line == 'Getting Here':
            lines = lines[:idx]
            break
    dates = []
    times = []
    for idx, line in enumerate(lines):
        if line.lower() == 'date':
            dates.extend(DATE_RE.findall(' '.join(lines[idx+1:idx+4])))
        elif line.lower() in ('event starts', 'event start', 'show starts'):
            times.extend(TIME_RE.findall(' '.join(lines[idx+1:idx+4])))
    # If showtimes are listed separately under Tickets, use each explicitly dated performance.
    pairs = []
    if 'Tickets' in lines:
        ticket_start = lines.index('Tickets') + 1
        ticket_end = next((i for i in range(ticket_start, len(lines)) if lines[i] in ('Helpful Info', 'More Info', 'Event Info')), len(lines))
        ticket_text = ' '.join(lines[ticket_start:ticket_end])
        # Ticket lines can use weekday + month + day, without a year.
        full = re.findall(rf'\b(?:{MONTHS})\s+\d{{1,2}},?\s+20\d{{2}}\b[^.]*?\b\d{{1,2}}:\d{{2}}\s*(?:AM|PM)\b', ticket_text, re.I)
        for fragment in full:
            dm, tm = DATE_RE.search(fragment), TIME_RE.search(fragment)
            if dm and tm:
                pairs.append((dm.group(), tm.group()))
    if not pairs and dates and times:
        pairs = [(dates[0], t) for t in dict.fromkeys(times)]
    results = []
    for date_text, time_text in pairs:
        try:
            date_text = re.sub(r'(?<=\d),(?=\s*20\d{2})', '', date_text)
            dt = None
            for fmt in ('%B %d %Y %I:%M %p', '%b %d %Y %I:%M %p'):
                try:
                    dt = datetime.strptime(f'{date_text} {time_text.upper().replace(" ", "")}', fmt.replace('%I:%M %p', '%I:%M%p'))
                    break
                except ValueError:
                    pass
            if dt:
                ev = make_event(name, dt, url)
                if ev:
                    results.append(ev)
        except ValueError:
            continue
    return results


def main():
    events = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36')
        page.goto(BASE, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(1500)
        links = set()
        for _ in range(50):
            soup = BeautifulSoup(page.content(), 'html.parser')
            for a in soup.select('a[href]'):
                url = urljoin(BASE, a['href'])
                if urlparse(url).netloc == 'www.grandcasinoarena.com' and '/events/detail/' in urlparse(url).path:
                    links.add(url)
            button = page.get_by_text('Load More', exact=False)
            if not button.count() or not button.first.is_visible():
                break
            try:
                button.first.click(timeout=10000)
                page.wait_for_timeout(900)
            except Exception:
                break
        print(f'Found {len(links)} event detail links')
        for i, url in enumerate(sorted(links), 1):
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=30000)
                soup = BeautifulSoup(page.content(), 'html.parser')
                found = [e for obj in jsonld_events(soup) if (e := parse_structured(obj, url))]
                if not found:
                    found = parse_visible(soup, url)
                for event in found:
                    key = (event['url'], event['start'].isoformat())
                    events[key] = event
                if not found:
                    print(f'No dated performance found: {url}')
            except Exception as exc:
                print(f'Skipped {url}: {exc}')
        browser.close()
    if not events:
        raise RuntimeError('No events extracted; preserving existing feed. Inspect detail pages and access restrictions.')
    now = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Grand Casino Arena Community Calendar//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'X-WR-CALNAME:Grand Casino Arena - All Events', 'X-WR-TIMEZONE:America/Chicago', 'REFRESH-INTERVAL;VALUE=DURATION:PT6H']
    for event in sorted(events.values(), key=lambda e: e['start']):
        uid = hashlib.sha256((event['url'] + '|' + event['start'].isoformat()).encode()).hexdigest()[:24] + '@grandcasinoarena-community-calendar'
        lines += ['BEGIN:VEVENT', f'UID:{uid}', f'DTSTAMP:{now}', f'DTSTART:{event["start"].astimezone(timezone.utc):%Y%m%dT%H%M%SZ}', f'DTEND:{event["end"].astimezone(timezone.utc):%Y%m%dT%H%M%SZ}', f'SUMMARY:{escape(event["name"])}', 'LOCATION:Grand Casino Arena\\, 199 W Kellogg Blvd\\, Saint Paul\\, MN 55102', f'DESCRIPTION:{escape("Event details: " + event["url"] + " | End time estimated when not published")}', f'URL:{event["url"]}', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    OUTPUT.write_bytes(('\r\n'.join(fold(line) for line in lines) + '\r\n').encode())
    print(f'Wrote {len(events)} calendar events to {OUTPUT}')


if __name__ == '__main__':
    main()
