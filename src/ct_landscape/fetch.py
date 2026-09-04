"""Stage 0 — acquire the ClinicalTrials.gov dump (spec §2.1, §5 Stage 0).

Primary route: the site's empty-search "Download" button, which calls the internal endpoint
    https://clinicaltrials.gov/api/int/studies/download?format=json.zip
(HTTP 200, application/zip, one JSON file per study). It is not in the OpenAPI spec, so the
documented v2 pager is kept as a fallback that writes the *same* per-study-JSON zip layout.

Census: bytes + n_files, printed and returned.
"""

from __future__ import annotations

import json
import sys
import time
import zipfile
from pathlib import Path

import httpx

DOWNLOAD_URL = "https://clinicaltrials.gov/api/int/studies/download?format=json.zip"
PAGER_URL = "https://clinicaltrials.gov/api/v2/studies"
DEFAULT_ZIP = Path("data/raw/ctg-studies.json.zip")


def _progress(done: int, total: int | None, t0: float) -> None:
    mb = done / 1e6
    rate = mb / max(time.monotonic() - t0, 1e-6)
    tail = f" of {total / 1e6:,.0f} MB" if total else ""
    print(f"\r  {mb:,.0f} MB{tail}  ({rate:,.1f} MB/s)", end="", file=sys.stderr, flush=True)


def download_zip(dest: Path = DEFAULT_ZIP, url: str = DOWNLOAD_URL) -> dict:
    """Stream the internal download endpoint to `dest` (atomic via .part rename)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    t0 = time.monotonic()
    with httpx.stream("GET", url, timeout=httpx.Timeout(60.0, read=600.0), follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers["content-length"]) if "content-length" in r.headers else None
        done = 0
        with part.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if done % (50 << 20) < (1 << 20):
                    _progress(done, total, t0)
    print(file=sys.stderr)
    part.replace(dest)
    return census(dest)


def crawl_pager(dest: Path = DEFAULT_ZIP, page_size: int = 1000, max_pages: int | None = None) -> dict:
    """Fallback: page GET /api/v2/studies into the same one-file-per-study zip layout."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    n = 0
    token: str | None = None
    with (
        httpx.Client(timeout=120.0) as client,
        zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED) as zf,
    ):
        page = 0
        while True:
            params = {"format": "json", "pageSize": page_size}
            if token:
                params["pageToken"] = token
            r = client.get(PAGER_URL, params=params)
            r.raise_for_status()
            body = r.json()
            for study in body.get("studies", []):
                nct = study["protocolSection"]["identificationModule"]["nctId"]
                zf.writestr(f"{nct}.json", json.dumps(study))
                n += 1
            page += 1
            token = body.get("nextPageToken")
            print(f"\r  page {page}: {n:,} studies", end="", file=sys.stderr, flush=True)
            if not token or (max_pages and page >= max_pages):
                break
    print(file=sys.stderr)
    part.replace(dest)
    return census(dest)


def census(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as zf:
        n_files = sum(1 for i in zf.infolist() if not i.is_dir())
    c = {"zip_path": str(zip_path), "bytes": zip_path.stat().st_size, "n_files": n_files}
    print(f"fetch census: {c['bytes']:,} bytes, {c['n_files']:,} files", file=sys.stderr)
    return c


if __name__ == "__main__":  # python -m ct_landscape.fetch [dest]
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ZIP
    download_zip(out)
