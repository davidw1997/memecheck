import requests
import time

DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"


def get_token_data(address: str) -> dict:
    try:
        url = DEXSCREENER_URL.format(address=address)
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()

        pairs = data.get("pairs", [])
        if not pairs:
            return {
                "success": False,
                "error": "No trading pairs found for this token."
            }

        pair = pairs[0]

        pair_created = pair.get("pairCreatedAt")
        age_minutes = None

        if pair_created:
            now = int(time.time() * 1000)
            age_minutes = int((now - pair_created) / 60000)

        return {
            "success": True,
            "source": "dexscreener",
            "raw": pair,
            "token_name": pair.get("baseToken", {}).get("name", "Unknown"),
            "symbol": pair.get("baseToken", {}).get("symbol", "Unknown"),
            "chain_id": pair.get("chainId", "unknown"),
            "dex_id": pair.get("dexId", "unknown"),
            "pair_address": pair.get("pairAddress"),
            "price_usd": pair.get("priceUsd"),
            "liquidity_usd": pair.get("liquidity", {}).get("usd"),
            "fdv": pair.get("fdv"),
            "market_cap": pair.get("marketCap"),
            "volume_24h": pair.get("volume", {}).get("h24"),
            "price_change_24h": pair.get("priceChange", {}).get("h24"),
            "url": pair.get("url"),
            "age_minutes": age_minutes,
        }

    except requests.RequestException as exc:
        return {
            "success": False,
            "error": f"DEX Screener request failed: {str(exc)}"
        }
