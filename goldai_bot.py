"""
GoldAI Telegram Bot v4
======================
- Plain language output — no technical jargon
- Natural input: SELL 4380 sl 4400
- Verdict + Suggestions
- No price API needed
"""

import logging, time, math, os, requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, filters, ContextTypes
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s', level=logging.INFO)
log = logging.getLogger(__name__)

# ================================================================
# HISTORY CACHE — for indicators only
# ================================================================

_cache = {"history": [], "ts": 0}

def get_history():
    now = time.time()
    if now - _cache["ts"] < 300 and _cache["history"]:
        return _cache["history"]
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
        r = requests.get(url,
            params={"interval":"1h","range":"10d"},
            headers={"User-Agent":"Mozilla/5.0"}, timeout=8)
        closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        _cache["history"] = closes[-60:]
        _cache["ts"] = now
        return _cache["history"]
    except Exception as e:
        log.error(f"History error: {e}")
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
    (4380,"Resistance zone"), (4400,"Major level"),
    (4420,"Intraday zone"),  (4440,"Watch zone"),
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
    closes  = get_history()
    is_sell = direction.upper() == "SELL"
    score   = 0
    breakdown   = []
    suggestions = []
    sl_dist = abs(entry - sl)

    rsi  = calc_rsi(closes)  if closes else 50.0
    ma20 = calc_ma(closes,20) if closes else entry
    ma50 = calc_ma(closes,50) if closes else entry
    atr  = calc_atr(closes)  if closes else 15.0

    # ── TREND ──────────────────────────────────
    if is_sell:
        if ma20 < ma50:
            score += 20
            breakdown.append(("✅ Gold is in a downtrend — good direction for SELL", +20))
        else:
            score -= 20
            breakdown.append(("❌ Gold is rising right now — risky to SELL", -20))
            suggestions.append("Wait for gold to start falling before SELL")
    else:
        if ma20 > ma50:
            score += 20
            breakdown.append(("✅ Gold is in an uptrend — good direction for BUY", +20))
        else:
            score -= 20
            breakdown.append(("❌ Gold is falling right now — risky to BUY", -20))
            suggestions.append("Wait for gold to start rising before BUY")

    # ── ENTRY ──────────────────────────────────
    level, desc = at_key_level(entry, 12)
    if level:
        score += 20
        breakdown.append((f"✅ Entry at a known price zone ({level})", +20))
    else:
        sup, res = nearest_levels(entry)
        if is_sell and res and res[0][0]-entry < 20:
            score += 12
            breakdown.append((f"✅ Entry near resistance zone {res[0][0]}", +12))
        elif not is_sell and sup and entry-sup[0][0] < 20:
            score += 12
            breakdown.append((f"✅ Entry near support zone {sup[0][0]}", +12))
        else:
            score -= 12
            breakdown.append(("⚠️ Entry not near any key price zone", -12))
            if is_sell and res:
                suggestions.append(f"Better entry: {res[0][0]} — a known resistance zone")
            elif not is_sell and sup:
                suggestions.append(f"Better entry: {sup[0][0]} — a known support zone")

    # ── MOMENTUM (plain language) ───────────────
    if is_sell:
        if rsi > 65:
            score += 15
            breakdown.append(("✅ Price has risen a lot — sellers likely to step in", +15))
        elif rsi > 55:
            score += 5
            breakdown.append(("⚠️ Price still moving up — wait for rejection before SELL", +5))
        elif rsi < 40:
            score -= 15
            breakdown.append(("❌ Price already dropped a lot — bad timing to SELL", -15))
            suggestions.append("Price fell too fast — wait for a bounce before SELL")
    else:
        if rsi < 35:
            score += 15
            breakdown.append(("✅ Price has fallen a lot — buyers likely to step in", +15))
        elif rsi < 45:
            score += 5
            breakdown.append(("⚠️ Price still falling — wait for reversal sign before BUY", +5))
        elif rsi > 60:
            score -= 15
            breakdown.append(("❌ Price already risen a lot — bad timing to BUY", -15))
            suggestions.append("Price rose too fast — wait for a pullback before BUY")

    # ── STOP LOSS ──────────────────────────────
    if sl_dist < atr * 0.6:
        score -= 15
        better_sl = round(entry+atr*0.9,1) if is_sell else round(entry-atr*0.9,1)
        breakdown.append((f"❌ Stop loss too close — easily hit by normal price movement", -15))
        suggestions.append(f"Move SL to {better_sl} — safer distance from entry")
    elif sl_dist <= atr * 2.5:
        score += 15
        breakdown.append((f"✅ Stop loss at a good distance ({sl_dist:.0f} pip)", +15))
    else:
        score += 5
        breakdown.append((f"⚠️ Stop loss is wide ({sl_dist:.0f} pip) — reduces position size", +5))

    # ── REWARD vs RISK ─────────────────────────
    tp1 = round(entry-sl_dist*1.5,1) if is_sell else round(entry+sl_dist*1.5,1)
    tp2 = round(entry-sl_dist*2.5,1) if is_sell else round(entry+sl_dist*2.5,1)
    rr  = 2.5

    if sl_dist >= 5:
        if sl_dist*2.5/sl_dist >= 2.5:
            score += 15
            breakdown.append(("✅ Good reward vs risk — worth the trade", +15))
        elif sl_dist*1.5/sl_dist >= 1.5:
            score += 8
            breakdown.append(("⚠️ Reward vs risk is acceptable", +8))
        else:
            score -= 12
            breakdown.append(("❌ Reward too small vs risk — not worth it", -12))
            suggestions.append(f"Set TP further at {tp2} for a better reward")

    score = max(5, min(95, score))
    return score, breakdown, suggestions, tp1, tp2, rr

# ================================================================
# FORMAT
# ================================================================

def conf_bar(pct):
    f = round(pct/10)
    return "█"*f + "░"*(10-f)

def get_verdict(score):
    if score >= 75: return "🟢 SOLID"
    if score >= 58: return "🟡 OK"
    if score >= 40: return "🔴 WEAK"
    return "⛔ AVOID"

def format_result(direction, entry, sl, score, breakdown, suggestions, tp1, tp2, rr):
    em = "📉" if direction.upper()=="SELL" else "📈"
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🤖 GoldAI | {em} {direction.upper()} @ {entry}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"CONFIDENCE  {conf_bar(score)}  {score}%  {get_verdict(score)}",
        "",
    ]
    for reason, _ in breakdown:
        lines.append(reason)
    lines += [
        "",
        f"SL: {sl} | TP1: {tp1} | TP2: {tp2}",
        f"RR: 1:{rr}",
    ]
    if suggestions:
        lines += ["", "💡 How to improve:"]
        for s in suggestions:
            lines.append(f"   • {s}")
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Your idea. Your decision. Always use SL.",
    ]
    return "\n".join(lines)

# ================================================================
# PARSE INPUT
# ================================================================

def parse_trade(text):
    text = text.strip().upper().replace("SL", "")
    parts = [p for p in text.split() if p]
    if len(parts) < 3: return None
    try:
        direction = parts[0]
        if direction not in ("BUY","SELL"): return None
        entry = float(parts[1])
        sl    = float(parts[2])
        if direction=="SELL" and sl <= entry: return None
        if direction=="BUY"  and sl >= entry: return None
        return direction, entry, sl
    except:
        return None

# ================================================================
# HANDLERS
# ================================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 GoldAI Bot — Free Trade Checker\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Type your trade idea to check it:\n\n"
        "SELL 4380 sl 4400\n"
        "BUY 4280 sl 4258\n\n"
        "Other tools:\n"
        "/scan — find setups now\n"
        "/levels — price zones map\n"
        "/pos 1000 2 4380 4400 — lot size\n"
        "/help — all commands\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Just type — no command needed"
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    result = parse_trade(text)
    if not result:
        await update.message.reply_text(
            "Type your trade idea like this:\n\n"
            "SELL 4380 sl 4400\n"
            "BUY 4280 sl 4258\n\n"
            "SELL = you think price goes down\n"
            "BUY  = you think price goes up\n"
            "sl   = your stop loss price\n\n"
            "For SELL: stop loss must be above entry\n"
            "For BUY:  stop loss must be below entry"
        )
        return
    direction, entry, sl = result
    await update.message.reply_text("Analyzing... ⏳")
    score, breakdown, suggestions, tp1, tp2, rr = calc_confidence(direction, entry, sl)
    await update.message.reply_text(
        format_result(direction, entry, sl, score, breakdown, suggestions, tp1, tp2, rr)
    )

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scanning... ⏳")
    closes = get_history()
    if not closes:
        await update.message.reply_text("⚠️ Data unavailable. Try again."); return
    current = closes[-1]
    sup, res = nearest_levels(current, 2)
    setups = []
    for level, desc in res:
        if abs(level-current) < 50:
            sl = round(level+22,1)
            score, bd, sg, tp1, tp2, rr = calc_confidence("SELL",level,sl)
            if score >= 55:
                setups.append({"dir":"SELL","entry":level,"sl":sl,"tp2":tp2,"rr":rr,"score":score,"desc":desc})
    for level, desc in sup:
        if abs(current-level) < 50:
            sl = round(level-22,1)
            score, bd, sg, tp1, tp2, rr = calc_confidence("BUY",level,sl)
            if score >= 55:
                setups.append({"dir":"BUY","entry":level,"sl":sl,"tp2":tp2,"rr":rr,"score":score,"desc":desc})
    if not setups:
        await update.message.reply_text(
            f"📊 No strong setups near {round(current,1)} right now.\n\n"
            "Type your own idea to check it:\n"
            "SELL 4380 sl 4400"
        ); return
    lines = ["━━━━━━━━━━━━━━━━━━━━━━",
             f"🤖 GoldAI Scan | {round(current,1)}",
             "━━━━━━━━━━━━━━━━━━━━━━"]
    for s in sorted(setups,key=lambda x:x['score'],reverse=True)[:3]:
        em = "📉" if s['dir']=="SELL" else "📈"
        lines += [f"{em} {s['dir']} @ {s['entry']}  {get_verdict(s['score'])}  {s['score']}%",
                  f"   SL: {s['sl']} | TP: {s['tp2']} | RR 1:{s['rr']}",
                  f"   {s['desc']}", ""]
    lines.append("Type to check: SELL 4380 sl 4400")
    await update.message.reply_text("\n".join(lines))

async def cmd_levels(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    closes = get_history()
    current = round(closes[-1],1) if closes else 4300
    sup, res = nearest_levels(current, 4)
    lines = ["━━━━━━━━━━━━━━━━━━━━━━",
             "📍 XAU/USD Price Zones",
             "━━━━━━━━━━━━━━━━━━━━━━",
             "🔴 RESISTANCE (above):"]
    for level, desc in reversed(res):
        lines.append(f"   {level}  (+{round(level-current,1)} pip)  {desc}")
    lines += ["", f"📍 NOW → {current}", "", "🟢 SUPPORT (below):"]
    for level, desc in sup:
        lines.append(f"   {level}  (-{round(current-level,1)} pip)  {desc}")
    lines += ["━━━━━━━━━━━━━━━━━━━━━━",
              "Check a zone: SELL 4380 sl 4400"]
    await update.message.reply_text("\n".join(lines))

async def cmd_pos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 4:
        await update.message.reply_text(
            "Usage: /pos balance risk% entry sl\n"
            "Example: /pos 1000 2 4380 4400"
        ); return
    try:
        balance=float(args[0]); risk_pct=float(args[1])
        entry=float(args[2]);   sl=float(args[3])
    except:
        await update.message.reply_text("Invalid numbers."); return
    sl_dist  = abs(entry-sl)
    risk_amt = balance*risk_pct/100
    lot      = max(0.01,round(risk_amt/(sl_dist*10),2))
    direction= "SELL" if entry>sl else "BUY"
    lines = ["━━━━━━━━━━━━━━━━━━━━━━",
             f"💰 Lot Size | {direction} @ {entry}",
             "━━━━━━━━━━━━━━━━━━━━━━",
             f"Balance:  ${balance:,.0f}",
             f"Risk:     {risk_pct}% = ${risk_amt:.0f}",
             f"SL:       {sl_dist:.0f} pip away",
             "", f"📊 LOT SIZE: {lot} oz", "",
             "🎯 Take Profit:"]
    for mult,label in [(1,"1:1"),(1.5,"1:1.5"),(2,"1:2"),(3,"1:3")]:
        tp = round(entry-sl_dist*mult if direction=="SELL" else entry+sl_dist*mult,1)
        lines.append(f"   {label}  {tp}  +${round(risk_amt*mult,0):.0f}")
    lines += ["━━━━━━━━━━━━━━━━━━━━━━",
              "Never risk more than 2% per trade."]
    await update.message.reply_text("\n".join(lines))

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 GoldAI Bot — How to use\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Check your trade idea:\n"
        "SELL 4380 sl 4400\n"
        "BUY 4280 sl 4258\n\n"
        "Commands:\n"
        "/scan — find top setups now\n"
        "/levels — support & resistance zones\n"
        "/pos 1000 2 4380 4400 — lot size\n"
        "/help — this message\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "XAU/USD Gold only | Free forever"
    )

# ================================================================
# MAIN
# ================================================================

def main():
    if not BOT_TOKEN or BOT_TOKEN=="YOUR_BOT_TOKEN_HERE":
        print("[ERROR] Set BOT_TOKEN env variable"); return
    log.info("GoldAI Bot v4 starting...")
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
