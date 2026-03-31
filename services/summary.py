import json
import requests


def generate_fallback_summary(risk: dict, recommendation: str, facts: dict) -> str:
    reasons = ", ".join(risk["reasons"]) if risk["reasons"] else "no major red flags"

    token_name = facts.get("token_name") or "This token"
    symbol = facts.get("symbol") or ""
    liquidity = facts.get("liquidity_usd")
    volume = facts.get("volume_24h")
    age = facts.get("age_minutes")
    momentum_score = facts.get("momentum_score")
    phase = facts.get("phase")
    hype_type = facts.get("hype_type")

    symbol_text = f" ({symbol})" if symbol else ""

    return (
        f"{token_name}{symbol_text} shows {risk['level'].lower()} risk with a score of {risk['score']}. "
        f"Momentum score is {momentum_score}, phase is {phase}, and hype type is {hype_type}. "
        f"Liquidity is {liquidity}, 24h volume is {volume}, and token age is {age} minutes. "
        f"Key factors include: {reasons}. Suggested action: {recommendation}."
    )


def generate_summary(
    risk: dict,
    recommendation: str,
    facts: dict,
    api_key: str = "",
    model: str = "gpt-5"
) -> str:
    if not api_key:
        return generate_fallback_summary(risk, recommendation, facts)

    safe_facts = {
        "token_name": facts.get("token_name"),
        "symbol": facts.get("symbol"),
        "chain": facts.get("chain"),
        "address": facts.get("address"),
        "liquidity_usd": facts.get("liquidity_usd"),
        "volume_24h": facts.get("volume_24h"),
        "price_usd": facts.get("price_usd"),
        "price_change_24h": facts.get("price_change_24h"),
        "age_minutes": facts.get("age_minutes"),
        "rugcheck_warnings": facts.get("rugcheck_warnings", []),
        "risk_score": risk.get("score"),
        "risk_level": risk.get("level"),
        "risk_reasons": risk.get("reasons", []),
        "recommendation": recommendation,
        "momentum_score": facts.get("momentum_score"),
        "phase": facts.get("phase"),
        "hype_type": facts.get("hype_type"),
    }

    prompt = f"""
You are writing a concise meme coin lifecycle report.

Use only the facts provided.
Do not promise profits.
Do not invent missing data.
Keep it concise and useful.
Write:
1. One short paragraph
2. Bullish signals
3. Risks
4. Bottom line

Facts:
{json.dumps(safe_facts, indent=2)}
""".strip()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "input": [
            {
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": "You produce concise, evidence-based crypto lifecycle summaries."
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt
                    }
                ]
            }
        ]
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=payload,
            timeout=45
        )
        response.raise_for_status()
        data = response.json()

        text = data.get("output_text")
        if text and text.strip():
            return text.strip()

        return generate_fallback_summary(risk, recommendation, facts)

    except Exception:
        return generate_fallback_summary(risk, recommendation, facts)
