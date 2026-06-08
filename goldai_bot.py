"""
GoldAI Telegram Bot v3
======================
- No price API — user enters entry price
- Natural language commands
- Full output: confidence + verdict + suggestions
"""

import logging
import time
import math
import os
import requests
from telegram import Update
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler,
    MessageHandler, filters, ContextTypes
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s', level=logging.INFO)
log = logging.getLogger(__name__)

# ================================================================
# PRICE HISTORY CACHE (for indicators only)
# ================================================================

_cache = {"history": [], "ts": 0}

def get_history():
    """Fetch OHLC history for indicator calculation only"""
    now = time.time()
    if now - _cache["ts"] < 300 and _cache["history"]:  # Cache 5 minutes
        return _cache["history"]
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
        r = requests.get(url,
            params={"interval":"1h","range":"10d"},
            headers={"User-Agent":"Mozilla/5.0"}, timeout=8)
        data = r.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        _cache["history"] = closes[-60:]
        _cache["ts"] = now
        return _cache["history"]
    except Exception as e:
        log.error(f"History fetch error: {e}")
        return _cache["history"] or []

# ================================================================
# INDICATORS
# ================================================================

def calc_rsi(closes, p=14):
    if len(closes) < p+1: return 50.0
    d = [closes[i]-closes[i-1] for i in range(1,len(closes))]
    ag = sum(max(x,0) for x in d[-p:])/p + 1e-9
    al = sum(abs(min(x,0)) for x in d[-p:])/p + 1e-9
    return round(100-100/(1+ag/al), 1)

def calc_ma(closes, p):
    if len(closes) < p: return closes[-1] if closes else 4300
    return round(sum(closes[-p:])/p, 2)

def calc_atr(closes, p=14):
    if len(closes) < p+1: return 15.0
    trs = [abs(closes[i]-closes[i-1]) for i in range(1,len(closes))]
    return round(sum(trs[-p:])/p, 2)

# ================================================================
# KEY LEVELS
# ================================================================

KEY_LEVELS = [
    (4200,"Major support"), (4250,"Support zone"),
    (4280,"Psychological"), (4300,"Support cluster"),
    (4320,"Minor support"), (4350,"Structure level"),
    (4380,"Flip zone"),     (4400,"Major psychological"),
    (4420,"Intraday sup"),  (4440,"Watch zone"),
    (4463,"Structure"),     (4488,"Key resistance"),
    (4508,"Resistance"),    (4527,"Spike high"),
    (4550,"Resistance"),    (4595,"Swing high"),
]

def nearest_levels(price, n=3):
    sup = sorted([(l,d) for l,d in KEY_LEVELS if l<price], key=lambda x:price-x[0])[:n]
    res = sorted([(l,d) for l,d in KEY_LEVELS if l>price], key=lambda x:x[0]-price)[:n]
    return sup, res

def at_key_level(price, thr=12):
    for level, desc in KEY_LEVELS:
        if abs(price-level) <= thr:
            return level, desc
    return None, None

# ================================================================
# CONFIDENCE ENGINE
# ================================================================

def calc_confidence(direction, entry, sl):
    closes = get_history()
    is_sell = direction.upper() == "SELL"
    score = 0
    breakdown = []
    suggestions = []

    sl_dist = abs(entry - sl)

    # Use entry as reference for indicators
    if closes:
        rsi  = calc_rsi(closes)
        ma20 = calc_ma(closes, 20)
        ma50 = calc_ma(closes, 50)
        atr  = calc_atr(closes)
    else:
        rsi, ma20, ma50, atr = 50.0, entry, entry, 15.0

    # ── TREND ────────────────────────────────────
    if is_sell:
        if ma20 < ma50:
            score += 20
            breakdown.append(("✅ Downtrend confirmed — MA20 below MA50", +20))
        else:
            score -= 20
            breakdown.append(("❌ Counter-trend — price in uptrend", -20))
            suggestions.append("Consider waiting for downtrend to establish before SELL")
    else:
        if ma20 > ma50:
            score += 20
            breakdown.append(("✅ Uptrend confirmed — MA20 above MA50", +20))
        else:
            score -= 20
            breakdown.append(("❌ Counter-trend — price in downtrend", -20))
            suggestions.append("Consider waiting for uptrend to establish before BUY")

    # ── ENTRY QUALITY ────────────────────────────
    level, level_desc = at_key_level(entry, 12)
    if level:
        score += 20
        breakdown.append((f"✅ Entry at key level {level} — {level_desc}", +20))
    else:
        sup, res = nearest_levels(entry)
        if is_sell and res and res[0][0]-entry < 20:
            score += 12
            breakdown.append((f"✅ Entry close to resistance {res[0][0]}", +12))
        elif not is_sell and sup and entry-sup[0][0] < 20:
            score += 12
            breakdown.append((f"✅ Entry close to support {sup[0][0]}", +12))
        else:
            score -= 12
            breakdown.append(("⚠️ Entry between levels — no clear zone", -12))
            if is_sell and res:
                suggestions.append(f"Better SELL entry: {res[0][0]} ({res[0][1]})")
            elif not is_sell and sup:
                suggestions.append(f"Better BUY entry: {sup[0][0]} ({sup[0][1]})")

    # ── RSI ──────────────────────────────────────
    if is_sell:
        if rsi > 65:
            score += 15
            breakdown.append((f"✅ RSI {rsi} overbought — confirms SELL", +15))
        elif rsi > 55:
            score += 5
            breakdown.append((f"⚠️ RSI {rsi} slightly elevated", +5))
        elif rsi < 40:
            score -= 15
            breakdown.append((f"❌ RSI {rsi} oversold — risky to SELL here", -15))
            suggestions.append("RSI oversold — wait for bounce above 50 before SELL")
        else:
            breakdown.append((f"➖ RSI {rsi} neutral", 0))
    else:
        if rsi < 35:
            score += 15
            breakdown.append((f"✅ RSI {rsi} oversold — confirms BUY", +15))
        elif rsi < 45:
            score += 5
            breakdown.append((f"⚠️ RSI {rsi} slightly oversold", +5))
        elif rsi > 60:
            score -= 15
            breakdown.append((f"❌ RSI {rsi} overbought — risky to BUY here", -15))
            suggestions.append("RSI overbought — wait for pullback below 50 before BUY")
        else:
            breakdown.append((f"➖ RSI {rsi} neutral", 0))

    # ── SL QUALITY ───────────────────────────────
    if sl_dist < atr * 0.6:
        score -= 15
        better_sl = round(entry + atr*0.9, 1) if is_sell else round(entry - atr*0.9, 1)
        breakdown.append((f"❌ SL too tight — {sl_dist:.0f} pip (ATR={atr:.0f})", -15))
        suggestions.append(f"Widen SL to {better_sl} — current SL likely to be hit by noise")
    elif sl_dist <= atr * 2.5:
        score += 15
        breakdown.append((f"✅ SL well-placed — {sl_dist:.0f} pip", +15))
    else:
        score += 5
        breakdown.append((f"⚠️ SL wide — {sl_dist:.0f} pip (reduces lot size)", +5))

    # ── RR ───────────────────────────────────────
    tp1 = round(entry - sl_dist*1.5, 1) if is_sell else round(entry + sl_dist*1.5, 1)
    tp2 = round(entry - sl_dist*2.5, 1) if is_sell else round(entry + sl_dist*2.5, 1)

    if sl_dist >= 5:
        rr = round(sl_dist*2.5/sl_dist, 1)
        if rr >= 2.5:
            score += 15
            breakdown.append((f"✅ Excellent RR 1:{rr}", +15))
        elif rr >= 1.5:
            score += 8
            breakdown.append((f"✅ Acceptable RR 1:{rr}", +8))
        else:
            score -= 12
            breakdown.append((f"❌ Poor RR 1:{rr} — not worth the risk", -12))
            suggestions.append(f"Extend TP to {tp2} for minimum 1:2.5 ratio")
    else:
        rr = 2.5

    score = max(5, min(95, score))
    return score, breakdown, suggestions, tp1, tp2, rr

# ================================================================
# VERDICT
# ================================================================

def get_verdict(score):
    if score >= 75:
        return "🟢 SOLID IDEA", "Setup looks good. Consider entering with proper risk management."
    elif score >= 58:
        return "🟡 BORDERLINE", "Some concerns. Address suggestions before entering."
    elif score >= 40:
        return "🔴 WEAK SETUP", "Multiple issues detected. Better opportunities likely ahead."
    else:
        return "⛔ AVOID", "This setup has too many risks. Do not enter."

# ================================================================
# FORMAT OUTPUT
# ================================================================

def conf_bar(pct):
    f = round(pct/10)
    return "█"*f + "░"*(10-f)

def format_result(direction, entry, sl, score, breakdown, suggestions, tp1, tp2, rr):
    em = "📉" if direction.upper()=="SELL" else "📈"
    verdict_title, verdict_msg = get_verdict(score)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🤖 GoldAI | {em} {direction.upper()} @ {entry}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"CONFIDENCE  {conf_bar(score)}  {score}%  {verdict_title}",
        "",
    ]

    for reason, _ in breakdown:
        lines.append(reason)

    lines += [
        "",
        f"SL: {sl} | TP1: {tp1} | TP2: {tp2}",
        f"RR: 1:{rr}",
        "",
        f"📋 {verdict_msg}",
    ]

    if suggestions:
        lines.append("")
        lines.append("💡 Suggestions:")
        for s in suggestions:
            lines.append(f"   • {s}")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Your idea. Your decision. Always use SL.",
    ]
    return "\n".join(lines)

# ================================================================
# PARSE NATURAL LANGUAGE
# ================================================================

def parse_trade(text):
    """
    Parse: SELL 4380 sl 4400
           BUY 4280 sl 4258
           sell 4380 4400
    Returns (direction, entry, sl) or None
    """
    text = text.strip().upper()
    parts = text.replace("SL", "").split()
    parts = [p for p in parts if p]

    if len(parts) < 3:
        return None

    try:
        direction = parts[0]
        if direction not in ("BUY","SELL"):
            return None
        entry = float(parts[1])
        sl    = float(parts[2])

        if direction == "SELL" and sl <= entry:
            return None
        if direction == "BUY" and sl >= entry:
            return None

        return direction, entry, sl
    except:
        return None

# ================================================================
# HANDLERS
# ================================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 GoldAI Bot — Free Confidence Checker\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Check if your trade idea makes sense.\n\n"
        "How to use:\n"
        "SELL 4380 sl 4400\n"
        "BUY 4280 sl 4258\n\n"
        "Other commands:\n"
        "/scan — top setups now\n"
        "/levels — support & resistance\n"
        "/pos 1000 2 4380 4400 — lot size\n"
        "/help — all commands\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Just type your idea — no command needed"
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle natural language trade ideas"""
    text = update.message.text or ""

    result = parse_trade(text)
    if not result:
        await update.message.reply_text(
            "To check a trade idea, type:\n\n"
            "SELL 4380 sl 4400\n"
            "BUY 4280 sl 4258\n\n"
            "Format: [BUY/SELL] [entry] sl [stop loss]\n\n"
            "For SELL: SL must be above entry\n"
            "For BUY: SL must be below entry"
        )
        return

    direction, entry, sl = result
    await update.message.reply_text("Analyzing... ⏳")

    score, breakdown, suggestions, tp1, tp2, rr = calc_confidence(direction, entry, sl)
    output = format_result(direction, entry, sl, score, breakdown, suggestions, tp1, tp2, rr)
    await update.message.reply_text(output)

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scanning... ⏳")
    closes = get_history()
    if not closes:
        await update.message.reply_text("⚠️ Data unavailable. Try again shortly."); return

    current = closes[-1]
    sup, res = nearest_levels(current, 2)
    setups = []

    for level, desc in res:
        if abs(level-current) < 50:
            sl = round(level+22, 1)
            score, bd, sg, tp1, tp2, rr = calc_confidence("SELL", level, sl)
            if score >= 55:
                setups.append({"dir":"SELL","entry":level,"sl":sl,"tp2":tp2,"rr":rr,"score":score,"desc":desc})

    for level, desc in sup:
        if abs(current-level) < 50:
            sl = round(level-22, 1)
            score, bd, sg, tp1, tp2, rr = calc_confidence("BUY", level, sl)
            if score >= 55:
                setups.append({"dir":"BUY","entry":level,"sl":sl,"tp2":tp2,"rr":rr,"score":score,"desc":desc})

    if not setups:
        await update.message.reply_text(
            f"📊 No strong setups detected near {round(current,1)}\n\n"
            "Market is between key levels.\n"
            "Type your own idea to check it:\n"
            "`SELL 4380 sl 4400`",
            parse_mode='Markdown'
        ); return

    lines = ["━━━━━━━━━━━━━━━━━━━━━━",
             f"🤖 GoldAI Scan | {round(current,1)}",
             "━━━━━━━━━━━━━━━━━━━━━━"]

    for s in sorted(setups, key=lambda x:x['score'], reverse=True)[:3]:
        em = "📉" if s['dir']=="SELL" else "📈"
        v, _ = get_verdict(s['score'])
        lines += [
            f"{em} {s['dir']} @ {s['entry']}  {v}",
            f"   {conf_bar(s['score'])} {s['score']}%",
            f"   SL: {s['sl']} | TP: {s['tp2']} | RR 1:{s['rr']}",
            f"   {s['desc']}", "",
        ]

    lines += ["━━━━━━━━━━━━━━━━━━━━━━",
              "Type to verify: `SELL 4380 sl 4400`"]
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

async def cmd_levels(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    closes = get_history()
    current = round(closes[-1], 1) if closes else "—"

    sup, res = nearest_levels(float(current), 4) if closes else ([], [])
    lines = ["━━━━━━━━━━━━━━━━━━━━━━",
             f"📍 XAU/USD Key Levels",
             f"Reference: {current}",
             "━━━━━━━━━━━━━━━━━━━━━━",
             "🔴 RESISTANCE:"]
    for level, desc in reversed(res):
        dist = round(level-float(current), 1) if closes else "?"
        lines.append(f"   {level}  (+{dist} pip)  {desc}")
    lines += ["", f"📍 REF → {current}", "", "🟢 SUPPORT:"]
    for level, desc in sup:
        dist = round(float(current)-level, 1) if closes else "?"
        lines.append(f"   {level}  (-{dist} pip)  {desc}")
    lines += ["━━━━━━━━━━━━━━━━━━━━━━",
              "Check a level: `SELL 4380 sl 4400`"]
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

async def cmd_pos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 4:
        await update.message.reply_text(
            "Usage: `/pos balance risk% entry sl`\n"
            "Example: `/pos 1000 2 4380 4400`",
            parse_mode='Markdown'
        ); return
    try:
        balance  = float(args[0])
        risk_pct = float(args[1])
        entry    = float(args[2])
        sl       = float(args[3])
    except:
        await update.message.reply_text("Invalid numbers."); return

    sl_dist   = abs(entry-sl)
    risk_amt  = balance*risk_pct/100
    lot       = max(0.01, round(risk_amt/(sl_dist*10), 2))
    direction = "SELL" if entry>sl else "BUY"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Position | {direction} @ {entry}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Balance:   ${balance:,.2f}",
        f"Risk:      {risk_pct}% = ${risk_amt:.2f}",
        f"SL:        {sl} ({sl_dist:.0f} pip away)",
        "",
        f"📊 LOT SIZE:  {lot} oz",
        f"Pip value:    ${lot*10:.2f} per pip",
        "",
        "🎯 Take Profit:",
    ]
    for mult, label in [(1,"1:1"),(1.5,"1:1.5"),(2,"1:2"),(3,"1:3")]:
        tp = round(entry-sl_dist*mult if direction=="SELL" else entry+sl_dist*mult, 1)
        profit = round(risk_amt*mult, 2)
        lines.append(f"   {label}  →  {tp}  (+${profit})")
    lines += ["━━━━━━━━━━━━━━━━━━━━━━",
              "Never risk more than 2% per trade."]
    await update.message.reply_text("\n".join(lines))

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *GoldAI Bot — How to use*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "*Check your trade idea:*\n"
        "`SELL 4380 sl 4400`\n"
        "`BUY 4280 sl 4258`\n"
        "_Just type — no command needed_\n\n"
        "*Commands:*\n"
        "`/scan` — find top setups\n"
        "`/levels` — key support & resistance\n"
        "`/pos 1000 2 4380 4400` — lot size calculator\n"
        "`/help` — this message\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "_XAU/USD Gold only_",
        parse_mode='Markdown'
    )

# ================================================================
# MAIN
# ================================================================

def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[ERROR] Set BOT_TOKEN environment variable")
        return

    log.info("GoldAI Bot v3 starting...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("scan",   cmd_scan))
    app.add_handler(CommandHandler("levels", cmd_levels))
    app.add_handler(CommandHandler("pos",    cmd_pos))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
