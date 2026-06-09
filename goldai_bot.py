"""
GoldAI Telegram Bot v5
======================
- No trend judgment — user decides direction
- Plain language with explanations
- Key level based TP
- How to use reminder at end
"""

import logging, time, math, os, requests, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, filters, ContextTypes
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s', level=logging.INFO)
log = logging.getLogger(__name__)

# ================================================================
# HEALTH SERVER
# ================================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"GoldAI Bot running!")
    def log_message(self, format, *args): pass

def run_health():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

# ================================================================
# PRICE HISTORY CACHE
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

def calc_atr(closes, p=14):
    if len(closes) < p+1: return 15.0
    trs = [abs(closes[i]-closes[i-1]) for i in range(1,len(closes))]
    return round(sum(trs[-p:])/p, 2)

def calc_momentum(closes, p=5):
    """Recent momentum — rising or falling"""
    if len(closes) < p+1: return 0
    recent = closes[-p:]
    return recent[-1] - recent[0]

# ================================================================
# KEY LEVELS
# ================================================================

KEY_LEVELS = [
    (4200,"Major support — long-term base"),
    (4250,"Support zone"),
    (4280,"Psychological level"),
    (4300,"Support cluster"),
    (4320,"Minor support"),
    (4350,"Structure level — tested multiple times"),
    (4380,"Flip zone — support turned resistance"),
    (4400,"Major psychological level"),
    (4420,"Intraday zone"),
    (4440,"Watch zone"),
    (4463,"Structure level"),
    (4488,"Key resistance — tested multiple times"),
    (4508,"Resistance zone"),
    (4527,"Spike high — strong resistance"),
    (4550,"Resistance cluster"),
    (4595,"Recent swing high"),
]

def nearest_levels(price, n=4):
    sup = sorted([(l,d) for l,d in KEY_LEVELS if l<price], key=lambda x:price-x[0])[:n]
    res = sorted([(l,d) for l,d in KEY_LEVELS if l>price], key=lambda x:x[0]-price)[:n]
    return sup, res

def at_key_level(price, thr=12):
    for level, desc in KEY_LEVELS:
        if abs(price-level) <= thr:
            return level, desc
    return None, None

# ================================================================
# CONFIDENCE ENGINE — no trend judgment
# ================================================================

def calc_confidence(direction, entry, sl):
    closes  = get_history()
    is_sell = direction.upper() == "SELL"
    score   = 0
    breakdown   = []  # (text, explanation, points)
    suggestions = []
    sl_dist = abs(entry - sl)

    atr      = calc_atr(closes) if closes else 15.0
    momentum = calc_momentum(closes) if closes else 0

    # ── ENTRY QUALITY ──────────────────────────
    level, desc = at_key_level(entry, 12)
    if level:
        score += 30
        breakdown.append((
            f"✅ Entry at key price zone ({level})",
            f"Price has reacted at {level} before — good spot to watch for reversal",
            +30
        ))
    else:
        sup, res = nearest_levels(entry)
        if is_sell and res and res[0][0]-entry < 20:
            score += 18
            breakdown.append((
                f"✅ Entry near resistance zone {res[0][0]}",
                f"Close to a known resistance — sellers may step in here",
                +18
            ))
        elif not is_sell and sup and entry-sup[0][0] < 20:
            score += 18
            breakdown.append((
                f"✅ Entry near support zone {sup[0][0]}",
                f"Close to a known support — buyers may step in here",
                +18
            ))
        else:
            score -= 15
            sup2, res2 = nearest_levels(entry)
            if is_sell and res2:
                better = res2[0][0]
                breakdown.append((
                    f"⚠️ Entry not near a key price zone",
                    f"No strong level nearby — consider waiting for price to reach {better} (resistance)",
                    -15
                ))
                suggestions.append(f"Move entry to {better} — a stronger resistance zone")
            elif not is_sell and sup2:
                better = sup2[0][0]
                breakdown.append((
                    f"⚠️ Entry not near a key price zone",
                    f"No strong level nearby — consider waiting for price to reach {better} (support)",
                    -15
                ))
                suggestions.append(f"Move entry to {better} — a stronger support zone")

    # ── STOP LOSS ──────────────────────────────
    # XAU/USD realistic thresholds
    # <15 pip = too tight, 15-50 pip = good, >50 pip = wide
    if sl_dist < 15:
        score -= 20
        better_sl = round(entry + 20, 1) if is_sell else round(entry - 20, 1)
        breakdown.append((
            f"❌ Stop loss too close ({sl_dist:.0f} pip)",
            f"Normal gold movement can hit this SL by accident — move to {better_sl} for safer distance",
            -20
        ))
        suggestions.append(f"Widen SL to {better_sl} — minimum 20 pip for gold")
    elif sl_dist <= 50:
        score += 25
        breakdown.append((
            f"✅ Stop loss well-placed ({sl_dist:.0f} pip)",
            f"Good distance — far enough to avoid noise, close enough to limit loss",
            +25
        ))
    else:
        score += 10
        breakdown.append((
            f"⚠️ Stop loss is wide ({sl_dist:.0f} pip)",
            f"Wide SL means smaller position size to keep same risk amount",
            +10
        ))

    # ── MOMENTUM TIMING ────────────────────────
    if is_sell:
        if momentum > atr * 0.3:
            score -= 10
            breakdown.append((
                f"⚠️ Price is still moving up",
                f"Momentum favors buyers right now — wait for price to stall or reject at entry",
                -10
            ))
            suggestions.append("Wait for a red candle closing below entry before entering")
        elif momentum < -atr * 0.3:
            score += 15
            breakdown.append((
                f"✅ Price momentum turning down",
                f"Sellers are gaining control — timing looks good for SELL",
                +15
            ))
        else:
            score += 5
            breakdown.append((
                f"➖ Price momentum neutral",
                f"No strong direction yet — watch for confirmation before entering",
                +5
            ))
            suggestions.append("Wait for a clear rejection candle at entry")
    else:
        if momentum < -atr * 0.3:
            score -= 10
            breakdown.append((
                f"⚠️ Price is still moving down",
                f"Momentum favors sellers right now — wait for price to stall or bounce at entry",
                -10
            ))
            suggestions.append("Wait for a green candle closing above entry before entering")
        elif momentum > atr * 0.3:
            score += 15
            breakdown.append((
                f"✅ Price momentum turning up",
                f"Buyers are gaining control — timing looks good for BUY",
                +15
            ))
        else:
            score += 5
            breakdown.append((
                f"➖ Price momentum neutral",
                f"No strong direction yet — watch for confirmation before entering",
                +5
            ))
            suggestions.append("Wait for a clear bounce candle at entry")

    # ── TP based on key levels ──────────────────
    sup, res = nearest_levels(entry)
    if is_sell:
        tp_levels = [l for l,d in sup if l < entry - sl_dist*0.3]
        tp1 = tp_levels[0] if len(tp_levels) >= 1 else round(entry - sl_dist*1.5, 1)
        tp2 = tp_levels[1] if len(tp_levels) >= 2 else round(entry - sl_dist*2.5, 1)
    else:
        tp_levels = [l for l,d in res if l > entry + sl_dist*0.3]
        tp1 = tp_levels[0] if len(tp_levels) >= 1 else round(entry + sl_dist*1.5, 1)
        tp2 = tp_levels[1] if len(tp_levels) >= 2 else round(entry + sl_dist*2.5, 1)

    rr1 = round(abs(tp1-entry)/sl_dist, 1) if sl_dist > 0 else 1.5
    rr2 = round(abs(tp2-entry)/sl_dist, 1) if sl_dist > 0 else 2.5

    # RR score
    if rr2 >= 2.0:
        score += 15
    elif rr2 >= 1.5:
        score += 8
    else:
        score -= 10
        suggestions.append(f"Consider targeting {tp2} for better reward")

    score = max(5, min(95, score))
    return score, breakdown, suggestions, tp1, tp2, rr1, rr2

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

def format_result(direction, entry, sl, score, breakdown, suggestions, tp1, tp2, rr1, rr2):
    em = "📉" if direction.upper()=="SELL" else "📈"
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🤖 GoldAI | {em} {direction.upper()} @ {entry}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"CONFIDENCE  {conf_bar(score)}  {score}%  {get_verdict(score)}",
        "",
    ]

    for title, explanation, _ in breakdown:
        lines.append(title)
        lines.append(f"   {explanation}")

    lines += [
        "",
        f"SL:  {sl}",
        f"TP1: {tp1}  (RR 1:{rr1})",
        f"TP2: {tp2}  (RR 1:{rr2})",
    ]

    if suggestions:
        lines += ["", "💡 How to improve:"]
        for s in suggestions:
            lines.append(f"   • {s}")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Your idea. Your decision. Always use SL.",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Check another idea:",
        "SELL 4380 sl 4400",
        "BUY 4280 sl 4258",
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
        "🤖 GoldAI — Free Trade Checker\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Already have a trade idea?\n"
        "Type it to check if it makes sense:\n\n"
        "SELL 4380 sl 4400\n"
        "BUY 4280 sl 4258\n\n"
        "Format: [BUY or SELL] [entry price] sl [stop loss]\n\n"
        "Other tools:\n"
        "/scan — find setups near current price\n"
        "/levels — key support & resistance zones\n"
        "/pos 1000 2 4380 4400 — lot size calculator\n"
        "/help — all commands\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "XAU/USD Gold only · Free forever"
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    result = parse_trade(text)
    if not result:
        await update.message.reply_text(
            "Type your trade idea to check it:\n\n"
            "SELL 4380 sl 4400\n"
            "BUY 4280 sl 4258\n\n"
            "SELL = you think price goes down\n"
            "BUY  = you think price goes up\n"
            "sl   = your stop loss price\n\n"
            "SELL: stop loss must be above entry\n"
            "BUY:  stop loss must be below entry"
        )
        return

    direction, entry, sl = result
    await update.message.reply_text("Analyzing... ⏳")
    score, breakdown, suggestions, tp1, tp2, rr1, rr2 = calc_confidence(direction, entry, sl)
    await update.message.reply_text(
        format_result(direction, entry, sl, score, breakdown, suggestions, tp1, tp2, rr1, rr2)
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
            sl = round(level+20, 1)
            score, bd, sg, tp1, tp2, rr1, rr2 = calc_confidence("SELL", level, sl)
            if score >= 55:
                setups.append({"dir":"SELL","entry":level,"sl":sl,"tp1":tp1,"tp2":tp2,"rr1":rr1,"rr2":rr2,"score":score,"desc":desc})

    for level, desc in sup:
        if abs(current-level) < 50:
            sl = round(level-20, 1)
            score, bd, sg, tp1, tp2, rr1, rr2 = calc_confidence("BUY", level, sl)
            if score >= 55:
                setups.append({"dir":"BUY","entry":level,"sl":sl,"tp1":tp1,"tp2":tp2,"rr1":rr1,"rr2":rr2,"score":score,"desc":desc})

    if not setups:
        await update.message.reply_text(
            f"📊 No strong setups near {round(current,1)} right now.\n\n"
            "Price is between key levels.\n"
            "Check your own idea:\n"
            "SELL 4380 sl 4400"
        ); return

    lines = ["━━━━━━━━━━━━━━━━━━━━━━",
             f"🤖 GoldAI Scan | {round(current,1)}",
             "━━━━━━━━━━━━━━━━━━━━━━"]

    for s in sorted(setups, key=lambda x:x['score'], reverse=True)[:3]:
        em = "📉" if s['dir']=="SELL" else "📈"
        lines += [
            f"{em} {s['dir']} @ {s['entry']}  {get_verdict(s['score'])}  {s['score']}%",
            f"   SL: {s['sl']}",
            f"   TP1: {s['tp1']} (RR 1:{s['rr1']})  TP2: {s['tp2']} (RR 1:{s['rr2']})",
            f"   {s['desc']}", ""
        ]

    lines += ["━━━━━━━━━━━━━━━━━━━━━━",
              "Check a setup: SELL 4380 sl 4400"]
    await update.message.reply_text("\n".join(lines))

async def cmd_levels(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    closes = get_history()
    current = round(closes[-1], 1) if closes else 4300
    sup, res = nearest_levels(current, 4)

    lines = ["━━━━━━━━━━━━━━━━━━━━━━",
             "📍 XAU/USD Key Price Zones",
             "━━━━━━━━━━━━━━━━━━━━━━",
             "🔴 RESISTANCE (above):"]
    for level, desc in reversed(res):
        lines.append(f"   {level}  +{round(level-current,1)} pip  {desc}")
    lines += ["", f"📍 NOW → {current}", "", "🟢 SUPPORT (below):"]
    for level, desc in sup:
        lines.append(f"   {level}  -{round(current-level,1)} pip  {desc}")
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
    lot      = max(0.01, round(risk_amt/(sl_dist*10), 2))
    direction= "SELL" if entry>sl else "BUY"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Position Size | {direction} @ {entry}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Balance:   ${balance:,.0f}",
        f"Risk:      {risk_pct}% = ${risk_amt:.0f}",
        f"SL:        {sl_dist:.0f} pip away",
        "",
        f"📊 LOT SIZE:  {lot} oz",
        f"Pip value:    ${lot*10:.2f} per pip",
        "",
        "🎯 Take Profit targets:",
    ]
    for mult, label in [(1,"1:1"),(1.5,"1:1.5"),(2,"1:2"),(3,"1:3")]:
        tp = round(entry-sl_dist*mult if direction=="SELL" else entry+sl_dist*mult, 1)
        lines.append(f"   {label}  →  {tp}  +${round(risk_amt*mult):.0f}")
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
        "/scan — top setups near current price\n"
        "/levels — key support & resistance zones\n"
        "/pos 1000 2 4380 4400 — lot size\n"
        "/help — this message\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "XAU/USD Gold only · Free forever"
    )

# ================================================================
# MAIN
# ================================================================

def main():
    if not BOT_TOKEN or BOT_TOKEN=="YOUR_BOT_TOKEN_HERE":
        print("[ERROR] Set BOT_TOKEN env variable"); return

    log.info("GoldAI Bot v5 starting...")
    threading.Thread(target=run_health, daemon=True).start()
    log.info("Health server started")

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
