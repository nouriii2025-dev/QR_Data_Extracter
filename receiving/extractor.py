"""Fetch a page and pull out every label/value pair it exposes."""
import ipaddress
import json
import re
import queue
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; GRN-Scanner/1.0)"
MIN_FIELDS_BEFORE_RENDER = 4
NOISE_KEYS = re.compile(r"build.?id|runtime|asset.?prefix|css|script|nonce|csrf|token|favicon", re.I)


class ExtractError(Exception):
    pass


# ---------- safety ----------
_DNS_OK = {}            # hostname -> time it was last verified as public
_DNS_TTL = 600


def check_url(url: str) -> str:
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ExtractError("That scan is not a web address. Scan the QR code again.")
    if time.time() - _DNS_OK.get(p.hostname, 0) < _DNS_TTL:      # same host checked a moment ago
        return url
    try:
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ExtractError("Addresses on private networks are blocked.")
    except socket.gaierror:
        raise ExtractError("Could not find that website. Check the connection.")
    _DNS_OK[p.hostname] = time.time()
    return url


# ---------- helpers ----------
def _clean(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def _nice(key):
    key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(key)).replace("_", " ").replace("-", " ")
    return key.strip().title()


def _add(out, key, val):
    key, val = _clean(key).rstrip(":"), _clean(val)
    if not key or not val or len(key) > 80 or len(val) > 500 or NOISE_KEYS.search(key):
        return
    if key not in out:
        out[key] = val


def _flatten(obj, out, prefix="", depth=0):
    if depth > 5:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("@"):
                continue
            _flatten(v, out, f"{prefix} › {_nice(k)}" if prefix else _nice(k), depth + 1)
    elif isinstance(obj, list):
        if all(not isinstance(i, (dict, list)) for i in obj):
            _add(out, prefix, ", ".join(str(i) for i in obj if i not in (None, "")))
        else:
            for i, v in enumerate(obj[:30], 1):
                _flatten(v, out, f"{prefix} {i}", depth + 1)
    elif obj not in (None, ""):
        _add(out, prefix, obj)


# ---------- parsers ----------
def _from_scripts(soup, out):
    for s in soup.find_all("script"):
        t = (s.get("type") or "").lower()
        if t in ("application/ld+json", "application/json") or s.get("id") == "__NEXT_DATA__":
            try:
                _flatten(json.loads(s.string or ""), out)
            except (ValueError, TypeError):
                pass


def _from_tables(soup, out):
    for table in soup.find_all("table"):
        rows = [[_clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        if all(len(r) == 2 for r in rows):
            for k, v in rows:
                _add(out, k, v)
        elif len(rows) > 1 and len(rows[0]) > 1:
            head = rows[0]
            for n, row in enumerate(rows[1:], 1):
                for h, v in zip(head, row):
                    _add(out, h if n == 1 else f"{h} (row {n})", v)


def _from_definition_lists(soup, out):
    for dl in soup.find_all("dl"):
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                _add(out, dt.get_text(" "), dd.get_text(" "))


def _from_label_value_blocks(soup, out):
    for el in soup.find_all(class_=re.compile(r"label|key", re.I)):
        sib = el.find_next_sibling()
        if sib and len(sib.get_text(strip=True)) < 300:
            _add(out, el.get_text(" "), sib.get_text(" "))


def _from_meta(soup, out):
    for m in soup.find_all("meta"):
        name = m.get("property") or m.get("name") or ""
        if name.startswith(("og:", "product:")) or name in ("description", "author", "keywords"):
            if name not in ("og:image", "og:url"):
                _add(out, _nice(name.replace(":", " ")), m.get("content", ""))


def _from_text_lines(soup, out):
    for line in soup.get_text("\n").splitlines():
        m = re.match(r"^\s*([A-Za-z][\w /().-]{1,40}?)\s*:\s*(\S.{0,200})$", line)
        if m:
            _add(out, m.group(1), m.group(2))


# ---------- fetching ----------
_SESSION = requests.Session()                     # keeps the connection open between scans
_SESSION.headers.update({"User-Agent": UA})
NEEDS_JS = set()                                  # hosts that only show their data after JavaScript runs
_BLOCKED = {"image", "media", "font", "stylesheet"}


class _Renderer:
    """One headless Chromium that stays open, so a JavaScript page costs ~1 s instead of 5+ s."""

    def __init__(self):
        self.q = queue.Queue()
        self.ready = threading.Event()
        self.available = False
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True, name="chromium")
                self._thread.start()

    def _loop(self):
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = pw.chromium.launch(args=["--disable-gpu", "--disable-dev-shm-usage"])
            self.available = True
        except Exception:
            self.ready.set()
            return
        self.ready.set()
        while True:
            url, box = self.q.get()
            try:
                box["result"] = self._render(browser, url)
            except Exception as e:                # a bad page must not kill the browser thread
                box["error"] = e
            finally:
                box["done"].set()

    @staticmethod
    def _render(browser, url):
        ctx = browser.new_context(user_agent=UA)
        try:
            page = ctx.new_page()
            page.route("**/*", lambda r: r.abort() if r.request.resource_type in _BLOCKED else r.continue_())
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:                                  # stop as soon as the data is on screen
                page.wait_for_function("/serial/i.test(document.body.innerText)", timeout=8000)
                page.wait_for_function("/barg|bar\\s*\\(g\\)/i.test(document.body.innerText)", timeout=7000)
            except Exception:
                pass
            return page.content(), page.url
        finally:
            ctx.close()

    def render(self, url):
        self.start()
        self.ready.wait(60)
        if not self.available:
            return None
        box = {"done": threading.Event()}
        self.q.put((url, box))
        box["done"].wait(60)
        if "result" not in box:
            return None
        return box["result"]


_renderer = _Renderer()


def warm_up():
    """Start the browser in the background (called when the page opens) so the first scan is not slow."""
    _renderer.start()


try:
    import lxml  # noqa: F401
    _PARSER = "lxml"
except ImportError:
    _PARSER = "html.parser"


def _parse(html):
    soup = BeautifulSoup(html, _PARSER)
    out = {}
    _from_scripts(soup, out)
    for s in soup(["script", "style", "noscript"]):
        s.decompose()
    for fn in (_from_tables, _from_definition_lists, _from_label_value_blocks, _from_meta, _from_text_lines):
        fn(soup, out)
    lines = [_clean(l) for l in soup.get_text("\n").splitlines()]
    lines = [l for l in lines if l]
    title = ""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"]
    elif soup.title and soup.title.string:
        title = soup.title.string
    elif soup.h1:
        title = soup.h1.get_text(" ")
    return _clean(title), out, lines


def _has_barg(details, lines):
    return any(_UNIT_RE.search(str(v)) for v in details.values()) or any(_UNIT_RE.search(l) for l in lines)


def _complete(details, lines):
    """True only when the page text really gives a serial number AND a barg range."""
    return bool(pick_serial(details, "", lines)) and bool(pick_range(details, lines))


def extract(url: str) -> dict:
    url = check_url(url)
    host = urlparse(url).hostname
    title = final = ""
    details, lines, rendered = {}, [], False

    if host in NEEDS_JS:                          # learned earlier: skip the plain download
        res = _renderer.render(url)
        if res:
            html, final = res
            check_url(final)
            title, details, lines = _parse(html)
            rendered = True

    if not rendered:
        try:
            r = _SESSION.get(url, timeout=(4, 10))
            r.raise_for_status()
        except requests.RequestException as e:
            raise ExtractError(f"The website did not respond ({e.__class__.__name__}).")
        check_url(r.url)                          # re-check after redirects
        final = r.url
        title, details, lines = _parse(r.text)
        if not _complete(details, lines):         # data is probably built by JavaScript
            res = _renderer.render(r.url)
            if res:
                title2, details2, lines2 = _parse(res[0])
                check_url(res[1])
                if _complete(details2, lines2) or len(details2) + len(lines2) > len(details) + len(lines):
                    title, details, lines, rendered = title2 or title, details2, lines2, True
                    if _complete(details2, lines2):
                        NEEDS_JS.add(host)

    if not details and not lines:
        raise ExtractError(
            "No product details found on this page. If the page loads its content with JavaScript, "
            "install Playwright (see README) so the page can be rendered."
        )
    return {"url": final, "title": title, "details": details, "lines": lines, "count": len(details), "rendered": rendered}


# ---------- picking the two fields we care about ----------
_NUM = r"[-+]?\d+(?:[.,]\d+)?"
_UNIT = r"bar\s*\(?g\)?(?![a-z])"          # barg, bar(g), bar g
_RANGE_RE = re.compile(rf"({_NUM})\s*(?:\.{{2,}}|\u2026|\u2013|\u2014|\bto\b|(?<=\d)-(?=\d)|\s-\s)\s*({_NUM})\s*{_UNIT}", re.I)
_SINGLE_RE = re.compile(rf"({_NUM})\s*{_UNIT}", re.I)
_UNIT_RE = re.compile(_UNIT, re.I)
_RANGE_KEY = re.compile(r"range|scale|measur|span", re.I)
_SKIP_KEY = re.compile(r"over.?pressure|over.?load|burst|proof|test|operating|working|limit|max(?!.*range)", re.I)


def pick_serial(details: dict, url: str = "", lines=()) -> str:
    """Serial number from the page; falls back to the ?s= value of the QR link."""
    keys = [k for k in details if "serial" in k.lower() and str(details[k]).strip()]
    if keys:
        val = str(details[min(keys, key=len)])
        return re.sub(r"^\s*S\s*#\s*", "", val).strip()
    for i, line in enumerate(lines):          # label on one line, value on the next
        m = re.match(r"^serial\s*(number|no\.?)?\s*:?\s*(.*)$", line, re.I)
        if m:
            val = m.group(2).strip() or (lines[i + 1].strip() if i + 1 < len(lines) else "")
            if val and len(val) <= 40:
                return val
    q = parse_qs(urlparse(url).query).get("s")
    return q[0].strip() if q and q[0].strip() else ""


def _fmt(lo: str, hi: str) -> str:
    return f"{lo} to {hi} barg" if lo.startswith("-") else f"{lo}-{hi} barg"


def _range_from_text(text: str) -> str:
    text = text.replace("\u2212", "-")
    m = _RANGE_RE.search(text)
    if m:
        return _fmt(m.group(1), m.group(2))
    m = _SINGLE_RE.search(text)
    if m:                                   # single number before barg: the range runs from 0 (or up to 0)
        n = m.group(1)
        return f"{n} to 0 barg" if n.startswith("-") else f"0-{n} barg"
    return ""


_SKIP_LINE = re.compile(r"over.?pressure|over.?load|burst|proof|test", re.I)


def pick_range(details: dict, lines=()) -> str:
    """Number before 'barg' is the range top, e.g. '700 barg' -> '0-700 barg'. Empty if none found."""
    items = [(k, str(v)) for k, v in details.items() if not _SKIP_KEY.search(k)]
    range_keys = [kv for kv in items if _RANGE_KEY.search(kv[0])]
    other_keys = [kv for kv in items if not _RANGE_KEY.search(kv[0])]

    def from_items(group):
        for key, val in group:
            text = f"{val} barg" if _UNIT_RE.search(key) and not _UNIT_RE.search(val) else val
            found = _range_from_text(text)
            if found:
                return found
        return ""

    found = from_items(range_keys)
    if found:
        return found
    for line in lines:                       # free text such as the "Product Features" list
        if _UNIT_RE.search(line) and not _SKIP_LINE.search(line):
            found = _range_from_text(line)
            if found:
                return found
    return from_items(other_keys)
