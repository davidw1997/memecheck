import requests

RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/{address}/report"


def get_rugcheck_data(address: str) -> dict:
    try:
        url = RUGCHECK_URL.format(address=address)
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()

        return {
            "success": True,
            "raw": data,
            "score": data.get("score"),
            "warnings": data.get("warnings", []),
            "is_rugged": data.get("rugged", False)
        }

    except requests.RequestException as exc:
        return {
            "success": False,
            "error": f"RugCheck request failed: {str(exc)}"
        }
