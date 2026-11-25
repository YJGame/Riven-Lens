"""Backwards-compatible facade re-exporting Warframe.market client symbols from `src.wm_client`."""

from src.wm_client import (  # noqa: F401
    CANONICAL_ZH_TO_WM_SLUG,
    DEFAULT_TIERS_PATH,
    MATCH_LEVEL_LABELS,
    WM_AUCTIONS_API_URL,
    WM_AUCTIONS_WEB_URL,
    WM_HTTP_TIMEOUT_SEC,
    WMFetchError,
    WMMarketReport,
    WMMatchingListing,
    WMStatFreq,
    analyze_auctions_payload,
    build_wm_query_params,
    build_wm_search_url,
    extract_top_listings,
    fetch_wm_exact_riven_matches,
    fetch_wm_riven_stats,
    save_wm_recommendation_to_local,
    stat_name_to_wm_slug,
)

WM_AUCTIONS_SEARCH_URL = WM_AUCTIONS_API_URL
