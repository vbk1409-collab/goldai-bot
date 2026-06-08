"""
GoldAI Telegram Bot v2
======================
Compatible: Python 3.11-3.14, python-telegram-bot 22.7
Cost: $0/month
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

# ================================================================
# CONFIG
# ================================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ================================================================
# PRICE CACHE
# ================================================================

_cache = {"price": None, "history": [], "ts": 0}

def get_gold_price():
    now = time.time()
    if now - _cache["ts"] < 30 and _cache["price"]:
        return _cache["price"], _cache["history"]
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
        r = requests.get(url, params={"interval":"1h","range":"5d"},
                        headers={"User-Agent":"Mozilla/5.0"}, timeout=8)
        data = r.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        _cache["price"] = round(closes[-1], 2)
        _cache["history"] = closes[-50:]
        _cache["ts"] = now
        return _cache["price"], _cache["history"]
    except Exception as e:
        log.error(f"Price fetch error: {e}")
        return _cache["price"], _cache["history"]

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
    if len(closes) < p: return closes[-1]
    return round(sum(closes[-p:])/p, 2)

def calc_atr(closes, p=14):
    if len(closes) < p+1: return 10.0
    trs = [abs(closes[i]-closes[i-1]) for i in range(1,len(closes))]
    return round(sum(trs[-p:])/p, 2)

def calc_bb(closes, p=20, m=2.0):
    if len(closes) < p: return closes[-1]*1.01, closes[-1], closes[-1]*0.99
    w = closes[-p:]
    mid = sum(w)/p
    std = math.sqrt(sum((x-mid)**2 for x in w)/p)
    return round(mid+m*std,2), round(mid,2), round(mid-m*std,2)

# ================================================================
# KEY LEVELS
# ================================================================

KEY_LEVELS = [
    (4200,"Major support — long-term base"),
    (4250,"Support zone"),
    (4280,"Psychological level"),
    (4300,"Support cluster"),
    (4320,"Minor support"),
    (4350,"Recent structure level"),
    (4380,"Flip zone"),
    (4400,"Psychological — major"),
    (4420,"Intraday support"),
    (4440,"Watch zone"),
    (4463,"Structure level"),
    (4488,"Key resistance — tested multiple times"),
    (4508,"Resistance zone"),
    (4527,"Spike high — strong resistance"),
    (4550,"Resistance cluster"),
    (4595,"Recent swing high"),
]

def nearest_levels(price, n=3):
    sup = sorted([(l,d) for l,d in KEY_LEVELS if l<price], key=lambda x:price-x[0])[:n]
    res = sorted([(l,d) for l,d in KEY_LEVELS if l>price], key=lambda x:x[0]-price)[:n]
    return sup, res

def at_key_level(price, thr=10):
    for level, desc in KEY_LEVELS:
        if abs(price-level) <= thr:
            return level, desc
    return None, None

# ================================================================
# CONFIDENCE ENGINE
# ================================================================

def calc_confidence(direction, entry, sl, current_price, closes):
    if not closes or len(closes) < 20:
        return 50, [("⚠️ Limited data",0)], [], entry, entry, 1.0

    score = 0
    breakdown = []
    suggestions = []
    is_sell = direction.upper() == "SELL"

    rsi  = calc_rsi(closes)
    ma20 = calc_ma(closes, 20)
    ma50 = calc_ma(closes, 50)
    atr  = calc_atr(closes)
    bb_u, bb_m, bb_l = calc_bb(closes)
    sl_dist = abs(entry - sl)

    # Trend
    if is_sell:
        if ma20 < ma50:
            score += 20; breakdown.append(("✅ Downtrend — MA20 below MA50", +20))
        else:
            score -= 20; breakdown.append(("❌ Counter-trend — MA20 above MA50", -20))
            suggestions.append("Wait for MA20 to cross below MA50")
    else:
        if ma20 > ma50:
            score += 20; breakdown.append(("✅ Uptrend — MA20 above MA50", +20))
        else:
            score -= 20; breakdown.append(("❌ Counter-trend — MA20 below MA50", -20))
            suggestions.append("Wait for MA20 to cross above MA50")

    # Entry quality
    level, level_desc = at_key_level(entry, 10)
    if level:
        score += 20; breakdown.append((f"✅ Entry near key level {level}", +20))
    else:
        sup, res = nearest_levels(entry)
        if is_sell and res and res[0][0]-entry < 15:
            score += 10; breakdown.append((f"✅ Near resistance {res[0][0]}", +10))
        elif not is_sell and sup and entry-sup[0][0] < 15:
            score += 10; breakdown.append((f"✅ Near support {sup[0][0]}", +10))
        else:
            score -= 10; breakdown.append(("⚠️ Entry in no-man's land", -10))
            if is_sell and res:
                suggestions.append(f"Better entry: {res[0][0]} (resistance)")
            elif not is_sell and sup:
                suggestions.append(f"Better entry: {sup[0][0]} (support)")

    # RSI
    if is_sell:
        if rsi > 65:   score += 15; breakdown.append((f"✅ RSI {rsi} overbought — confirms SELL", +15))
        elif rsi > 55: score += 5;  breakdown.append((f"⚠️ RSI {rsi} slightly elevated", +5))
        elif rsi < 40: score -= 15; breakdown.append((f"❌ RSI {rsi} oversold — risky SELL", -15)); suggestions.append("Wait for RSI to recover above 50")
        else:          breakdown.append((f"➖ RSI {rsi} neutral", 0))
    else:
        if rsi < 35:   score += 15; breakdown.append((f"✅ RSI {rsi} oversold — confirms BUY", +15))
        elif rsi < 45: score += 5;  breakdown.append((f"⚠️ RSI {rsi} slightly oversold", +5))
        elif rsi > 60: score -= 15; breakdown.append((f"❌ RSI {rsi} overbought — risky BUY", -15)); suggestions.append("Wait for RSI pullback below 50")
        else:          breakdown.append((f"➖ RSI {rsi} neutral", 0))

    # SL quality
    if sl_dist < atr*0.5:
        score -= 15; breakdown.append((f"❌ SL too tight ({sl_dist:.1f} pip, ATR={atr:.1f})", -15))
        better = round(entry+atr*0.8,1) if is_sell else round(entry-atr*0.8,1)
        suggestions.append(f"Widen SL to {better} (0.8× ATR)")
    elif sl_dist <= atr*2.0:
        score += 15; breakdown.append((f"✅ SL well-placed ({sl_dist:.1f} pip)", +15))
    else:
        score += 5; breakdown.append((f"⚠️ SL wide ({sl_dist:.1f} pip)", +5))

    # RR
    tp1 = round(entry - sl_dist*1.5,1) if is_sell else round(entry + sl_dist*1.5,1)
    tp2 = round(entry - sl_dist*2.5,1) if is_sell else round(entry + sl_dist*2.5,1)
    rr  = 2.5
    if sl_dist > 0:
        rr = round(sl_dist*2.5/sl_dist, 1)
        if rr >= 2.5:   score += 15; breakdown.append((f"✅ Excellent RR 1:{rr}", +15))
        elif rr >= 1.5: score += 10; breakdown.append((f"✅ Good RR 1:{rr}", +10))
        else:           score -= 10; breakdown.append((f"❌ Poor RR 1:{rr}", -10)); suggestions.append(f"Extend TP to {tp2} for better RR")

    # BB
    if is_sell and entry > bb_u:
        score += 10; breakdown.append(("✅ Entry above BB upper band", +10))
    elif not is_sell and entry < bb_l:
        score += 10; breakdown.append(("✅ Entry below BB lower band", +10))

    score = max(10, min(95, score))
    return score, breakdown, suggestions, tp1, tp2, rr

# ================================================================
# HELPERS
# ================================================================

def conf_bar(pct):
    f = round(pct/10)
    return "█"*f + "░"*(10-f)

def conf_emoji(pct):
    return "🟢" if pct>=75 else "🟡" if pct>=55 else "🔴"

def format_result(direction, entry, sl, score, breakdown, suggestions, tp1, tp2, rr, price):
    em = "📉" if direction.upper()=="SELL" else "📈"
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🤖 GoldAI | {em} {direction.upper()} @ {entry}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"CONFIDENCE  {conf_bar(score)}  {score}%  {conf_emoji(score)}",
        "",
    ]
    for reason, _ in breakdown:
        lines.append(reason)
    lines += [
        "",
        f"SL: {sl} | TP1: {tp1} | TP2: {tp2}",
        f"RR: 1:{rr} | Current: {price}",
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
# HANDLERS
# ================================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *GoldAI Bot* — Free Confidence Checker\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "/check SELL 4355 4375\n"
        "/check BUY 4280 4258\n"
        "/scan — top setups now\n"
        "/levels — key levels map\n"
        "/pos 1000 2 4355 4375 — lot size\n"
        "/help — all commands",
        parse_mode='Markdown'
    )

async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 3:
        await update.message.reply_text(
            "Usage:\n/check SELL 4355 4375\n/check BUY 4280 4258"
        ); return
    try:
        direction = args[0].upper()
        entry = float(args[1])
        sl    = float(args[2])
        if direction not in ("BUY","SELL"):
            await update.message.reply_text("Direction must be BUY or SELL"); return
        if direction=="SELL" and sl<=entry:
            await update.message.reply_text("SELL: SL must be ABOVE entry"); return
        if direction=="BUY" and sl>=entry:
            await update.message.reply_text("BUY: SL must be BELOW entry"); return
    except:
        await update.message.reply_text("Invalid. Example: /check SELL 4355 4375"); return

    await update.message.reply_text("Analyzing... ⏳")
    price, closes = get_gold_price()
    if not price:
        await update.message.reply_text("⚠️ Price unavailable. Try again."); return

    score, breakdown, suggestions, tp1, tp2, rr = calc_confidence(
        direction, entry, sl, price, closes
    )
    result = format_result(direction, entry, sl, score, breakdown, suggestions, tp1, tp2, rr, price)
    await update.message.reply_text(result)

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scanning... ⏳")
    price, closes = get_gold_price()
    if not price:
        await update.message.reply_text("⚠️ Price unavailable."); return

    sup, res = nearest_levels(price, 2)
    setups = []

    for level, desc in res:
        if abs(level-price) < 40:
            sl = round(level+20,1)
            score, bd, sg, tp1, tp2, rr = calc_confidence("SELL",level,sl,price,closes)
            if score >= 58:
                setups.append({"dir":"SELL","entry":level,"sl":sl,"tp2":tp2,"rr":rr,"score":score,"reason":desc})

    for level, desc in sup:
        if abs(price-level) < 40:
            sl = round(level-20,1)
            score, bd, sg, tp1, tp2, rr = calc_confidence("BUY",level,sl,price,closes)
            if score >= 58:
                setups.append({"dir":"BUY","entry":level,"sl":sl,"tp2":tp2,"rr":rr,"score":score,"reason":desc})

    if not setups:
        await update.message.reply_text(
            f"📊 No high-confidence setups now.\nCurrent: {price}\n\nMarket unclear — wait."
        ); return

    lines = ["━━━━━━━━━━━━━━━━━━━━━━","🤖 GoldAI Market Scan","━━━━━━━━━━━━━━━━━━━━━━"]
    for s in sorted(setups,key=lambda x:x['score'],reverse=True)[:3]:
        em = "📉" if s['dir']=="SELL" else "📈"
        lines += [
            f"{em} {s['dir']} @ {s['entry']}",
            f"   {conf_bar(s['score'])} {s['score']}%",
            f"   SL:{s['sl']} TP:{s['tp2']} RR:1:{s['rr']}",
            f"   {s['reason']}","",
        ]
    lines.append("Use /check to analyze your own idea.")
    await update.message.reply_text("\n".join(lines))

async def cmd_levels(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    price, _ = get_gold_price()
    if not price:
        await update.message.reply_text("⚠️ Price unavailable."); return

    sup, res = nearest_levels(price, 4)
    lines = ["━━━━━━━━━━━━━━━━━━━━━━",
             f"📍 XAU/USD Key Levels | {price}",
             "━━━━━━━━━━━━━━━━━━━━━━",
             "🔴 RESISTANCE:"]
    for level, desc in reversed(res):
        lines.append(f"   {level}  (+{round(level-price,1)} pip)  {desc}")
    lines += ["", f"📍 NOW → {price}", "", "🟢 SUPPORT:"]
    for level, desc in sup:
        lines.append(f"   {level}  (-{round(price-level,1)} pip)  {desc}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    await update.message.reply_text("\n".join(lines))

async def cmd_pos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 4:
        await update.message.reply_text(
            "Usage: /pos [balance] [risk%] [entry] [sl]\n"
            "Example: /pos 1000 2 4355 4375"
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
        f"Balance:  ${balance:,.2f}",
        f"Risk:     {risk_pct}% = ${risk_amt:.2f}",
        f"SL dist:  {sl_dist:.1f} pip",
        f"",
        f"📊 LOT SIZE: {lot} oz",
        f"Pip value: ${lot*10:.2f}/pip",
        f"",
        "🎯 TP Targets:",
    ]
    for mult, label in [(1,"1:1"),(1.5,"1:1.5"),(2,"1:2"),(3,"1:3")]:
        tp_dist = sl_dist*mult
        tp = round(entry-tp_dist if direction=="SELL" else entry+tp_dist, 1)
        lines.append(f"   {label}  →  {tp}  (+${risk_amt*mult:.2f})")
    lines += ["━━━━━━━━━━━━━━━━━━━━━━","Never risk more than 2% per trade."]
    await update.message.reply_text("\n".join(lines))

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *GoldAI Bot Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "`/check SELL 4355 4375` — confidence check\n"
        "`/check BUY 4280 4258`\n\n"
        "`/scan` — top setups now\n\n"
        "`/levels` — support & resistance\n\n"
        "`/pos 1000 2 4355 4375` — lot size\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "_XAU/USD only | Updated every 30s_",
        parse_mode='Markdown'
    )

async def cmd_unknown(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Unknown command. Type /help")

# ================================================================
# MAIN
# ================================================================

def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[ERROR] BOT_TOKEN not set!")
        print("Set environment variable BOT_TOKEN in Render dashboard")
        return

    log.info("GoldAI Bot starting...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("check",  cmd_check))
    app.add_handler(CommandHandler("scan",   cmd_scan))
    app.add_handler(CommandHandler("levels", cmd_levels))
    app.add_handler(CommandHandler("pos",    cmd_pos))
    app.add_handler(MessageHandler(filters.COMMAND, cmd_unknown))

    log.info("Bot running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
