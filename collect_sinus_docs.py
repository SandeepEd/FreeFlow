#!/usr/bin/env python3
"""
Higher-precision sinusitis/rhinosinusitis document collector.

Improvements over the previous version:
- English-only filtering
- stronger relevance scoring
- basic PDF text validation
- rejects thin / low-signal pages
- rejects likely navigation/index/search pages
- cleaner keep/reject reasons
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import sys
import time
from collections import deque, defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup
from lxml import etree
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# -------------------------
# Config
# -------------------------

BASE_DIR = Path(".")
RAW_HTML_DIR = BASE_DIR / "data" / "raw" / "html"
RAW_PDF_DIR = BASE_DIR / "data" / "raw" / "pdfs"
MANIFEST_PATH = BASE_DIR / "data" / "manifest.jsonl"
LOG_PATH = BASE_DIR / "data" / "collector.log"

ALLOWED_DOMAINS = {
    "cdc.gov",
    "www.cdc.gov",
    "medlineplus.gov",
    "www.medlineplus.gov",
    "entnet.org",
    "www.entnet.org",
}

SEED_URLS = [
    "https://www.cdc.gov/sinus-infection/about/index.html",
    "https://medlineplus.gov/sinusitis.html",
    "https://www.entnet.org/quality-practice/quality-products/clinical-practice-guidelines/cpg-adult-sinusitis/",
]

# Stronger keywords for relevance
PRIMARY_KEYWORDS = [
    "sinusitis",
    "rhinosinusitis",
    "sinus infection",
    "acute bacterial rhinosinusitis",
    "chronic sinusitis",
    "abrs",
]

SECONDARY_KEYWORDS = [
    "sinus",
    "nasal congestion",
    "facial pain",
    "facial pressure",
    "postnasal drip",
    "runny nose",
    "blocked nose",
    "stuffy nose",
    "antibiotics",
    "watchful waiting",
    "saline",
    "nasal spray",
]

# URL patterns that often indicate non-English or undesired content
REJECT_URL_PATTERNS = [
    "/es/",
    "/spanish/",
    "/espanol/",
    "/español/",
    "/fr/",
    "/french/",
    "/zh/",
    "/chinese/",
    "/arabic/",
    "/russian/",
    "/vietnamese/",
    "?lang=es",
    "&lang=es",
    "language=spanish",
]

REQUEST_TIMEOUT = (8, 20)
REQUEST_DELAY_SECONDS = 1.0
MAX_SITEMAP_URLS_PER_DOMAIN = 1500
MAX_SEED_CRAWL_DEPTH = 2
MAX_HTML_PAGES_TO_CRAWL = 120
MAX_TOTAL_CANDIDATES = 800
MAX_PDF_PAGES_TO_PREVIEW = 4

USER_AGENT = "SinusRAGCollector/0.2 (+educational project; respectful crawler; local-dev)"

TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "cmpid", "WT.mc_id"
}

MIN_HTML_TEXT_LEN = 500
MIN_PDF_TEXT_LEN = 300

ENGLISH_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "your", "about",
    "have", "you", "are", "not", "can", "care", "medical", "treatment",
    "infection", "sinus", "symptoms", "doctor", "health", "when", "what",
    "more", "will", "should", "than", "after", "people", "pain", "nose",
    "pressure", "nasal", "chronic", "acute"
}

SPANISH_STOPWORDS = {
    "que", "para", "con", "por", "una", "las", "los", "del", "como", "más",
    "salud", "dolor", "nariz", "usted", "cuando", "sinusitis", "infección",
    "tratamiento", "médico", "puede", "debe", "sobre", "después", "porque"
}

BAD_PAGE_HINTS = [
    "search results",
    "site search",
    "find a doctor",
    "find a provider",
    "results for",
    "no results found",
]

LOW_SIGNAL_PATH_HINTS = [
    "/search",
    "/find",
    "/tag/",
    "/tags/",
    "/category/",
    "/categories/",
    "/archive/",
    "/archives/",
]


# -------------------------
# Data classes
# -------------------------

@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    headers: dict
    content: bytes
    content_type: str  # "html" | "pdf" | "other"
    error: Optional[str] = None


# -------------------------
# Setup / utils
# -------------------------

def setup_paths() -> None:
    for p in [RAW_HTML_DIR, RAW_PDF_DIR, MANIFEST_PATH.parent]:
        p.mkdir(parents=True, exist_ok=True)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
        ],
    )


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(
        total=3,
        read=3,
        connect=3,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def throttle():
    time.sleep(REQUEST_DELAY_SECONDS)


def get_site_root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def is_allowed_domain(url: str) -> bool:
    return urlparse(url).netloc.lower() in ALLOWED_DOMAINS


def normalize_url(url: str) -> str:
    p = urlparse(url)
    scheme = p.scheme.lower() or "https"
    netloc = p.netloc.lower()

    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    path = re.sub(r"/{2,}", "/", p.path or "/")
    if not path:
        path = "/"

    query_pairs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if k not in TRACKING_QUERY_KEYS]
    query = urlencode(query_pairs, doseq=True)

    return urlunparse((scheme, netloc, path, "", query, ""))


def looks_like_pdf_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sanitize_filename(s: str, max_len: int = 120) -> str:
    s = re.sub(r"[^\w\-\.]+", "_", s)
    return s[:max_len].strip("_") or "file"


def content_type_from_headers(headers: dict, url: str) -> str:
    ct = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    if "application/pdf" in ct:
        return "pdf"
    if "text/html" in ct or "application/xhtml+xml" in ct:
        return "html"
    if looks_like_pdf_url(url):
        return "pdf"
    return "other"


def reject_url_for_language(url: str) -> bool:
    u = url.lower()
    return any(pattern in u for pattern in REJECT_URL_PATTERNS)


def count_keyword_hits(text: str, keywords: list[str]) -> int:
    t = (text or "").lower()
    hits = 0
    for kw in keywords:
        if kw in t:
            hits += 1
    return hits


def score_relevance(url: str, title: str, text: str) -> int:
    score = 0
    u = url.lower()
    title_l = (title or "").lower()
    text_l = (text or "").lower()

    score += count_keyword_hits(u, PRIMARY_KEYWORDS) * 5
    score += count_keyword_hits(u, SECONDARY_KEYWORDS) * 1

    score += count_keyword_hits(title_l, PRIMARY_KEYWORDS) * 6
    score += count_keyword_hits(title_l, SECONDARY_KEYWORDS) * 2

    preview = text_l[:12000]
    score += count_keyword_hits(preview, PRIMARY_KEYWORDS) * 4
    score += count_keyword_hits(preview, SECONDARY_KEYWORDS) * 1

    return score


def likely_low_signal_page(url: str, title: str, text: str) -> bool:
    u = url.lower()
    title_l = (title or "").lower()
    text_l = (text or "").lower()

    if any(h in u for h in LOW_SIGNAL_PATH_HINTS):
        return True

    for hint in BAD_PAGE_HINTS:
        if hint in title_l or hint in text_l[:4000]:
            return True

    return False


def english_score(text: str) -> tuple[int, int]:
    tokens = re.findall(r"[a-zA-ZÀ-ÿ']+", (text or "").lower())
    if not tokens:
        return 0, 0
    eng = sum(1 for t in tokens if t in ENGLISH_STOPWORDS)
    spa = sum(1 for t in tokens if t in SPANISH_STOPWORDS)
    return eng, spa


def is_likely_english_text(text: str) -> bool:
    eng, spa = english_score(text[:6000])
    # Conservative: accept ties only if English has some signal
    if eng == 0 and spa == 0:
        return True
    return eng >= spa


# -------------------------
# robots.txt / sitemap discovery
# -------------------------

def fetch_text(session: requests.Session, url: str) -> Optional[str]:
    try:
        throttle()
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code >= 400:
            logging.warning("GET %s -> %s", url, r.status_code)
            return None
        return r.text
    except Exception as e:
        logging.warning("GET %s failed: %s", url, e)
        return None


def fetch_bytes(session: requests.Session, url: str) -> Optional[bytes]:
    try:
        throttle()
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code >= 400:
            logging.warning("GET %s -> %s", url, r.status_code)
            return None
        return r.content
    except Exception as e:
        logging.warning("GET %s failed: %s", url, e)
        return None


def robots_txt_url(site_root: str) -> str:
    return site_root.rstrip("/") + "/robots.txt"


def extract_sitemaps_from_robots(robots_text: str) -> list[str]:
    sitemaps = []
    for line in robots_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("sitemap:"):
            _, val = line.split(":", 1)
            val = val.strip()
            if val.startswith("http://") or val.startswith("https://"):
                sitemaps.append(val)
    return list(dict.fromkeys(sitemaps))


def default_sitemap_guesses(site_root: str) -> list[str]:
    base = site_root.rstrip("/")
    return [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap-index.xml",
        f"{base}/sitemap/sitemap.xml",
    ]


def decompress_if_needed(data: bytes, url: str) -> bytes:
    if url.lower().endswith(".gz"):
        try:
            return gzip.decompress(data)
        except Exception:
            return data
    return data


def _localname(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def parse_sitemap_xml(xml_bytes: bytes) -> tuple[str, list[str]]:
    try:
        root = etree.fromstring(xml_bytes)
    except Exception as e:
        logging.warning("Failed parsing sitemap XML: %s", e)
        return "unknown", []

    root_name = _localname(root.tag)
    locs = []

    if root_name == "urlset":
        for elem in root.iter():
            if _localname(elem.tag) == "loc" and elem.text:
                locs.append(elem.text.strip())
        return "urlset", locs

    if root_name == "sitemapindex":
        for elem in root.iter():
            if _localname(elem.tag) == "loc" and elem.text:
                locs.append(elem.text.strip())
        return "sitemapindex", locs

    return "unknown", []


def discover_sitemaps_for_root(session: requests.Session, site_root: str) -> list[str]:
    txt = fetch_text(session, robots_txt_url(site_root))
    sitemap_urls = []
    if txt:
        sitemap_urls.extend(extract_sitemaps_from_robots(txt))

    if not sitemap_urls:
        for guess in default_sitemap_guesses(site_root):
            b = fetch_bytes(session, guess)
            if not b:
                continue
            kind, _ = parse_sitemap_xml(decompress_if_needed(b, guess))
            if kind in {"urlset", "sitemapindex"}:
                sitemap_urls.append(guess)

    sitemap_urls = list(dict.fromkeys(sitemap_urls))
    logging.info("Discovered %d sitemap(s) for %s", len(sitemap_urls), site_root)
    return sitemap_urls


def expand_sitemap(session: requests.Session, sitemap_url: str, seen: set[str], max_urls: int) -> set[str]:
    if sitemap_url in seen:
        return set()
    seen.add(sitemap_url)

    out_urls = set()
    b = fetch_bytes(session, sitemap_url)
    if not b:
        return out_urls

    xml_bytes = decompress_if_needed(b, sitemap_url)
    kind, locs = parse_sitemap_xml(xml_bytes)

    if kind == "urlset":
        for u in locs:
            if len(out_urls) >= max_urls:
                break
            out_urls.add(normalize_url(u))
        return out_urls

    if kind == "sitemapindex":
        for child in locs:
            if len(out_urls) >= max_urls:
                break
            out_urls |= expand_sitemap(session, child, seen, max_urls=max_urls - len(out_urls))
        return out_urls

    return out_urls


def discover_candidates_from_sitemaps(session: requests.Session, seed_urls: list[str]) -> set[str]:
    roots = sorted({get_site_root(u) for u in seed_urls})
    all_candidates = set()

    for root in roots:
        if not is_allowed_domain(root):
            continue

        sitemap_urls = discover_sitemaps_for_root(session, root)
        seen_sitemaps = set()
        page_urls = set()

        for sm in sitemap_urls:
            page_urls |= expand_sitemap(session, sm, seen_sitemaps, MAX_SITEMAP_URLS_PER_DOMAIN)
            if len(page_urls) >= MAX_SITEMAP_URLS_PER_DOMAIN:
                break

        logging.info("Sitemaps yielded %d URLs for %s", len(page_urls), root)

        for u in page_urls:
            if not is_allowed_domain(u):
                continue
            if reject_url_for_language(u):
                continue
            if url_keyword_match(u) or looks_like_pdf_url(u):
                all_candidates.add(u)

    logging.info("Total candidates from sitemaps (prefiltered): %d", len(all_candidates))
    return all_candidates


# -------------------------
# Relevance helpers
# -------------------------

def url_keyword_match(url: str) -> bool:
    u = url.lower()
    return any(k in u for k in (PRIMARY_KEYWORDS + SECONDARY_KEYWORDS))


def text_keyword_match(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in (PRIMARY_KEYWORDS + SECONDARY_KEYWORDS))


# -------------------------
# Seed-page bounded crawl
# -------------------------

def fetch_html_for_discovery(session: requests.Session, url: str) -> Optional[str]:
    try:
        throttle()
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code >= 400:
            return None
        ctype = content_type_from_headers(r.headers, url)
        if ctype != "html":
            return None
        return r.text
    except Exception as e:
        logging.warning("Discovery fetch failed %s: %s", url, e)
        return None


def extract_links_from_html(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not href:
            continue
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url, href)
        if full.startswith(("http://", "https://")):
            links.append(normalize_url(full))
    return list(dict.fromkeys(links))


def bounded_seed_crawl(session: requests.Session, seed_urls: list[str]) -> set[str]:
    visited = set()
    discovered = set()
    q = deque()

    for u in seed_urls:
        nu = normalize_url(u)
        if is_allowed_domain(nu) and not reject_url_for_language(nu):
            q.append((nu, 0))

    html_pages_seen = 0

    while q and html_pages_seen < MAX_HTML_PAGES_TO_CRAWL and len(discovered) < MAX_TOTAL_CANDIDATES:
        url, depth = q.popleft()

        if url in visited:
            continue
        visited.add(url)

        if not is_allowed_domain(url) or reject_url_for_language(url):
            continue

        if looks_like_pdf_url(url):
            if url_keyword_match(url):
                discovered.add(url)
            continue

        html = fetch_html_for_discovery(session, url)
        if not html:
            continue

        html_pages_seen += 1
        soup = BeautifulSoup(html, "lxml")
        text_snippet = " ".join(soup.stripped_strings)[:8000]

        if text_keyword_match(text_snippet) and is_likely_english_text(text_snippet):
            discovered.add(url)

        links = extract_links_from_html(html, url)
        for link in links:
            if not is_allowed_domain(link) or reject_url_for_language(link):
                continue

            if looks_like_pdf_url(link):
                if url_keyword_match(link) or text_keyword_match(text_snippet):
                    discovered.add(link)
                continue

            if depth + 1 <= MAX_SEED_CRAWL_DEPTH:
                if url_keyword_match(link) or (depth == 0 and text_keyword_match(text_snippet)):
                    q.append((link, depth + 1))

    logging.info("Seed crawl discovered %d candidates | html pages crawled=%d", len(discovered), html_pages_seen)
    return discovered


# -------------------------
# Fetch + store
# -------------------------

def fetch_url(session: requests.Session, url: str) -> FetchResult:
    try:
        throttle()
        r = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        ctype = content_type_from_headers(r.headers, str(r.url))
        return FetchResult(
            url=url,
            final_url=str(r.url),
            status_code=r.status_code,
            headers=dict(r.headers),
            content=r.content if r.status_code < 400 else b"",
            content_type=ctype,
            error=None if r.status_code < 400 else f"HTTP {r.status_code}",
        )
    except Exception as e:
        return FetchResult(
            url=url,
            final_url=url,
            status_code=0,
            headers={},
            content=b"",
            content_type="other",
            error=str(e),
        )


def make_output_path(final_url: str, content: bytes, content_type: str) -> Path:
    digest = sha256_bytes(content)[:16]
    parsed = urlparse(final_url)
    stem = sanitize_filename(parsed.netloc + "_" + Path(parsed.path).stem)
    if content_type == "pdf":
        return RAW_PDF_DIR / f"{stem}_{digest}.pdf"
    return RAW_HTML_DIR / f"{stem}_{digest}.html"


def save_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# -------------------------
# HTML / PDF quality checks
# -------------------------

def extract_html_title_text_lang(html_bytes: bytes) -> tuple[str, str, str]:
    try:
        soup = BeautifulSoup(html_bytes, "lxml")

        html_tag = soup.find("html")
        lang = ""
        if html_tag and html_tag.has_attr("lang"):
            lang = str(html_tag.get("lang", "")).lower()

        title = soup.title.get_text(" ", strip=True) if soup.title else ""

        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        text = " ".join(soup.stripped_strings)
        return title, text, lang
    except Exception:
        return "", "", ""


def extract_pdf_preview_text(pdf_bytes: bytes, max_pages: int = MAX_PDF_PAGES_TO_PREVIEW) -> tuple[str, int]:
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        parts = []
        page_count = len(reader.pages)
        for i, page in enumerate(reader.pages[:max_pages]):
            try:
                txt = page.extract_text() or ""
                if txt:
                    parts.append(txt)
            except Exception:
                continue
        return "\n".join(parts).strip(), page_count
    except Exception:
        return "", 0


def content_language_header_is_english(headers: dict) -> bool:
    cl = (headers.get("Content-Language") or headers.get("content-language") or "").lower()
    if not cl:
        return True
    return cl.startswith("en")


def html_is_english(fr: FetchResult, lang_attr: str, title: str, text: str) -> bool:
    if not content_language_header_is_english(fr.headers):
        return False
    if lang_attr and not lang_attr.startswith("en"):
        return False
    return is_likely_english_text(f"{title}\n{text[:5000]}")


def pdf_is_english(fr: FetchResult, preview_text: str) -> bool:
    if not content_language_header_is_english(fr.headers):
        return False
    return is_likely_english_text(preview_text)


def is_relevant_fetch_result(fr: FetchResult) -> tuple[bool, str]:
    if fr.status_code >= 400 or fr.error:
        return False, f"fetch_error:{fr.error or fr.status_code}"

    if not is_allowed_domain(fr.final_url):
        return False, "domain_not_allowed"

    if reject_url_for_language(fr.final_url):
        return False, "url_non_english_hint"

    if fr.content_type == "html":
        title, text, lang_attr = extract_html_title_text_lang(fr.content)

        if len(text) < MIN_HTML_TEXT_LEN:
            return False, "html_too_thin"

        if not html_is_english(fr, lang_attr, title, text):
            return False, "html_not_english"

        if likely_low_signal_page(fr.final_url, title, text):
            return False, "html_low_signal_page"

        score = score_relevance(fr.final_url, title, text)
        if score < 10:
            return False, f"html_low_relevance_score:{score}"

        return True, f"html_keep_score:{score}"

    if fr.content_type == "pdf":
        preview_text, page_count = extract_pdf_preview_text(fr.content)

        if len(preview_text) < MIN_PDF_TEXT_LEN:
            return False, "pdf_no_readable_text"

        if not pdf_is_english(fr, preview_text):
            return False, "pdf_not_english"

        score = score_relevance(fr.final_url, "", preview_text)
        if score < 8:
            return False, f"pdf_low_relevance_score:{score}"

        return True, f"pdf_keep_score:{score}_pages:{page_count}"

    return False, "unsupported_content_type"


# -------------------------
# Manifest / run
# -------------------------

def append_manifest(record: dict) -> None:
    with MANIFEST_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def collect():
    setup_paths()
    setup_logging()

    session = build_session()

    logging.info("Starting discovery...")
    sitemap_candidates = discover_candidates_from_sitemaps(session, SEED_URLS)
    crawl_candidates = bounded_seed_crawl(session, SEED_URLS)

    candidates = list(dict.fromkeys(
        normalize_url(u) for u in (list(sitemap_candidates) + list(crawl_candidates))
    ))

    candidates = [u for u in candidates if is_allowed_domain(u)]
    candidates = [u for u in candidates if not reject_url_for_language(u)]
    candidates = [u for u in candidates if (url_keyword_match(u) or looks_like_pdf_url(u))]
    candidates = candidates[:MAX_TOTAL_CANDIDATES]

    logging.info("Total candidate URLs to fetch: %d", len(candidates))

    seen_urls = set()
    seen_hashes = set()
    stats = defaultdict(int)

    for i, url in enumerate(candidates, start=1):
        url = normalize_url(url)
        if url in seen_urls:
            stats["skip_seen_url"] += 1
            continue
        seen_urls.add(url)

        logging.info("[%d/%d] Fetching %s", i, len(candidates), url)
        fr = fetch_url(session, url)

        rec = {
            "url": fr.url,
            "final_url": fr.final_url,
            "status_code": fr.status_code,
            "content_type": fr.content_type,
            "error": fr.error,
        }

        if fr.error or fr.status_code >= 400:
            stats["fetch_failed"] += 1
            rec["decision"] = "reject"
            rec["reason"] = fr.error or f"http_{fr.status_code}"
            append_manifest(rec)
            continue

        content_hash = sha256_bytes(fr.content)
        rec["content_sha256"] = content_hash

        if content_hash in seen_hashes:
            stats["duplicate_content"] += 1
            rec["decision"] = "reject"
            rec["reason"] = "duplicate_content_hash"
            append_manifest(rec)
            continue
        seen_hashes.add(content_hash)

        keep, reason = is_relevant_fetch_result(fr)
        rec["relevance_reason"] = reason

        if not keep:
            stats["not_relevant"] += 1
            rec["decision"] = "reject"
            rec["reason"] = reason
            append_manifest(rec)
            continue

        out_path = make_output_path(fr.final_url, fr.content, fr.content_type)
        save_bytes(out_path, fr.content)

        rec["decision"] = "keep"
        rec["saved_path"] = str(out_path)
        append_manifest(rec)

        if fr.content_type == "html":
            stats["saved_html"] += 1
        elif fr.content_type == "pdf":
            stats["saved_pdf"] += 1
        else:
            stats["saved_other"] += 1

    logging.info("Done.")
    logging.info("Summary: %s", dict(stats))

    print("\n=== Collection Summary ===")
    for k in sorted(stats.keys()):
        print(f"{k}: {stats[k]}")
    print(f"manifest: {MANIFEST_PATH}")
    print(f"html dir : {RAW_HTML_DIR}")
    print(f"pdf dir  : {RAW_PDF_DIR}")


if __name__ == "__main__":
    collect()