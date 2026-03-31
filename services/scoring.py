def calculate_risk_score(data: dict) -> dict:
    score = 0
    reasons = []

    liquidity = float(data.get("liquidity_usd") or 0)
    volume = float(data.get("volume_24h") or 0)
    age = float(data.get("age_minutes") or 9999)
    rug_warnings = data.get("rugcheck_warnings") or []

    if liquidity < 20000:
        score += 25
        reasons.append("Low liquidity")

    if volume < 10000:
        score += 15
        reasons.append("Low trading volume")

    if age < 120:
        score += 20
        reasons.append("Very new token (high risk launch phase)")

    if len(rug_warnings) >= 2:
        score += 20
        reasons.append("Multiple contract risk warnings")

    if liquidity > 100000:
        score -= 10

    if volume > 100000:
        score -= 10

    score = max(0, min(score, 100))

    if score < 25:
        level = "Low"
    elif score < 50:
        level = "Moderate"
    elif score < 75:
        level = "High"
    else:
        level = "Extreme"

    return {
        "score": score,
        "level": level,
        "reasons": reasons
    }


def get_recommendation(risk_score: int) -> str:
    if risk_score >= 75:
        return "AVOID"
    elif risk_score >= 50:
        return "WAIT"
    else:
        return "ENTER"


def calculate_momentum_score(data: dict) -> int:
    liquidity = float(data.get("liquidity_usd") or 0)
    volume = float(data.get("volume_24h") or 0)
    price_change = float(data.get("price_change_24h") or 0)
    age = float(data.get("age_minutes") or 9999)

    score = 0

    if volume > 50000:
        score += 25
    elif volume > 10000:
        score += 15

    if liquidity > 50000:
        score += 20
    elif liquidity > 20000:
        score += 10

    if price_change > 25:
        score += 30
    elif price_change > 10:
        score += 20
    elif price_change > 0:
        score += 10

    if age < 180:
        score += 15
    elif age < 1440:
        score += 10

    return max(0, min(score, 100))


def detect_phase(data: dict, risk_score: int, momentum_score: int) -> str:
    liquidity = float(data.get("liquidity_usd") or 0)
    volume = float(data.get("volume_24h") or 0)
    price_change = float(data.get("price_change_24h") or 0)
    age = float(data.get("age_minutes") or 9999)

    if age < 120 and liquidity < 50000:
        return "Launch"

    if momentum_score >= 60 and price_change > 10 and volume > liquidity:
        return "Viral Growth"

    if price_change < 0 and volume > 20000 and risk_score >= 50:
        return "Distribution Risk"

    return "Stabilizing"


def detect_hype_type(data: dict, risk_score: int, momentum_score: int) -> str:
    liquidity = float(data.get("liquidity_usd") or 0)
    volume = float(data.get("volume_24h") or 0)
    price_change = float(data.get("price_change_24h") or 0)
    warnings = data.get("rugcheck_warnings") or []

    if price_change > 40 and liquidity < 25000 and len(warnings) >= 2:
        return "Likely Inorganic"

    if momentum_score >= 50 and risk_score < 50 and volume > liquidity * 0.5:
        return "Likely Organic"

    return "Mixed / Unclear"
