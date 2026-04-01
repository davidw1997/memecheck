from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from config import Config
import os
import json
import hmac
import hashlib
import requests
from datetime import date

from services.chain import detect_chain
from services.dexscreener import get_token_data
from services.birdeye import get_birdeye_data
from services.rugcheck import get_rugcheck_data
from services.scoring import (
    calculate_risk_score,
    get_recommendation,
    calculate_momentum_score,
    detect_phase,
    detect_hype_type,
)
from services.summary import generate_summary

app = Flask(__name__)
app.config.from_object(Config)

db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    plan = db.Column(db.String(20), nullable=False, default="free")
    stripe_customer_id = db.Column(db.String(120))
    stripe_subscription_id = db.Column(db.String(120))
    subscription_status = db.Column(db.String(40), default="inactive")

    free_analysis_count = db.Column(db.Integer, default=0)
    free_analysis_date = db.Column(db.Date)

    created_at = db.Column(db.DateTime, server_default=db.func.now())


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    address = db.Column(db.String(100))
    chain = db.Column(db.String(20))
    token_name = db.Column(db.String(120))
    symbol = db.Column(db.String(40))
    price_usd = db.Column(db.String(40))
    risk_score = db.Column(db.Integer)
    risk_level = db.Column(db.String(20))
    recommendation = db.Column(db.String(10))
    momentum_score = db.Column(db.Integer)
    phase = db.Column(db.String(40))
    hype_type = db.Column(db.String(40))
    summary = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class WatchlistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    address = db.Column(db.String(100), nullable=False)
    chain = db.Column(db.String(20), nullable=False)
    token_name = db.Column(db.String(120))
    symbol = db.Column(db.String(40))
    last_price_usd = db.Column(db.String(40))
    last_risk_score = db.Column(db.Integer)
    last_recommendation = db.Column(db.String(10))
    last_momentum_score = db.Column(db.Integer)
    last_phase = db.Column(db.String(40))
    last_hype_type = db.Column(db.String(40))
    created_at = db.Column(db.DateTime, server_default=db.func.now())


with app.app_context():
    reset_db = os.getenv("RESET_DB_ON_START", "False").lower() == "true"
    if reset_db:
        db.drop_all()
    db.create_all()


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


@app.context_processor
def inject_user():
    user = current_user()
    return {
        "current_user": user,
        "is_pro_user": is_pro(user) if user else False
    }


def login_required(route_function):
    @wraps(route_function)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in first.")
            return redirect(url_for("login"))
        return route_function(*args, **kwargs)
    return wrapper


def is_pro(user):
    if not user:
        return False
    return user.plan == "pro" and user.subscription_status in ("active", "trialing")


def reset_free_counter_if_needed(user):
    today = date.today()
    if user.free_analysis_date != today:
        user.free_analysis_date = today
        user.free_analysis_count = 0
        db.session.commit()


def free_analyses_remaining(user):
    reset_free_counter_if_needed(user)
    return max(0, app.config["FREE_DAILY_ANALYSIS_LIMIT"] - (user.free_analysis_count or 0))


def increment_free_analysis(user):
    reset_free_counter_if_needed(user)
    user.free_analysis_count = (user.free_analysis_count or 0) + 1
    db.session.commit()


def premium_required():
    user = current_user()
    if not is_pro(user):
        flash("That feature is part of the Pro plan.")
        return False
    return True


def stripe_bearer_headers():
    return {
        "Authorization": f"Bearer {app.config['STRIPE_SECRET_KEY']}"
    }


def stripe_form_headers():
    return {
        "Authorization": f"Bearer {app.config['STRIPE_SECRET_KEY']}",
        "Content-Type": "application/x-www-form-urlencoded"
    }


def upgrade_user_to_pro(user, customer_id=None, subscription_id=None, status="active"):
    if not user:
        return
    if customer_id:
        user.stripe_customer_id = customer_id
    if subscription_id:
        user.stripe_subscription_id = subscription_id
    user.plan = "pro"
    user.subscription_status = status
    db.session.commit()


def downgrade_user_to_free(user, status="canceled"):
    if not user:
        return
    user.plan = "free"
    user.subscription_status = status
    db.session.commit()


def sync_user_plan_from_stripe(user):
    if not user or not app.config["STRIPE_SECRET_KEY"]:
        return

    customer_id = user.stripe_customer_id

    if not customer_id and user.email:
        try:
            resp = requests.get(
                "https://api.stripe.com/v1/customers/search",
                headers=stripe_bearer_headers(),
                params={
                    "query": f"email:'{user.email}'",
                    "limit": 1
                },
                timeout=30
            )
            if resp.status_code < 400:
                data = resp.json()
                customers = data.get("data", [])
                if customers:
                    customer_id = customers[0].get("id")
                    user.stripe_customer_id = customer_id
                    db.session.commit()
        except Exception:
            pass

    if not customer_id:
        return

    try:
        resp = requests.get(
            "https://api.stripe.com/v1/subscriptions",
            headers=stripe_bearer_headers(),
            params={
                "customer": customer_id,
                "status": "all",
                "limit": 10
            },
            timeout=30
        )
        if resp.status_code >= 400:
            return

        data = resp.json()
        subs = data.get("data", [])

        active_like = None
        for sub in subs:
            if sub.get("status") in ("active", "trialing"):
                active_like = sub
                break

        if active_like:
            upgrade_user_to_pro(
                user,
                customer_id=customer_id,
                subscription_id=active_like.get("id"),
                status=active_like.get("status", "active")
            )
        else:
            if subs:
                latest = subs[0]
                user.stripe_subscription_id = latest.get("id")
                user.subscription_status = latest.get("status", "inactive")
                user.plan = "free"
                db.session.commit()
    except Exception:
        pass


def detect_tradingview_symbol(chain: str, symbol: str):
    if not symbol:
        return ""

    symbol = symbol.upper().strip()

    common_map = {
        "BTC": "BINANCE:BTCUSDT",
        "ETH": "BINANCE:ETHUSDT",
        "SOL": "BINANCE:SOLUSDT",
        "BNB": "BINANCE:BNBUSDT",
        "XRP": "BINANCE:XRPUSDT",
        "DOGE": "BINANCE:DOGEUSDT",
        "ADA": "BINANCE:ADAUSDT",
        "AVAX": "BINANCE:AVAXUSDT",
        "LINK": "BINANCE:LINKUSDT",
        "PEPE": "BINANCE:PEPEUSDT",
        "SHIB": "BINANCE:SHIBUSDT",
        "BONK": "BINANCE:BONKUSDT",
        "WIF": "BINANCE:WIFUSDT",
        "FLOKI": "BINANCE:FLOKIUSDT",
    }

    return common_map.get(symbol, "")


def analyze_token(address: str, chain: str):
    dex_data = get_token_data(address)

    birdeye_data = get_birdeye_data(
        address=address,
        chain=chain,
        api_key=app.config.get("BIRDEYE_API_KEY", "")
    )

    rug_data = None
    if chain == "solana":
        rug_data = get_rugcheck_data(address)

    normalized = {
        "liquidity_usd": dex_data.get("liquidity_usd") if dex_data.get("success") else 0,
        "volume_24h": dex_data.get("volume_24h") if dex_data.get("success") else 0,
        "price_change_24h": dex_data.get("price_change_24h") if dex_data.get("success") else 0,
        "rugcheck_warnings": rug_data.get("warnings") if rug_data and rug_data.get("success") else [],
        "age_minutes": dex_data.get("age_minutes") if dex_data.get("success") else None
    }

    risk = calculate_risk_score(normalized)
    recommendation = get_recommendation(risk["score"])
    momentum_score = calculate_momentum_score(normalized)
    phase = detect_phase(normalized, risk["score"], momentum_score)
    hype_type = detect_hype_type(normalized, risk["score"], momentum_score)

    facts = {
        "token_name": dex_data.get("token_name") if dex_data.get("success") else "Unknown",
        "symbol": dex_data.get("symbol") if dex_data.get("success") else "Unknown",
        "chain": chain,
        "address": address,
        "liquidity_usd": dex_data.get("liquidity_usd") if dex_data.get("success") else None,
        "volume_24h": dex_data.get("volume_24h") if dex_data.get("success") else None,
        "price_usd": dex_data.get("price_usd") if dex_data.get("success") else None,
        "price_change_24h": dex_data.get("price_change_24h") if dex_data.get("success") else None,
        "age_minutes": dex_data.get("age_minutes") if dex_data.get("success") else None,
        "rugcheck_warnings": rug_data.get("warnings") if rug_data and rug_data.get("success") else [],
        "momentum_score": momentum_score,
        "phase": phase,
        "hype_type": hype_type,
    }

    summary = generate_summary(
        risk=risk,
        recommendation=recommendation,
        facts=facts,
        api_key=app.config.get("OPENAI_API_KEY", ""),
        model=app.config.get("OPENAI_MODEL", "gpt-5")
    )

    return {
        "dex_data": dex_data,
        "birdeye_data": birdeye_data,
        "rug_data": rug_data,
        "risk": risk,
        "recommendation": recommendation,
        "momentum_score": momentum_score,
        "phase": phase,
        "hype_type": hype_type,
        "facts": facts,
        "summary": summary
    }


def save_report(user_id: int, address: str, chain: str, facts: dict, risk: dict, recommendation: str, summary: str):
    report = Report(
        user_id=user_id,
        address=address,
        chain=chain,
        token_name=facts["token_name"],
        symbol=facts["symbol"],
        price_usd=str(facts["price_usd"]) if facts["price_usd"] is not None else "",
        risk_score=risk["score"],
        risk_level=risk["level"],
        recommendation=recommendation,
        momentum_score=facts["momentum_score"],
        phase=facts["phase"],
        hype_type=facts["hype_type"],
        summary=summary
    )
    db.session.add(report)
    db.session.commit()


def build_signals(
    old_risk_score,
    new_risk_score,
    old_recommendation,
    new_recommendation,
    old_price,
    new_price,
    old_phase,
    new_phase,
    old_momentum,
    new_momentum,
    old_hype_type,
    new_hype_type,
):
    signals = []

    if old_risk_score is not None and new_risk_score is not None:
        if new_risk_score > old_risk_score:
            signals.append(f"Collapse probability increased from {old_risk_score} to {new_risk_score}")
        elif new_risk_score < old_risk_score:
            signals.append(f"Collapse probability decreased from {old_risk_score} to {new_risk_score}")

    if old_recommendation and new_recommendation and old_recommendation != new_recommendation:
        signals.append(f"Entry window changed from {old_recommendation} to {new_recommendation}")

    if old_phase and new_phase and old_phase != new_phase:
        signals.append(f"Hype phase changed from {old_phase} to {new_phase}")

    if old_hype_type and new_hype_type and old_hype_type != new_hype_type:
        signals.append(f"Hype type changed from {old_hype_type} to {new_hype_type}")

    if old_momentum is not None and new_momentum is not None:
        if new_momentum > old_momentum:
            signals.append(f"Hype velocity increased from {old_momentum} to {new_momentum}")
        elif new_momentum < old_momentum:
            signals.append(f"Hype velocity decreased from {old_momentum} to {new_momentum}")

    try:
        if old_price not in (None, "", "None") and new_price not in (None, "", "None"):
            old_price_f = float(old_price)
            new_price_f = float(new_price)

            if new_price_f > old_price_f:
                pct = ((new_price_f - old_price_f) / old_price_f) * 100 if old_price_f != 0 else 0
                signals.append(f"Price increased by {pct:.2f}%")
            elif new_price_f < old_price_f:
                pct = ((old_price_f - new_price_f) / old_price_f) * 100 if old_price_f != 0 else 0
                signals.append(f"Price decreased by {pct:.2f}%")
        elif new_price not in (None, "", "None"):
            signals.append("Current price available")
    except (ValueError, TypeError, ZeroDivisionError):
        pass

    if not signals:
        signals.append("No major changes detected")

    return signals


def verify_stripe_signature(payload: bytes, sig_header: str, secret: str):
    if not sig_header or not secret:
        return False

    parts = sig_header.split(",")
    timestamp = None
    signature = None

    for part in parts:
        if part.startswith("t="):
            timestamp = part.split("=", 1)[1]
        elif part.startswith("v1="):
            signature = part.split("=", 1)[1]

    if not timestamp or not signature:
        return False

    signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
    expected = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect(url_for("profile"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not username or not email or not password or not confirm_password:
            flash("Please fill out all fields.")
            return redirect(url_for("signup"))

        if password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for("signup"))

        if User.query.filter_by(username=username).first():
            flash("Username already exists.")
            return redirect(url_for("signup"))

        if User.query.filter_by(email=email).first():
            flash("Email already exists.")
            return redirect(url_for("signup"))

        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            free_analysis_date=date.today(),
            free_analysis_count=0
        )
        db.session.add(user)
        db.session.commit()

        session["user_id"] = user.id
        flash("Account created successfully.")
        return redirect(url_for("profile"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("profile"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.")
            return redirect(url_for("login"))

        session["user_id"] = user.id
        flash("Logged in successfully.")
        return redirect(url_for("profile"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.")
    return redirect(url_for("home"))


@app.route("/profile")
@login_required
def profile():
    user = current_user()
    sync_user_plan_from_stripe(user)
    user = current_user()

    reports = Report.query.filter_by(user_id=user.id).order_by(Report.created_at.desc()).limit(20).all()
    watchlist_count = WatchlistItem.query.filter_by(user_id=user.id).count()
    return render_template(
        "profile.html",
        user=user,
        reports=reports,
        watchlist_count=watchlist_count,
        free_remaining=free_analyses_remaining(user) if not is_pro(user) else None
    )


@app.route("/pricing")
@login_required
def pricing():
    user = current_user()
    return render_template(
        "pricing.html",
        user=user,
        free_remaining=free_analyses_remaining(user) if not is_pro(user) else None
    )


@app.route("/sync-subscription", methods=["POST"])
@login_required
def sync_subscription():
    user = current_user()
    sync_user_plan_from_stripe(user)
    flash("Subscription sync attempted. Refreshing profile.")
    return redirect(url_for("profile"))


@app.route("/create-checkout-session", methods=["POST"])
@login_required
def create_checkout_session():
    user = current_user()

    if is_pro(user):
        flash("You already have Pro.")
        return redirect(url_for("pricing"))

    if not app.config["STRIPE_SECRET_KEY"] or not app.config["STRIPE_PRICE_ID_PRO"]:
        flash("Stripe is not configured yet.")
        return redirect(url_for("pricing"))

    data = {
        "mode": "subscription",
        "success_url": f"{app.config['APP_BASE_URL']}/billing/success",
        "cancel_url": f"{app.config['APP_BASE_URL']}/billing/cancel",
        "client_reference_id": str(user.id),
        "customer_email": user.email,
        "line_items[0][price]": app.config["STRIPE_PRICE_ID_PRO"],
        "line_items[0][quantity]": "1",
        "metadata[user_id]": str(user.id)
    }

    response = requests.post(
        "https://api.stripe.com/v1/checkout/sessions",
        headers=stripe_form_headers(),
        data=data,
        timeout=45
    )

    if response.status_code >= 400:
        flash("Could not start checkout session.")
        return redirect(url_for("pricing"))

    session_data = response.json()
    checkout_url = session_data.get("url")

    if not checkout_url:
        flash("Stripe did not return a checkout URL.")
        return redirect(url_for("pricing"))

    return redirect(checkout_url)


@app.route("/create-billing-portal-session", methods=["POST"])
@login_required
def create_billing_portal_session():
    user = current_user()
    sync_user_plan_from_stripe(user)
    user = current_user()

    if not is_pro(user):
        flash("You do not have an active Pro subscription.")
        return redirect(url_for("pricing"))

    if not app.config["STRIPE_SECRET_KEY"]:
        flash("Stripe is not configured yet.")
        return redirect(url_for("pricing"))

    if not user.stripe_customer_id:
        flash("No Stripe customer found for this account.")
        return redirect(url_for("profile"))

    data = {
        "customer": user.stripe_customer_id,
        "return_url": f"{app.config['APP_BASE_URL']}/profile"
    }

    response = requests.post(
        "https://api.stripe.com/v1/billing_portal/sessions",
        headers=stripe_form_headers(),
        data=data,
        timeout=45
    )

    if response.status_code >= 400:
        flash("Could not open billing portal.")
        return redirect(url_for("profile"))

    portal_data = response.json()
    portal_url = portal_data.get("url")

    if not portal_url:
        flash("Stripe did not return a billing portal URL.")
        return redirect(url_for("profile"))

    return redirect(portal_url)


@app.route("/billing/success")
@login_required
def billing_success():
    user = current_user()
    sync_user_plan_from_stripe(user)
    flash("Payment submitted. Your subscription status has been rechecked.")
    return redirect(url_for("profile"))


@app.route("/billing/cancel")
@login_required
def billing_cancel():
    flash("Checkout canceled.")
    return redirect(url_for("pricing"))


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")

    if not verify_stripe_signature(payload, sig_header, app.config["STRIPE_WEBHOOK_SECRET"]):
        return {"error": "invalid signature"}, 400

    event = json.loads(payload.decode("utf-8"))
    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        user_id = obj.get("metadata", {}).get("user_id") or obj.get("client_reference_id")

        if user_id:
            user = db.session.get(User, int(user_id))
            if user:
                upgrade_user_to_pro(
                    user,
                    customer_id=obj.get("customer"),
                    subscription_id=obj.get("subscription"),
                    status="active"
                )

    elif event_type in ("customer.subscription.created", "customer.subscription.updated"):
        customer_id = obj.get("customer")
        subscription_id = obj.get("id")
        status = obj.get("status", "inactive")

        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            if status in ("active", "trialing"):
                upgrade_user_to_pro(
                    user,
                    customer_id=customer_id,
                    subscription_id=subscription_id,
                    status=status
                )
            else:
                user.stripe_subscription_id = subscription_id
                user.subscription_status = status
                user.plan = "free"
                db.session.commit()

    elif event_type == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            downgrade_user_to_free(user, status="canceled")

    return {"received": True}, 200


@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    user = current_user()
    address = request.form.get("address", "").strip()

    if not address:
        return render_template("report.html", error="Enter a token address.")

    if not is_pro(user):
        if free_analyses_remaining(user) <= 0:
            flash("You’ve hit the free scan limit. Upgrade to Pro for unlimited scans.")
            return redirect(url_for("pricing"))

    chain = detect_chain(address)

    if chain == "unknown":
        return render_template("report.html", error="Invalid address.")

    result = analyze_token(address, chain)
    tradingview_symbol = detect_tradingview_symbol(chain, result["facts"]["symbol"])

    save_report(
        user_id=user.id,
        address=address,
        chain=chain,
        facts=result["facts"],
        risk=result["risk"],
        recommendation=result["recommendation"],
        summary=result["summary"]
    )

    if not is_pro(user):
        increment_free_analysis(user)

    show_premium = is_pro(user)

    return render_template(
        "report.html",
        address=address,
        chain=chain,
        dex_data=result["dex_data"],
        birdeye_data=result["birdeye_data"],
        rug_data=result["rug_data"] if show_premium else None,
        risk=result["risk"],
        recommendation=result["recommendation"] if show_premium else None,
        momentum_score=result["momentum_score"],
        phase=result["phase"],
        hype_type=result["hype_type"] if show_premium else None,
        summary=result["summary"],
        token_name=result["facts"]["token_name"],
        symbol=result["facts"]["symbol"],
        price_usd=result["facts"]["price_usd"],
        signals=None,
        show_premium=show_premium,
        tradingview_symbol=tradingview_symbol,
        error=None
    )


@app.route("/watchlist")
@login_required
def watchlist():
    user = current_user()
    reports = Report.query.filter_by(user_id=user.id).order_by(Report.created_at.desc()).limit(20).all()
    raw_items = WatchlistItem.query.filter_by(user_id=user.id).order_by(WatchlistItem.created_at.desc()).all()

    watchlist_items = []
    for item in raw_items:
        dex_data = get_token_data(item.address)

        current_price_usd = None
        current_token_name = item.token_name
        current_symbol = item.symbol

        if dex_data.get("success"):
            current_price_usd = dex_data.get("price_usd")
            current_token_name = dex_data.get("token_name") or item.token_name
            current_symbol = dex_data.get("symbol") or item.symbol

        watchlist_items.append({
            "id": item.id,
            "address": item.address,
            "chain": item.chain,
            "token_name": current_token_name,
            "symbol": current_symbol,
            "current_price_usd": current_price_usd,
            "last_risk_score": item.last_risk_score,
            "last_recommendation": item.last_recommendation,
            "last_momentum_score": item.last_momentum_score,
            "last_phase": item.last_phase,
            "last_hype_type": item.last_hype_type,
            "created_at": item.created_at
        })

    return render_template(
        "watchlist.html",
        reports=reports,
        watchlist_items=watchlist_items,
        show_premium=is_pro(user)
    )


@app.route("/signals")
@login_required
def signals_dashboard():
    user = current_user()

    if not premium_required():
        return redirect(url_for("pricing"))

    raw_items = WatchlistItem.query.filter_by(user_id=user.id).order_by(WatchlistItem.created_at.desc()).all()
    signal_items = []

    for item in raw_items:
        result = analyze_token(item.address, item.chain)
        new_price = str(result["facts"]["price_usd"]) if result["facts"]["price_usd"] is not None else ""

        signals = build_signals(
            old_risk_score=item.last_risk_score,
            new_risk_score=result["risk"]["score"],
            old_recommendation=item.last_recommendation,
            new_recommendation=result["recommendation"],
            old_price=item.last_price_usd,
            new_price=new_price,
            old_phase=item.last_phase,
            new_phase=result["phase"],
            old_momentum=item.last_momentum_score,
            new_momentum=result["momentum_score"],
            old_hype_type=item.last_hype_type,
            new_hype_type=result["hype_type"],
        )

        item.token_name = result["facts"]["token_name"]
        item.symbol = result["facts"]["symbol"]
        item.last_price_usd = new_price
        item.last_risk_score = result["risk"]["score"]
        item.last_recommendation = result["recommendation"]
        item.last_momentum_score = result["momentum_score"]
        item.last_phase = result["phase"]
        item.last_hype_type = result["hype_type"]

        save_report(
            user_id=user.id,
            address=item.address,
            chain=item.chain,
            facts=result["facts"],
            risk=result["risk"],
            recommendation=result["recommendation"],
            summary=result["summary"]
        )

        signal_items.append({
            "id": item.id,
            "token_name": result["facts"]["token_name"],
            "symbol": result["facts"]["symbol"],
            "address": item.address,
            "chain": item.chain,
            "current_price_usd": result["facts"]["price_usd"],
            "risk_score": result["risk"]["score"],
            "recommendation": result["recommendation"],
            "momentum_score": result["momentum_score"],
            "phase": result["phase"],
            "hype_type": result["hype_type"],
            "signals": signals
        })

    db.session.commit()

    return render_template("signals.html", signal_items=signal_items)


@app.route("/add_to_watchlist", methods=["POST"])
@login_required
def add_to_watchlist():
    user = current_user()

    address = request.form.get("address", "").strip()
    chain = request.form.get("chain", "").strip()
    token_name = request.form.get("token_name", "").strip()
    symbol = request.form.get("symbol", "").strip()
    price_usd = request.form.get("price_usd", "").strip()
    risk_score = request.form.get("risk_score", "").strip()
    recommendation = request.form.get("recommendation", "").strip()
    momentum_score = request.form.get("momentum_score", "").strip()
    phase = request.form.get("phase", "").strip()
    hype_type = request.form.get("hype_type", "").strip()

    if not address or not chain:
        return redirect(url_for("watchlist"))

    existing = WatchlistItem.query.filter_by(user_id=user.id, address=address).first()
    if not existing:
        item = WatchlistItem(
            user_id=user.id,
            address=address,
            chain=chain,
            token_name=token_name,
            symbol=symbol,
            last_price_usd=price_usd,
            last_risk_score=int(risk_score) if risk_score else None,
            last_recommendation=recommendation,
            last_momentum_score=int(momentum_score) if momentum_score else None,
            last_phase=phase,
            last_hype_type=hype_type
        )
        db.session.add(item)
        db.session.commit()

    flash("Token saved to watchlist.")
    return redirect(url_for("watchlist"))


@app.route("/reanalyze/<int:item_id>")
@login_required
def reanalyze(item_id):
    user = current_user()
    item = WatchlistItem.query.filter_by(id=item_id, user_id=user.id).first_or_404()

    result = analyze_token(item.address, item.chain)
    new_price = str(result["facts"]["price_usd"]) if result["facts"]["price_usd"] is not None else ""
    tradingview_symbol = detect_tradingview_symbol(item.chain, result["facts"]["symbol"])

    signals = build_signals(
        old_risk_score=item.last_risk_score,
        new_risk_score=result["risk"]["score"],
        old_recommendation=item.last_recommendation,
        new_recommendation=result["recommendation"],
        old_price=item.last_price_usd,
        new_price=new_price,
        old_phase=item.last_phase,
        new_phase=result["phase"],
        old_momentum=item.last_momentum_score,
        new_momentum=result["momentum_score"],
        old_hype_type=item.last_hype_type,
        new_hype_type=result["hype_type"],
    )

    item.token_name = result["facts"]["token_name"]
    item.symbol = result["facts"]["symbol"]
    item.last_price_usd = new_price
    item.last_risk_score = result["risk"]["score"]
    item.last_recommendation = result["recommendation"]
    item.last_momentum_score = result["momentum_score"]
    item.last_phase = result["phase"]
    item.last_hype_type = result["hype_type"]
    db.session.commit()

    save_report(
        user_id=user.id,
        address=item.address,
        chain=item.chain,
        facts=result["facts"],
        risk=result["risk"],
        recommendation=result["recommendation"],
        summary=result["summary"]
    )

    show_premium = is_pro(user)

    return render_template(
        "report.html",
        address=item.address,
        chain=item.chain,
        dex_data=result["dex_data"],
        birdeye_data=result["birdeye_data"],
        rug_data=result["rug_data"] if show_premium else None,
        risk=result["risk"],
        recommendation=result["recommendation"] if show_premium else None,
        momentum_score=result["momentum_score"],
        phase=result["phase"],
        hype_type=result["hype_type"] if show_premium else None,
        summary=result["summary"],
        token_name=result["facts"]["token_name"],
        symbol=result["facts"]["symbol"],
        price_usd=result["facts"]["price_usd"],
        signals=signals if show_premium else None,
        show_premium=show_premium,
        tradingview_symbol=tradingview_symbol,
        error=None
    )


@app.route("/delete_watchlist/<int:item_id>", methods=["POST"])
@login_required
def delete_watchlist(item_id):
    user = current_user()
    item = WatchlistItem.query.filter_by(id=item_id, user_id=user.id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    flash("Watchlist item removed.")
    return redirect(url_for("watchlist"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=7700)
