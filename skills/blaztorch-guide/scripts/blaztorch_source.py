"""Read only the data already delivered to visitors of blaztorch.com.

This is a deliberately narrow React Flight adapter, not a JavaScript evaluator.
An unfamiliar format is an error; callers can fall back to the visible website.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ORIGIN = 'https://blaztorch.com'
MAX_BODY = 12 * 1024 * 1024
TTL = 900


class SourceError(Exception):
    pass


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_url(url: str) -> None:
    u = urlsplit(url)
    if (u.scheme != 'https' or u.hostname != 'blaztorch.com' or u.port not in (None, 443)
            or u.username or u.password or u.path != '/resources'):
        raise SourceError('Only the public https://blaztorch.com/resources page is supported')


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active, self.parts = True, []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.scripts.append(''.join(self.parts))
            self.active = False


def flight_stream(html: str) -> bytes:
    parser = Scripts()
    parser.feed(html)
    chunks = []
    decoder = json.JSONDecoder()
    for script in parser.scripts:
        for match in re.finditer(r'self\.__next_f\.push\(', script):
            try:
                value, _ = decoder.raw_decode(script[match.end():])
            except (ValueError, TypeError) as exc:
                raise SourceError('The public page payload format changed') from exc
            if isinstance(value, list) and len(value) == 2 and value[0] == 1 and isinstance(value[1], str):
                chunks.append(value[1])
    if not chunks:
        raise SourceError('Public structured data unavailable; use the visible website')
    return ''.join(chunks).encode('utf-8')


def parse_props(html: str, component: str) -> dict:
    data = flight_stream(html)
    modules, models, texts = {}, {}, {}
    pos = 0
    while pos < len(data):
        if data[pos:pos + 1] == b':':
            # React resource hints have no row identifier and carry no item data.
            end = data.find(b'\n', pos)
            if end < 0:
                raise SourceError('Incomplete public resource hint')
            pos = end + 1
            continue
        match = re.match(rb'([0-9a-f]+):', data[pos:])
        if not match:
            raise SourceError('Unrecognized public data framing')
        key = match.group(1).decode('ascii')
        pos += match.end()
        if data[pos:pos + 1] == b'T':
            header = re.match(rb'T([0-9a-f]+),', data[pos:])
            if not header:
                raise SourceError('Invalid public text frame')
            size = int(header.group(1), 16)
            pos += header.end()
            if size > MAX_BODY or pos + size > len(data):
                raise SourceError('Incomplete public text frame')
            texts[key] = data[pos:pos + size].decode('utf-8')
            pos += size
            continue
        end = data.find(b'\n', pos)
        if end < 0:
            end = len(data)
        row = data[pos:end].decode('utf-8')
        pos = end + 1
        try:
            if row.startswith('I'):
                imported = json.loads(row[1:])
                if isinstance(imported, list) and len(imported) >= 3:
                    modules[key] = imported[2]
            elif row.startswith(('[', '{', '"')) or row == 'null':
                models[key] = json.loads(row)
        except ValueError as exc:
            raise SourceError('Invalid public structured data') from exc

    def resolve(value, seen=frozenset(), depth=0):
        if depth > 70:
            raise SourceError('Public data nesting limit exceeded')
        if isinstance(value, str):
            if value.startswith('$$'):
                return value[1:]
            ref = re.fullmatch(r'\$([0-9a-f]+)', value)
            if ref:
                key = ref.group(1)
                if key in seen:
                    raise SourceError('Cyclic public data reference')
                if key in texts:
                    return texts[key]
                if key in models:
                    return resolve(models[key], seen | {key}, depth + 1)
                raise SourceError('Unresolved public data reference')
            return value
        if isinstance(value, list):
            return [resolve(v, seen, depth + 1) for v in value]
        if isinstance(value, dict):
            return {k: resolve(v, seen, depth + 1) for k, v in value.items()}
        return value

    def find(value):
        if isinstance(value, list):
            if (len(value) >= 4 and value[0] == '$' and isinstance(value[1], str)
                    and value[1].startswith('$L') and modules.get(value[1][2:]) == component
                    and isinstance(value[3], dict)):
                return resolve(value[3])
            for child in value:
                found = find(child)
                if found is not None:
                    return found
        elif isinstance(value, dict):
            for child in value.values():
                found = find(child)
                if found is not None:
                    return found
        return None

    for model in models.values():
        result = find(model)
        if result is not None:
            items = result.get('items')
            if not isinstance(items, list) or any(not isinstance(i, dict) or not i.get('id') for i in items):
                raise SourceError('Public item schema changed')
            if len({i['id'] for i in items}) != len(items):
                raise SourceError('Duplicate identifiers in public page')
            if component == 'ResourceExplorer' and not items:
                raise SourceError('Resource catalog unexpectedly empty')
            return result
    raise SourceError(f'Public component {component} unavailable; use a browser to inspect the page')


class Client:
    def __init__(self, cache_dir: Path | None = None, refresh=False, offline=False):
        parent = Path(os.getenv('LOCALAPPDATA') or os.getenv('XDG_CACHE_HOME') or Path.home() / '.cache')
        self.cache_dir = cache_dir or parent / 'blaztorch-skill'
        self.refresh, self.offline = refresh, offline
        self.opener = build_opener(SameOriginRedirect())

    def load(self, url: str, component: str) -> tuple[dict, dict]:
        check_url(url)
        file = self.cache_dir / ('page-' + sha256(url.encode()).hexdigest() + '.json')
        cached = None
        try:
            if file.stat().st_size <= MAX_BODY:
                candidate = json.loads(file.read_text('utf-8'))
                if candidate['url'] == url and candidate['component'] == component:
                    cached = candidate
        except (OSError, ValueError, KeyError, TypeError):
            pass
        age = time.time() - cached['time'] if cached and isinstance(cached.get('time'), (int, float)) else float('inf')
        if self.offline or (cached and not self.refresh and 0 <= age < TTL):
            if not cached:
                raise SourceError('No cached public page available in offline mode')
            return cached['data'], {'url': url, 'retrievedAt': cached['retrievedAt'],
                                    'cache': True, 'offline': self.offline, 'ageSeconds': max(0, round(age))}
        request = Request(url, headers={'User-Agent': 'BlaztorchSkill/0.1 (+https://github.com/SanCalibur/blaztorch-skill)',
                                        'Accept': 'text/html', 'Accept-Encoding': 'identity'})
        try:
            with self.opener.open(request, timeout=25) as response:
                check_url(response.url)
                if 'text/html' not in response.headers.get('Content-Type', ''):
                    raise SourceError('Unexpected public page content type')
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise SourceError('Public page exceeds the download limit')
                parsed = parse_props(body.decode('utf-8'), component)
        except (HTTPError, URLError, TimeoutError, UnicodeError) as exc:
            raise SourceError('Could not read the public website; no stale data was silently substituted') from exc
        retrieved = stamp()
        entry = {'url': url, 'component': component, 'time': time.time(), 'retrievedAt': retrieved, 'data': parsed}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Only public pages are cached. No user capability profile is persisted.
        file.write_text(json.dumps(entry, ensure_ascii=False), 'utf-8')
        files = sorted(self.cache_dir.glob('page-*.json'), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[40:]:
            old.unlink()
        return parsed, {'url': url, 'retrievedAt': retrieved, 'cache': False, 'offline': False, 'ageSeconds': 0}

    def resources(self):
        return self.load(ORIGIN + '/resources', 'ResourceExplorer')

    def winners(self, query='', page=1, code=False):
        params = {'tab': 'winners', 'q': query, 'page': page}
        if code:
            params['code'] = '1'
        return self.load(ORIGIN + '/resources?' + urlencode(params), 'AwardLibrary')
