import re


def detect_chain(address: str) -> str:
    if not address:
        return "unknown"

    address = address.strip()

    if re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
        return "base"

    if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", address):
        return "solana"

    return "unknown"
