
#!/usr/bin/env python3
"""
Site Mirror: crawl a domain, download all locally linked pages and assets,
and recreate the directory structure on disk.

Usage:
  python site_mirror.py https://example.com ./mirror \
      --include-subdomains \
      --max-pages 0 \
      --delay 0.3 \
      --workers 10 \
      --ignore-robots

      python site_scraper.py https://example.com ./example --workers 10 --delay 0.2 --ignore-robots

Notes:
- "Locally linked" means URLs on the same domain as the start URL.
  Use --include-subdomains to also follow links on subdomains.
- By default we respect robots.txt. Pass --ignore-robots to skip that check.
- Query strings are preserved by appending a short hash to filenames to avoid collisions.
- CSS files are scanned for url(...) references and those assets are downloaded too.
- This script does NOT rewrite HTML to point at local files; it simply mirrors paths.
  (Absolute links will still point to the live site.)

Dependencies:
  pip install requests beautifulsoup4
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup
import urllib.robotparser as robotparser


# --------------- Configuration & Types ---------------

ASSET_ATTRS = {
    ("img", "src"),
    ("img", "srcset"),
    ("script", "src"),
    ("link", "href"),
    ("source", "src"),
    ("video", "src"),
    ("audio", "src"),
    ("track", "src"),
    ("iframe", "src"),
    ("embed", "src"),
    ("object", "data"),
}

# Tags/rel combinations that usually indicate stylesheets or icons
LINK_RELS_AS_ASSETS = {"stylesheet", "icon", "shortcut icon", "apple-touch-icon", "preload", "prefetch"}

# Rough content-type checks
HTML_CT = ("text/html", "application/xhtml+xml")
CSS_CT = ("text/css",)


@dataclass(frozen=True)
class CrawlTask:
    url: str


class SiteMirror:
    def __init__(
        self,
        start_url: str,
        out_dir: Path,
        include_subdomains: bool = False,
        max_pages: int = 0,
        delay: float = 0.2,
        workers: int = 8,
        ignore_robots: bool = False,
        timeout: float = 20.0,
        user_agent: str = "SiteMirrorBot/1.0",
    ):
        self.start_url = self._normalize_url(start_url)
        self.base = urlparse(self.start_url)
        if self.base.scheme not in ("http", "https"):
            raise ValueError("Start URL must be http or https")

        self.out_dir = out_dir
        self.include_subdomains = include_subdomains
        self.max_pages = max_pages  # 0 means unlimited
        self.delay = delay
        self.workers = max(1, workers)
        self.ignore_robots = ignore_robots
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.lock = threading.Lock()

        # tracking
        self.seen_urls: Set[str] = set()         # everything we've scheduled (pages + assets)
        self.saved_urls: Set[str] = set()        # everything we've saved
        self.page_count = 0

        # robots
        self.rp: Optional[robotparser.RobotFileParser] = None
        if not self.ignore_robots:
            self._init_robots()

        # work queue
        self.q: "queue.Queue[CrawlTask]" = queue.Queue()

        # Make output directory
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # --------------- Utility ---------------

    @staticmethod
    def _normalize_url(u: str) -> str:
        # remove fragments; strip whitespace
        u = u.strip()
        u, _ = urldefrag(u)
        return u

    @staticmethod
    def _is_http(u: str) -> bool:
        p = urlparse(u)
        return p.scheme in ("http", "https")

    def _same_domain(self, u: str) -> bool:
        p = urlparse(u)
        if p.netloc == self.base.netloc:
            return True
        if self.include_subdomains:
            # include *.example.com when base is example.com
            return p.hostname and self.base.hostname and p.hostname.endswith("." + self.base.hostname)
        return False

    def _init_robots(self) -> None:
        robots_url = urljoin(f"{self.base.scheme}://{self.base.netloc}", "/robots.txt")
        rp = robotparser.RobotFileParser()
        try:
            rp.set_url(robots_url)
            rp.read()
            self.rp = rp
        except Exception:
            # If robots cannot be read, default to allowing
            self.rp = None

    def _allowed_by_robots(self, url: str) -> bool:
        if self.ignore_robots or not self.rp:
            return True
        try:
            return self.rp.can_fetch(self.session.headers.get("User-Agent", "*"), url)
        except Exception:
            return True

    # --------------- File path mapping ---------------

    @staticmethod
    def _hash8(s: str) -> str:
        return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:8]

    def url_to_local_path(self, url: str, content_type: Optional[str]) -> Path:
        """
        Map a URL to a local filesystem path under out_dir.

        - Preserves path from URL.
        - For paths ending with '/' or no filename, write 'index.html' (if HTML) or 'index' (if other/unknown).
        - Appends short hash of query string to avoid collisions: foo.js?v=1 -> foo__q=abcd1234.js
        """
        p = urlparse(url)
        path = p.path

        # Ensure we have some path
        if not path or path.endswith("/"):
            # Choose index.{ext}
            if content_type and content_type.startswith(HTML_CT):
                filename = "index.html"
            else:
                filename = "index"
            local = self.out_dir / p.netloc / path.lstrip("/") / filename
        else:
            local = self.out_dir / p.netloc / path.lstrip("/")

        # add query hash to filename if query present
        if p.query:
            local_parent = local.parent
            stem = local.stem or "file"
            suffix = "".join(local.suffixes) or ""
            hashed = self._hash8(p.query)
            local = local_parent / f"{stem}__q={hashed}{suffix}"

        # If no extension and content looks HTML, add .html
        if not local.suffix and content_type and any(content_type.startswith(ct) for ct in HTML_CT):
            local = local.with_suffix(".html")

        return local

    # --------------- Parsing helpers ---------------

    CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(?!data:)([^)'\"]+)\1\s*\)", re.IGNORECASE)

    def extract_links_and_assets(self, base_url: str, html: str) -> Tuple[Set[str], Set[str]]:
        """Return (page_urls, asset_urls) discovered in the HTML."""
        soup = BeautifulSoup(html, "html.parser")

        pages: Set[str] = set()
        assets: Set[str] = set()

        # All <a href> for potential pages
        for a in soup.find_all("a", href=True):
            href = a.get("href")
            if not href:
                continue
            u = urljoin(base_url, href)
            u = self._normalize_url(u)
            if not self._is_http(u):
                continue
            if self._same_domain(u):
                pages.add(u)

        # Assets from common tags
        for tag, attr in ASSET_ATTRS:
            for el in soup.find_all(tag):
                val = el.get(attr)
                if not val:
                    continue

                # Handle srcset which can be comma-separated with "url width" pairs
                if attr == "srcset":
                    candidates = []
                    for part in val.split(","):
                        u = part.strip().split(" ")[0]
                        if u:
                            candidates.append(u)
                else:
                    candidates = [val]

                for candidate in candidates:
                    u = urljoin(base_url, candidate)
                    u = self._normalize_url(u)
                    if not self._is_http(u):
                        continue

                    if tag == "link":
                        rel = " ".join((el.get("rel") or [])).lower()
                        if rel and rel not in LINK_RELS_AS_ASSETS and not candidate.lower().endswith((".css", ".ico", ".png", ".svg", ".webmanifest")):
                            # Most non-asset <link> relations (eg alternate) aren't assets to download
                            continue

                    if self._same_domain(u):
                        assets.add(u)

        return pages, assets

    def extract_css_urls(self, base_url: str, css_text: str) -> Set[str]:
        urls = set()
        for m in self.CSS_URL_RE.finditer(css_text):
            u = urljoin(base_url, m.group(2).strip())
            u = self._normalize_url(u)
            if self._is_http(u) and self._same_domain(u):
                urls.add(u)
        return urls

    # --------------- Networking & saving ---------------

    def fetch(self, url: str) -> Optional[requests.Response]:
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            return resp
        except requests.RequestException as e:
            print(f"[ERR] Request failed: {url} ({e})")
            return None

    def save_response(self, url: str, resp: requests.Response) -> Optional[Path]:
        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        local_path = self.url_to_local_path(url, content_type)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(local_path, "wb") as f:
                f.write(resp.content)
            return local_path
        except OSError as e:
            print(f"[ERR] Saving failed: {local_path} ({e})")
            return None

    # --------------- Crawl loop ---------------

    def enqueue(self, url: str) -> None:
        url = self._normalize_url(url)
        with self.lock:
            if url in self.seen_urls:
                return
            self.seen_urls.add(url)
        self.q.put(CrawlTask(url))

    def worker(self, wid: int) -> None:
        while True:
            try:
                task = self.q.get(timeout=1.0)
            except queue.Empty:
                return

            url = task.url

            if not self._allowed_by_robots(url):
                print(f"[SKIP robots] {url}")
                self.q.task_done()
                continue

            # Gentle rate limiting
            if self.delay > 0:
                time.sleep(self.delay)

            resp = self.fetch(url)
            if resp is None:
                self.q.task_done()
                continue

            # Only process OK responses
            if resp.status_code != 200:
                print(f"[{resp.status_code}] {url}")
                self.q.task_done()
                continue

            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()

            saved_path = self.save_response(url, resp)
            if saved_path is None:
                self.q.task_done()
                continue

            with self.lock:
                self.saved_urls.add(url)

            # If HTML, parse links and enqueue
            if any(content_type.startswith(ct) for ct in HTML_CT):
                with self.lock:
                    if self.max_pages and self.page_count >= self.max_pages:
                        # We've hit the page cap; still download assets discovered by already-enqueued pages.
                        pass
                    else:
                        self.page_count += 1

                html_text = ""
                try:
                    resp.encoding = resp.encoding or "utf-8"
                    html_text = resp.text
                except Exception:
                    pass

                if html_text:
                    pages, assets = self.extract_links_and_assets(url, html_text)

                    # Enqueue pages if within limit
                    for p in pages:
                        with self.lock:
                            if self.max_pages and self.page_count >= self.max_pages and p not in self.seen_urls:
                                # stop scheduling new pages if cap reached
                                continue
                        if self._same_domain(p):
                            self.enqueue(p)

                    # Enqueue assets
                    for a in assets:
                        if self._same_domain(a):
                            self.enqueue(a)

            # If CSS, scan for url(...) and enqueue
            elif any(content_type.startswith(ct) for ct in CSS_CT):
                css_text = ""
                try:
                    resp.encoding = resp.encoding or "utf-8"
                    css_text = resp.text
                except Exception:
                    pass
                if css_text:
                    for u in self.extract_css_urls(url, css_text):
                        self.enqueue(u)

            # Done
            rel = os.path.relpath(saved_path, self.out_dir)
            print(f"[OK] {url} -> {rel}")
            self.q.task_done()

    def run(self) -> None:
        print(f"Starting crawl: {self.start_url}")
        print(f"Output directory: {self.out_dir.resolve()}")
        scope = self.base.netloc if not self.include_subdomains else f"*.{self.base.hostname or self.base.netloc}"
        print(f"Scope: {scope}")
        if not self.ignore_robots:
            print("robots.txt: respected")
        else:
            print("robots.txt: IGNORED")
        print("Press Ctrl+C to stop.\n")

        self.enqueue(self.start_url)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = [ex.submit(self.worker, i) for i in range(self.workers)]
            try:
                # Wait for queue to empty
                self.q.join()
            except KeyboardInterrupt:
                print("\n[INTERRUPTED] Stopping…")
            finally:
                # Workers will exit when queue is empty/timeout
                pass

        print("\nDone.")
        print(f"Pages/assets scheduled: {len(self.seen_urls)}")
        print(f"Saved files: {len(self.saved_urls)}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Mirror a website locally.")
    ap.add_argument("start_url", help="Starting URL, e.g. https://example.com")
    ap.add_argument("out_dir", help="Output directory")
    ap.add_argument("--include-subdomains", action="store_true", help="Also follow links on subdomains")
    ap.add_argument("--max-pages", type=int, default=0, help="Max number of HTML pages to crawl (0 = unlimited)")
    ap.add_argument("--delay", type=float, default=0.2, help="Delay between requests per worker (seconds)")
    ap.add_argument("--workers", type=int, default=8, help="Number of concurrent workers")
    ap.add_argument("--ignore-robots", action="store_true", help="Ignore robots.txt")
    ap.add_argument("--timeout", type=float, default=20.0, help="Request timeout in seconds")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    mirror = SiteMirror(
        start_url=args.start_url,
        out_dir=Path(args.out_dir),
        include_subdomains=args.include_subdomains,
        max_pages=args.max_pages,
        delay=args.delay,
        workers=args.workers,
        ignore_robots=args.ignore_robots,
        timeout=args.timeout,
    )
    mirror.run()


if __name__ == "__main__":
    main()
