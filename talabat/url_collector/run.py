"""
Talabat UAE — Restaurant URL Collector
=======================================
Collects ALL restaurant URLs from Talabat UAE listing pages.

Usage:
    cd talabat/
    python -m url_collector.run

Options:
    --no-proxy          Run without Oxylabs proxy (testing only)
    --no-discover       Skip area discovery, use main listing only
    --concurrency N     Max concurrent page fetches (default: 3)
    --min-delay F       Min seconds between requests (default: 2.0)
    --max-delay F       Max seconds between requests (default: 4.0)
    --output-dir PATH   Where to save output (default: data/urls)

Environment variables (in .env):
    OXYLABS_USERNAME    Your Oxylabs customer username
    OXYLABS_PASSWORD    Your Oxylabs password
    OXYLABS_COUNTRY     Proxy exit country code (default: ae)
"""

import argparse
import asyncio
import sys

from loguru import logger

from .collector import UrlCollector
from .config import CollectorConfig, ProxyConfig


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Collect all Talabat UAE restaurant URLs"
    )
    p.add_argument("--no-proxy", action="store_true", help="Disable Oxylabs proxy")
    p.add_argument("--no-discover", action="store_true", help="Skip area discovery")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--min-delay", type=float, default=2.0)
    p.add_argument("--max-delay", type=float, default=4.0)
    p.add_argument("--output-dir", default="data/urls")
    return p.parse_args()


def setup_logging(output_dir: str):
    import os
    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, "collector.log")
    logger.remove()
    logger.add(sys.stderr, level="INFO", colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    logger.add(log_path, level="DEBUG", rotation="10 MB", retention="7 days",
               format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")


async def main():
    args = parse_args()
    setup_logging(args.output_dir)

    cfg = CollectorConfig(
        max_concurrent=args.concurrency,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        output_dir=args.output_dir,
    )

    proxy = ProxyConfig()
    if args.no_proxy:
        # Blank out credentials so proxy is treated as disabled
        proxy.username = ""
        proxy.password = ""
        logger.warning("Proxy DISABLED — requests will use your real IP")

    if not proxy.enabled:
        logger.warning(
            "No proxy configured. Set OXYLABS_USERNAME and OXYLABS_PASSWORD in .env "
            "for residential proxy rotation."
        )

    collector = UrlCollector(cfg, proxy)
    result = await collector.run(discover_areas_flag=not args.no_discover)

    print(f"\n{'='*60}")
    print(f"  Total unique restaurant URLs : {result['total_unique_urls']}")
    print(f"  Pages completed              : {result['total_pages_completed']}")
    print(f"  Pages failed                 : {result['total_pages_failed']}")
    print(f"  Areas discovered             : {result['areas_discovered']}")
    print(f"  Output file                  : {args.output_dir}/{cfg.output_file}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
