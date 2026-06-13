import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ProxyConfig:
    username: str = field(default_factory=lambda: os.getenv("OXYLABS_USERNAME", ""))
    password: str = field(default_factory=lambda: os.getenv("OXYLABS_PASSWORD", ""))
    country: str = field(default_factory=lambda: os.getenv("OXYLABS_COUNTRY", "ae"))
    host: str = "pr.oxylabs.io"
    port: int = 7777

    def get_url(self, session_id: str) -> str:
        return (
            f"https://customer-{self.username}-cc-{self.country}"
            f"-sessid-{session_id}:{self.password}@{self.host}:{self.port}"
        )

    @property
    def enabled(self) -> bool:
        return bool(self.username and self.password)


@dataclass
class CollectorConfig:
    # UAE restaurant listing — paginate from page 1 until empty
    base_listing_url: str = "https://www.talabat.com/uae/restaurants"

    # Area-based URLs discovered at runtime (area_id → slug)
    # These are seeded with known UAE areas; collector also auto-discovers more
    known_areas: dict = field(default_factory=lambda: {
        # Format: area_id → area_slug
        # Main city-level entries (broad, high restaurant count)
        # Discovered from Talabat UAE nav; add more as you find them
        6815: "abu-dhabi-international-airport",
        6484: "mohammed-bin-zayed-city",
        # Will be populated dynamically by area discovery
    })

    # Concurrency — keep low to avoid triggering rate limits
    max_concurrent: int = 3

    # Delay between requests per worker (seconds)
    min_delay: float = 2.0
    max_delay: float = 4.0

    # Retry settings
    max_retries: int = 3
    retry_backoff: float = 2.0  # multiplier per retry attempt

    # Rotate proxy session ID every N requests to avoid session bans
    rotate_session_every: int = 20

    # curl_cffi Chrome impersonation profile — spoof TLS fingerprint
    impersonate: str = "chrome124"
    timeout: int = 30

    # Checkpoint + output paths (relative to talabat/ root)
    output_dir: str = "data/urls"
    output_file: str = "talabat_uae_all_urls.json"
    checkpoint_file: str = "checkpoint.json"

    # Auto-save interval (every N pages)
    checkpoint_interval: int = 10
