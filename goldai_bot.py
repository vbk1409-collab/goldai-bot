"""
GoldAI Telegram Bot
====================
Free tier — no AI API, no cost ever.

Features:
- /check SELL 4355 4375    → Confidence breakdown
- /check BUY 4280 4258
- /scan                    → Top setups right now
- /levels                  → Key levels vs current price
- /pos 1000 2 4355 4375    → Position size calculator
- /help                    → All commands

Deploy: Railway / Render (free tier)
Cost:   $0/month
"""

import logging
import time
import math
import requests
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

# ================================================================
# CONFIG — fill in your bot token
# ================================================================

BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # From @BotFather

# ================================================================
# LOGGING
# ================================================================

logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ================================================================
# PRICE CACHE — fetch once per 30 seconds, serve all users
# ================================================================

_price_cache = {"price": None, "history": [], "ts": 0}

def get_gold_price():
    """Fetch XAU/USD price — cached 30 seconds"""
    now = time.time()
    if now - _price_cache["ts"] < 30 and _price_cache["price"]:
        return _price_cache["price"], _price_cache["history"]

    try:
        # Yahoo Finance — free, no key needed
        url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
        params = {
            "interval": "1h",
            "range": "5d",
            "includePrePost": False,
        }
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=8)
        data = r.json()

        result = data["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]

        current = closes[-1]
        _price_cache["price"] = round(current, 2)
        _price_cache["history"] = closes[-50:]  # Last 50 candles for indicators
        _price_cache["ts"] = now

        return _price_cache["price"], _price_cache["history"]

    except Exception as e:
        log.error(f"Price fetch error: {e}")
        # Return cached if available
        if _price_cache["price"]:
            return _price_cache["price"], _price_cache["history"]
        return None, []

# ================================================================
# INDICATORS — calculated locally, no API
# ================================================================

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas[-period:]]
    losses = [abs(min(d, 0)) for d in deltas[-period:]]
    ag = sum(gains) / period + 1e-9
    al = sum(losses) / period + 1e-9
    return round(100 - 100 / (1 + ag/al), 1)

def calc_ma(closes, period):
    if len(closes) < period:
        return closes[-1]
    return round(sum(closes[-period:]) / period, 2)

def calc_atr(closes, period=14):
    if len(closes) < period + 1:
        return 10.0
    trs = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
    return round(sum(trs[-period:]) / period, 2)

def calc_bb(closes, period=20, mult=2.0):
    if len(closes) < period:
        return closes[-1] * 1.01, closes[-1], closes[-1] * 0.99
    window = closes[-period:]
    mid = sum(window) / period
    std = math.sqrt(sum((x - mid)**2 for x in window) / period)
    return round(mid + mult*std, 2), round(mid, 2), round(mid - mult*std, 2)

# ================================================================
# KEY LEVELS — XAU/USD
# ================================================================

KEY_LEVELS = [
    (4200, "Major support — long-term base"),
    (4250, "Support zone"),
    (4280, "Psychological level"),
    (4300, "Support cluster"),
    (4320, "Minor support"),
    (4350, "Recent structure low"),
    (4380, "Flip zone"),
    (4400, "Psychological — major"),
    (4420, "Intraday support"),
    (4440, "Watch zone"),
    (4463, "Structure level"),
    (4488, "Key resistance — tested multiple times"),
    (4508, "Resistance zone"),
    (4527, "Spike high — strong resistance"),
    (4550, "Resistance cluster"),
    (4595, "Recent swing high"),
]

def nearest_levels(price, n=3):
    """Get n nearest support and resistance levels"""
    supports    = [(l, d) for l, d in KEY_LEVELS if l < price]
    resistances = [(l, d) for l, d in KEY_LEVELS if l > price]
    supports    = sorted(supports, key=lambda x: price - x[0])[:n]
    resistances = sorted(resistances, key=lambda x: x[0] - price)[:n]
    return supports, resistances

def at_key_level(price, threshold=8):
    """Check if price is near a key level"""
    for level, desc in KEY_LEVELS:
        if abs(price - level) <= threshold:
            return level, desc
    return None, None

# ================================================================
# CONFIDENCE ENGINE
# ================================================================

def calc_confidence(direction, entry, sl, current_price, closes):
    """
    Calculate confidence score for a trade idea.
    Returns (score, breakdown_list, suggestions_list)
    """
    score = 0
    breakdown = []
    suggestions = []

    if not closes or len(closes) < 20:
        return 50, [("⚠️ Limited data", 0)], []

    # Indicators
    rsi    = calc_rsi(closes)
    ma20   = calc_ma(closes, 20)
    ma50   = calc_ma(closes, 50)
    atr    = calc_atr(closes)
    bb_u, bb_m, bb_l = calc_bb(closes)
    sl_dist = abs(entry - sl)
    is_sell = direction.upper() == "SELL"

    # ── TREND (most important) ──────────────────────
    if is_sell:
        if ma20 < ma50:
            score += 20
            breakdown.append(("✅ Downtrend — MA20 below MA50", +20))
        else:
            score -= 20
            breakdown.append(("❌ Counter-trend — MA20 above MA50", -20))
            suggestions.append("Consider SELL only after MA20 crosses below MA50")
    else:
        if ma20 > ma50:
            score += 20
            breakdown.append(("✅ Uptrend — MA20 above MA50", +20))
        else:
            score -= 20
            breakdown.append(("❌ Counter-trend — MA20 below MA50", -20))
            suggestions.append("Consider BUY only after MA20 crosses above MA50")

    # ── ENTRY QUALITY ───────────────────────────────
    level, level_desc = at_key_level(entry, threshold=10)
    if level:
        score += 20
        breakdown.append((f"✅ Entry near key level {level}", +20))
    else:
        nearest_sup, nearest_res = nearest_levels(entry)
        if nearest_res and nearest_sup:
            dist_res = nearest_res[0][0] - entry
            dist_sup = entry - nearest_sup[0][0]
            if is_sell and dist_res < 15:
                score += 10
                breakdown.append((f"✅ Entry close to resistance {nearest_res[0][0]}", +10))
            elif not is_sell and dist_sup < 15:
                score += 10
                breakdown.append((f"✅ Entry close to support {nearest_sup[0][0]}", +10))
            else:
                score -= 10
                breakdown.append(("⚠️ Entry in no-man's land", -10))
                if is_sell:
                    suggestions.append(f"Better SELL entry: {nearest_res[0][0]} (resistance)")
                else:
                    suggestions.append(f"Better BUY entry: {nearest_sup[0][0]} (support)")

    # ── RSI CONFIRMATION ────────────────────────────
    if is_sell:
        if rsi > 65:
            score += 15
            breakdown.append((f"✅ RSI {rsi} — overbought confirms SELL", +15))
        elif rsi > 55:
            score += 5
            breakdown.append((f"⚠️ RSI {rsi} — slightly elevated", +5))
        elif rsi < 40:
            score -= 15
            breakdown.append((f"❌ RSI {rsi} — oversold, risky to SELL", -15))
            suggestions.append("RSI oversold — wait for bounce before SELL")
        else:
            breakdown.append((f"➖ RSI {rsi} — neutral", 0))
    else:
        if rsi < 35:
            score += 15
            breakdown.append((f"✅ RSI {rsi} — oversold confirms BUY", +15))
        elif rsi < 45:
            score += 5
            breakdown.append((f"⚠️ RSI {rsi} — slightly oversold", +5))
        elif rsi > 60:
            score -= 15
            breakdown.append((f"❌ RSI {rsi} — overbought, risky to BUY", -15))
            suggestions.append("RSI overbought — wait for pullback before BUY")
        else:
            breakdown.append((f"➖ RSI {rsi} — neutral", 0))

    # ── SL QUALITY ──────────────────────────────────
    if sl_dist < atr * 0.5:
        score -= 15
        breakdown.append((f"❌ SL too tight ({sl_dist:.1f} pip vs ATR {atr:.1f})", -15))
        better_sl = round(entry + atr * 0.8, 1) if is_sell else round(entry - atr * 0.8, 1)
        suggestions.append(f"Widen SL to {better_sl} for better placement (0.8× ATR)")
    elif sl_dist <= atr * 2.0:
        score += 15
        breakdown.append((f"✅ SL well-placed ({sl_dist:.1f} pip)", +15))
    else:
        score += 5
        breakdown.append((f"⚠️ SL wide ({sl_dist:.1f} pip) — reduces position size", +5))

    # ── RR RATIO ────────────────────────────────────
    tp1_dist = sl_dist * 1.5
    tp2_dist = sl_dist * 2.5
    tp1 = round(entry - tp1_dist, 1) if is_sell else round(entry + tp1_dist, 1)
    tp2 = round(entry - tp2_dist, 1) if is_sell else round(entry + tp2_dist, 1)

    if sl_dist > 0:
        rr = tp2_dist / sl_dist
        if rr >= 2.5:
            score += 15
            breakdown.append((f"✅ Excellent RR 1:{rr:.1f}", +15))
        elif rr >= 1.5:
            score += 10
            breakdown.append((f"✅ Good RR 1:{rr:.1f}", +10))
        else:
            score -= 10
            breakdown.append((f"❌ Poor RR 1:{rr:.1f}", -10))
            suggestions.append(f"Extend TP to {tp2} for 1:2.5 ratio")
    else:
        rr = 2.5

    # ── BB CONFIRMATION ─────────────────────────────
    if is_sell and entry > bb_u:
        score += 10
        breakdown.append(("✅ Entry above Bollinger upper band", +10))
    elif not is_sell and entry < bb_l:
        score += 10
        breakdown.append(("✅ Entry below Bollinger lower band", +10))

    # ── PRICE VS ENTRY ──────────────────────────────
    dist_from_current = abs(entry - current_price)
    if dist_from_current > 50:
        score -= 10
        breakdown.append((f"⚠️ Entry {dist_from_current:.0f} pip from current price", -10))
        suggestions.append("Entry is far from current price — use limit order")

    # Clamp score
    score = max(10, min(95, score))

    return score, breakdown, suggestions, tp1, tp2, rr

# ================================================================
# FORMAT MESSAGES
# ================================================================

def conf_bar(pct):
    filled = round(pct / 10)
    return "█" * filled + "░" * (10 - filled)

def conf_color(pct):
    if pct >= 75: return "🟢"
    if pct >= 55: return "🟡"
    return "🔴"

def format_check_result(direction, entry, sl, score, breakdown, suggestions, tp1, tp2, rr, current_price):
    em = "📉" if direction.upper() == "SELL" else "📈"
    color = conf_color(score)

    lines = []
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🤖 GoldAI | {em} {direction.upper()} @ {entry}")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"CONFIDENCE  {conf_bar(score)}  {score}%  {color}")
    lines.append(f"")

    for reason, pts in breakdown:
        lines.append(f"{reason}")

    lines.append(f"")
    lines.append(f"SL: {sl} | TP1: {tp1} | TP2: {tp2}")
    lines.append(f"RR: 1:{rr:.1f} (to TP2)")
    lines.append(f"Current: {current_price}")

    if suggestions:
        lines.append(f"")
        lines.append(f"💡 Suggestions:")
        for s in suggestions:
            lines.append(f"   • {s}")

    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Your idea. Your decision. Always use SL.")

    return "\n".join(lines)

def format_scan_result(setups):
    if not setups:
        return "📊 No high-confidence setups right now.\nMarket conditions unclear — wait for better opportunity."

    lines = ["━━━━━━━━━━━━━━━━━━━━━━",
             "🤖 GoldAI Market Scan",
             "━━━━━━━━━━━━━━━━━━━━━━"]

    for s in setups:
        em = "📉" if s['dir'] == "SELL" else "📈"
        lines.append(f"{em} {s['dir']} @ {s['entry']}")
        lines.append(f"   CONF {conf_bar(s['score'])} {s['score']}%")
        lines.append(f"   SL: {s['sl']} | TP: {s['tp2']} | RR 1:{s['rr']:.1f}")
        lines.append(f"   {s['reason']}")
        lines.append(f"")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("Use /check to analyze your own idea.")
    return "\n".join(lines)

# ================================================================
# SCAN — find top setups automatically
# ================================================================

def auto_scan(current_price, closes):
    """Find high-confidence setups based on key levels"""
    setups = []
    supports, resistances = nearest_levels(current_price, n=2)

    # Check SELL setups at resistance
    for level, desc in resistances:
        if abs(level - current_price) < 30:  # Within 30 pip
            sl = round(level + 20, 1)
            score, breakdown, suggestions, tp1, tp2, rr = calc_confidence(
                "SELL", level, sl, current_price, closes
            )
            if score >= 60:
                setups.append({
                    "dir": "SELL", "entry": level, "sl": sl,
                    "tp1": tp1, "tp2": tp2, "rr": rr,
                    "score": score, "reason": desc
                })

    # Check BUY setups at support
    for level, desc in supports:
        if abs(current_price - level) < 30:
            sl = round(level - 20, 1)
            score, breakdown, suggestions, tp1, tp2, rr = calc_confidence(
                "BUY", level, sl, current_price, closes
            )
            if score >= 60:
                setups.append({
                    "dir": "BUY", "entry": level, "sl": sl,
                    "tp1": tp1, "tp2": tp2, "rr": rr,
                    "score": score, "reason": desc
                })

    return sorted(setups, key=lambda x: x['score'], reverse=True)[:3]

# ================================================================
# COMMAND HANDLERS
# ================================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *GoldAI Bot* — Free Confidence Checker\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Check if your trade idea makes sense.\n\n"
        "*Commands:*\n"
        "/check SELL 4355 4375 — analyze your idea\n"
        "/check BUY 4280 4258\n"
        "/scan — top setups right now\n"
        "/levels — key support & resistance\n"
        "/pos 1000 2 4355 4375 — position size\n"
        "/help — all commands\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "_Your idea. Your decision. Always use SL._"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /check SELL 4355 4375
    /check BUY 4280 4258
    """
    args = ctx.args
    if len(args) < 3:
        await update.message.reply_text(
            "Usage: /check SELL 4355 4375\n"
            "       /check BUY 4280 4258\n\n"
            "Format: /check [BUY/SELL] [entry] [stop_loss]"
        )
        return

    try:
        direction = args[0].upper()
        entry     = float(args[1])
        sl        = float(args[2])

        if direction not in ("BUY", "SELL"):
            await update.message.reply_text("Direction must be BUY or SELL")
            return

        # Validate
        if direction == "SELL" and sl <= entry:
            await update.message.reply_text("For SELL: SL must be above entry price")
            return
        if direction == "BUY" and sl >= entry:
            await update.message.reply_text("For BUY: SL must be below entry price")
            return

    except ValueError:
        await update.message.reply_text("Invalid numbers. Example: /check SELL 4355 4375")
        return

    # Fetch price
    await update.message.reply_text("Analyzing... ⏳")
    current_price, closes = get_gold_price()

    if not current_price:
        await update.message.reply_text("⚠️ Could not fetch price data. Try again in 30 seconds.")
        return

    # Calculate
    score, breakdown, suggestions, tp1, tp2, rr = calc_confidence(
        direction, entry, sl, current_price, closes
    )

    result = format_check_result(
        direction, entry, sl, score, breakdown,
        suggestions, tp1, tp2, rr, current_price
    )

    await update.message.reply_text(result)


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Auto scan for top setups"""
    await update.message.reply_text("Scanning market... ⏳")

    current_price, closes = get_gold_price()
    if not current_price:
        await update.message.reply_text("⚠️ Could not fetch price data.")
        return

    setups = auto_scan(current_price, closes)
    result = format_scan_result(setups)
    await update.message.reply_text(result)


async def cmd_levels(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show key levels vs current price"""
    current_price, _ = get_gold_price()
    if not current_price:
        await update.message.reply_text("⚠️ Could not fetch price data.")
        return

    supports, resistances = nearest_levels(current_price, n=4)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📍 XAU/USD Key Levels",
        f"Current: {current_price}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🔴 RESISTANCE:",
    ]
    for level, desc in reversed(resistances):
        dist = round(level - current_price, 1)
        lines.append(f"   {level}  (+{dist} pip)  {desc}")

    lines.append(f"")
    lines.append(f"📍 YOU ARE HERE → {current_price}")
    lines.append(f"")
    lines.append("🟢 SUPPORT:")
    for level, desc in supports:
        dist = round(current_price - level, 1)
        lines.append(f"   {level}  (-{dist} pip)  {desc}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    await update.message.reply_text("\n".join(lines))


async def cmd_pos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /pos 1000 2 4355 4375
    Position size calculator
    balance risk% entry sl
    """
    args = ctx.args
    if len(args) < 4:
        await update.message.reply_text(
            "Usage: /pos [balance] [risk%] [entry] [stop_loss]\n"
            "Example: /pos 1000 2 4355 4375\n"
            "(balance=1000, risk=2%, entry=4355, sl=4375)"
        )
        return

    try:
        balance = float(args[0])
        risk_pct = float(args[1])
        entry    = float(args[2])
        sl       = float(args[3])
    except ValueError:
        await update.message.reply_text("Invalid numbers. Example: /pos 1000 2 4355 4375")
        return

    sl_dist    = abs(entry - sl)
    risk_amt   = balance * risk_pct / 100
    pip_value  = 10  # $10 per pip per 1 lot (XAU/USD)
    lot        = max(0.01, round(risk_amt / (sl_dist * pip_value), 2))
    actual_pip = lot * pip_value

    # TP targets
    tps = []
    for mult, label in [(1.0,"1:1"), (1.5,"1:1.5"), (2.0,"1:2"), (3.0,"1:3")]:
        tp_dist = sl_dist * mult
        is_sell = entry > sl
        tp_price = round(entry - tp_dist if is_sell else entry + tp_dist, 1)
        profit = round(risk_amt * mult, 2)
        tps.append((label, tp_price, profit))

    direction = "SELL" if entry > sl else "BUY"
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Position Size | {direction} @ {entry}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Balance:    ${balance:,.2f}",
        f"Risk:       {risk_pct}% = ${risk_amt:.2f}",
        f"SL:         {sl} ({sl_dist:.1f} pip)",
        f"",
        f"📊 LOT SIZE:  {lot} oz",
        f"Pip value:   ${actual_pip:.2f}/pip",
        f"",
        "🎯 Take Profit Targets:",
    ]
    for label, tp, profit in tps:
        lines.append(f"   {label}  →  {tp}  (+${profit:.2f})")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("Never risk more than 2% per trade.")
    await update.message.reply_text("\n".join(lines))


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *GoldAI Bot — Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "*Confidence Check:*\n"
        "`/check SELL 4355 4375`\n"
        "`/check BUY 4280 4258`\n"
        "_→ Is your trade idea solid?_\n\n"
        "*Market Scan:*\n"
        "`/scan`\n"
        "_→ Top setups right now_\n\n"
        "*Key Levels:*\n"
        "`/levels`\n"
        "_→ Support & resistance map_\n\n"
        "*Position Size:*\n"
        "`/pos 1000 2 4355 4375`\n"
        "_→ balance risk% entry sl_\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "_Data: XAU/USD spot price_\n"
        "_Updated every 30 seconds_"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')


async def cmd_unknown(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Unknown command. Type /help to see all commands."
    )

# ================================================================
# MAIN
# ================================================================

def main():
    log.info("GoldAI Bot starting...")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("check",   cmd_check))
    app.add_handler(CommandHandler("scan",    cmd_scan))
    app.add_handler(CommandHandler("levels",  cmd_levels))
    app.add_handler(CommandHandler("pos",     cmd_pos))
    app.add_handler(MessageHandler(filters.COMMAND, cmd_unknown))

    log.info("Bot running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
