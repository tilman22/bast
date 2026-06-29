#!/usr/bin/env python3
"""
Export the Strava Global Heatmap as a high-resolution PNG.

Authentication works via browser cookies — see --help for instructions.
"""

import argparse
import math
import sys
import time
from io import BytesIO
from typing import Optional

import requests
from PIL import Image

TILE_SIZE = 256
TILE_HOSTS = ["a", "b", "c"]

ACTIVITY_TYPES = ["all", "ride", "run", "water", "winter"]
COLOR_SCHEMES = ["hot", "blue", "purple", "gray", "bluered"]


# ---------------------------------------------------------------------------
# Tile math
# ---------------------------------------------------------------------------

def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


# ---------------------------------------------------------------------------
# Tile download
# ---------------------------------------------------------------------------

def _build_session(cookies: dict) -> requests.Session:
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Referer": "https://www.strava.com/heatmap",
            "Accept": "image/avif,image/webp,image/png,*/*",
        }
    )
    return session


def download_tile(
    session: requests.Session,
    x: int,
    y: int,
    zoom: int,
    activity: str,
    color: str,
    retries: int = 3,
) -> Optional[Image.Image]:
    host = TILE_HOSTS[(x + y) % len(TILE_HOSTS)]
    url = (
        f"https://heatmap-external-{host}.strava.com"
        f"/tiles-auth/{activity}/{color}/{zoom}/{x}/{y}.png"
    )

    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                return Image.open(BytesIO(resp.content)).convert("RGBA")
            if resp.status_code == 401:
                print("\nAuthentication failed — cookies may be expired.", file=sys.stderr)
                return None
            if resp.status_code == 429:
                wait = 2**attempt
                print(f"\nRate limited, waiting {wait}s …", file=sys.stderr)
                time.sleep(wait)
            else:
                time.sleep(0.5 * (attempt + 1))
        except requests.RequestException as exc:
            if attempt < retries - 1:
                time.sleep(1)
            else:
                print(f"\nTile {x}/{y} failed: {exc}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Heatmap export
# ---------------------------------------------------------------------------

def export_heatmap(
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    zoom: int,
    cookies: dict,
    output_path: str,
    activity: str = "all",
    color: str = "hot",
    delay: float = 0.05,
) -> None:
    x_min, y_max = lat_lon_to_tile(lat_min, lon_min, zoom)
    x_max, y_min = lat_lon_to_tile(lat_max, lon_max, zoom)

    n_x = x_max - x_min + 1
    n_y = y_max - y_min + 1
    total = n_x * n_y
    width_px = n_x * TILE_SIZE
    height_px = n_y * TILE_SIZE

    print(f"Bounding box : {lat_min},{lon_min}  →  {lat_max},{lon_max}")
    print(f"Zoom         : {zoom}")
    print(f"Grid         : {n_x} × {n_y} = {total} tiles")
    print(f"Resolution   : {width_px} × {height_px} px")
    print(f"Activity     : {activity}   Color: {color}")

    canvas = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 255))
    session = _build_session(cookies)

    ok = fail = 0
    for row, y in enumerate(range(y_min, y_max + 1)):
        for col, x in enumerate(range(x_min, x_max + 1)):
            tile = download_tile(session, x, y, zoom, activity, color)
            if tile:
                canvas.paste(tile, (col * TILE_SIZE, row * TILE_SIZE))
                ok += 1
            else:
                fail += 1

            done = row * n_x + col + 1
            bar = "#" * (done * 30 // total)
            print(f"\r  [{bar:<30}] {done}/{total}", end="", flush=True)

            time.sleep(delay)

    print(f"\nDownloaded: {ok}  Failed: {fail}")

    final = canvas.convert("RGB")
    final.save(output_path, "PNG", optimize=False, compress_level=1)
    print(f"Saved  : {output_path}  ({width_px}×{height_px} px)")


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def parse_cookies(raw: str) -> dict:
    """Parse a 'key=value; key=value' cookie string."""
    cookies: dict = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

COOKIE_HELP = """
How to obtain your Strava cookies
----------------------------------
1.  Log in to https://www.strava.com
2.  Open  https://www.strava.com/heatmap  in the same browser tab
3.  Open DevTools (F12) → Network tab
4.  Reload the page and click on any *.png tile request
5.  In the request headers find the "Cookie:" header
6.  Copy its entire value and pass it via --cookies "..."
    (or save it to a text file and use --cookies-file)

The three critical cookies are:
  CloudFront-Key-Pair-Id, CloudFront-Policy, CloudFront-Signature
These expire after roughly one hour.
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the Strava Global Heatmap as a high-resolution PNG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=COOKIE_HELP
        + """
Examples
--------
  # Munich 100 km radius (default bbox), all activities, zoom 11
  python strava_heatmap_export.py \\
    --cookies "CloudFront-Key-Pair-Id=XXX; CloudFront-Policy=YYY; CloudFront-Signature=ZZZ" \\
    --output munich_100km.png

  # Munich 100 km radius, cycling only, blue colour, zoom 12
  python strava_heatmap_export.py \\
    --cookies-file cookies.txt --activity ride --color blue --zoom 12 \\
    --output munich_100km_cycling.png

  # Custom region
  python strava_heatmap_export.py \\
    --bbox 52.35,13.10,52.65,13.65 --zoom 12 \\
    --cookies-file cookies.txt --output berlin_heatmap.png
""",
    )

    # Default bbox: 100 km radius around Munich (48.137°N 11.576°E)
    # ±0.90° lat  (1° ≈ 111 km)
    # ±1.35° lon  (1° ≈ 74 km at 48°N)
    MUNICH_100KM = "47.237,10.226,49.037,12.926"

    parser.add_argument(
        "--bbox",
        default=MUNICH_100KM,
        metavar="LAT_MIN,LON_MIN,LAT_MAX,LON_MAX",
        help=f"Geographic bounding box to export (default: 100 km around Munich = {MUNICH_100KM})",
    )
    parser.add_argument(
        "--zoom",
        type=int,
        default=12,
        metavar="N",
        help="Tile zoom level (8–15 recommended; higher = more detail & more tiles)",
    )
    parser.add_argument(
        "--cookies",
        metavar="STRING",
        help="Raw cookie string copied from browser DevTools",
    )
    parser.add_argument(
        "--cookies-file",
        metavar="FILE",
        help="Path to a text file containing the cookie string",
    )
    parser.add_argument(
        "--output",
        default="strava_heatmap.png",
        metavar="FILE",
        help="Output PNG file (default: strava_heatmap.png)",
    )
    parser.add_argument(
        "--activity",
        default="all",
        choices=ACTIVITY_TYPES,
        help="Activity type filter (default: all)",
    )
    parser.add_argument(
        "--color",
        default="hot",
        choices=COLOR_SCHEMES,
        help="Heatmap colour scheme (default: hot)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        metavar="SECONDS",
        help="Delay between tile requests in seconds (default: 0.05)",
    )

    args = parser.parse_args()

    # Parse bounding box
    try:
        parts = [float(v) for v in args.bbox.split(",")]
        if len(parts) != 4:
            raise ValueError
        lat_min, lon_min, lat_max, lon_max = parts
        if lat_min >= lat_max or lon_min >= lon_max:
            raise ValueError("lat_min must be < lat_max and lon_min < lon_max")
    except ValueError as exc:
        parser.error(f"--bbox: {exc}")

    # Validate zoom
    if not 1 <= args.zoom <= 16:
        parser.error("--zoom must be between 1 and 16")

    # Get cookies
    raw_cookies = ""
    if args.cookies_file:
        try:
            with open(args.cookies_file) as fh:
                raw_cookies = fh.read().strip()
        except OSError as exc:
            parser.error(f"--cookies-file: {exc}")
    elif args.cookies:
        raw_cookies = args.cookies
    else:
        parser.error(
            "Provide Strava authentication via --cookies or --cookies-file.\n"
            + COOKIE_HELP
        )

    cookies = parse_cookies(raw_cookies)

    # Warn on large jobs
    x_min, y_max = lat_lon_to_tile(lat_min, lon_min, args.zoom)
    x_max, y_min = lat_lon_to_tile(lat_max, lon_max, args.zoom)
    n_tiles = (x_max - x_min + 1) * (y_max - y_min + 1)
    if n_tiles > 500:
        print(
            f"Warning: {n_tiles} tiles will be downloaded "
            f"(~{n_tiles * args.delay:.0f}s at current delay)."
        )
        try:
            input("Press Enter to continue or Ctrl+C to cancel …")
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(0)

    export_heatmap(
        lat_min=lat_min,
        lat_max=lat_max,
        lon_min=lon_min,
        lon_max=lon_max,
        zoom=args.zoom,
        cookies=cookies,
        output_path=args.output,
        activity=args.activity,
        color=args.color,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()
