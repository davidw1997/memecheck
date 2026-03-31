import requests

BIRDEYE_TOKEN_OVERVIEW_URL = "https://public-api.birdeye.so/defi/token_overview"


def get_birdeye_data(address: str, chain: str, api_key: str) -> dict:
    if not api_key:
        return {
            "success": False,
            "error": "Missing Birdeye API key."
        }

    chain_header = "solana" if chain == "solana" else "base"

    headers = {
        "accept": "application/json",
        "x-api-key": api_key,
        "x-chain": chain_header
    }

    params = {
        "address": address
    }

    try:
        response = requests.get(
            BIRDEYE_TOKEN_OVERVIEW_URL,
            headers=headers,
            params=params,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()

        token_data = data.get("data", {})

        return {
            "success": True,
            "source": "birdeye",
            "raw": token_data,
            "holder_count": token_data.get("holder"),
            "logo_uri": token_data.get("logoURI"),
            "price": token_data.get("price"),
            "liquidity": token_data.get("liquidity"),
            "mc": token_data.get("mc"),
            "v24h": token_data.get("v24h"),
        }

    except requests.RequestException as exc:
        return {
            "success": False,
            "error": f"Birdeye request failed: {str(exc)}"
        }
