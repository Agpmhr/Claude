"""Exécution quotidienne du système v2 (à lancer une fois par jour, après la clôture D1, vers 22h15 UTC).

  python -m swingbot.live                 # mode papier (défaut) : rien n'est envoyé
  SWINGBOT_MODE=demo python -m swingbot.live   # compte DÉMO cTrader (voir swingbot/ctrader.py)

Chaque décision est journalisée dans logs/live_decisions.jsonl : pourquoi ce trade, quelle idée,
quelle taille, quel risque, ce que disait le filtre news. L'état est gardé dans state/live_state.json.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .calendar_events import load_events
from .ctrader import CloseIntent, OrderIntent, make_broker, our_symbol, trading_mode
from .data import _read, clean, eur_conversion, load_rates, load_universe
from .ideas import IDEAS
from .indicators import atr
from .instruments import HOLDOUT, LOT_STEP, MIN_LOT, RESEARCH, UNIVERSE, currencies_of
from .portfolio import RiskConfig

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state" / "live_state.json"
LOG = ROOT / "logs" / "live_decisions.jsonl"
SYSTEM = {"idx_dip": dict(lo=5, trend=False), "carry_safe": dict(thr=0.02, vix_max=22.0)}


def log(entry: dict):
    LOG.parent.mkdir(exist_ok=True)
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    print(entry)


def load_state(capital: float) -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return dict(positions={}, equity_hist=[], peak=capital, halted=False)


def save_state(st: dict):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, default=str))


def refresh_data():
    """Recharge les D1 récentes : depuis MT5 si SWINGBOT_BROKER=mt5 (bougies du broker), sinon Yahoo.
    VIX : toujours Yahoo (pas forcément disponible chez le broker)."""
    import subprocess
    import sys
    if os.environ.get("SWINGBOT_BROKER", "").lower() == "mt5" and trading_mode() != "paper":
        from .mt5_broker import export_history
        export_history(RESEARCH + HOLDOUT, bars=400, mode=trading_mode())
    subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_yahoo.py"), "--force"], check=False)


def signals_today(data, ctx):
    """Pour chaque idée et instrument : (entrée, sortie_long, sortie_short, règles, ATR) à la dernière clôture."""
    out = []
    for name, p in SYSTEM.items():
        fn = IDEAS[name][0]
        for s, (ent, xl, xs, rules) in fn(data, ctx, **p).items():
            a = atr(data[s], rules.atr_n)
            out.append(dict(family=name, sym=s, entry=int(ent.iloc[-1]),
                            exit_long=bool(xl.iloc[-1]) if xl is not None else False,
                            exit_short=bool(xs.iloc[-1]) if xs is not None else False,
                            max_bars=rules.max_bars, stop_dist=float(rules.stop_atr * a.iloc[-1]),
                            date=data[s].index[-1], price=float(data[s].close.iloc[-1])))
    return out


def run(capital: float = float(os.environ.get("SWINGBOT_CAPITAL", 1000)), refresh: bool = True, as_of=None):
    mode = trading_mode()
    cfg = RiskConfig(capital=capital)
    if refresh:
        refresh_data()
    rates, ev = load_rates(), load_events()
    vix, _ = clean(_read("VIX", "D1"), "VIX", "D1")
    data, _ = load_universe("D1", RESEARCH + HOLDOUT)
    if as_of is not None:   # rejouer une journée passée (tests / comparaison)
        data = {s: d[d.index <= pd.Timestamp(as_of)] for s, d in data.items()}
        vix = vix[vix.index <= pd.Timestamp(as_of)]
    ctx = dict(rates=rates, events=ev, vix=vix, all_data=data)
    days = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    conv = eur_conversion(data, days).iloc[-1]
    today = days[-1]

    broker = make_broker(capital)
    snap = broker.snapshot()
    st = load_state(capital)
    equity = snap.get("balance", snap.get("equity", capital))
    if mode == "paper":
        equity = capital + sum(p.get("pnl_eur", 0.0) for p in st.get("closed", []))
    # --- coupe-circuit
    st["equity_hist"].append([str(today.date()), equity])
    st["peak"] = max(st["peak"], equity)
    hist = [e for _, e in st["equity_hist"]]
    day_ret = equity / hist[-2] - 1 if len(hist) > 1 else 0.0
    week_ret = equity / hist[-6] - 1 if len(hist) > 5 else 0.0
    if equity / st["peak"] - 1 <= -cfg.max_dd_stop:
        st["halted"] = True
    blocked = st["halted"] or day_ret <= -cfg.daily_stop or week_ret <= -cfg.weekly_stop
    log(dict(type="etat", mode=mode, date=today, equite=equity, pic=st["peak"], perte_jour=day_ret,
             perte_semaine=week_ret, entrees_bloquees=blocked, arret_definitif=st["halted"]))

    # --- filtre news (défense uniquement)
    news_block = {}
    try:
        from .news import assess, restrictions
        news_block = restrictions(assess())
    except Exception as e:
        log(dict(type="news", erreur=str(e)))

    sig = signals_today(data, ctx)
    by_key = {(s["family"], s["sym"]): s for s in sig}

    # --- réconciliation avec le broker (positions coupées par le stop)
    if mode != "paper":
        live_ids = {p["position_id"] for p in snap["positions"] if p.get("label") == "swingbot_v2"}
        for k, p in list(st["positions"].items()):
            if p.get("position_id") not in live_ids:
                log(dict(type="sortie_constatee", sym=p["sym"], raison="stop touché ou fermeture manuelle", pos=p))
                st["positions"].pop(k)
        for p in snap["positions"]:
            if p.get("label") == "swingbot_v2" and not any(q.get("position_id") == p["position_id"]
                                                          for q in st["positions"].values()):
                sym = our_symbol(p["sym"])
                st["positions"][f"?|{sym}"] = dict(sym=sym, family="?", dir=p["side"], position_id=p["position_id"],
                                                   entry_date=str(today.date()), lots=None, stop_dist=None)

    # --- sorties
    closes = []
    for k, p in list(st["positions"].items()):
        s = by_key.get((p["family"], p["sym"]))
        idx = data[p["sym"]].index
        bars = int((idx > pd.Timestamp(p["entry_date"])).sum())   # séances tenues depuis l'entrée (comme le backtest)
        why = None
        if s is not None:
            if (p["dir"] > 0 and s["exit_long"]) or (p["dir"] < 0 and s["exit_short"]):
                why = "signal de sortie de l'idée"
            elif bars >= s["max_bars"]:
                why = f"durée maximale atteinte ({bars} séances)"
        cur = news_block.get(UNIVERSE[p["sym"]].quote) or news_block.get(UNIVERSE[p["sym"]].base)
        if cur == "couper_position":
            why = "news : choc majeur sur la devise"
        if why:
            closes.append(CloseIntent(p["sym"], p.get("position_id"), why))
            if mode == "paper" and p.get("stop_dist"):
                px = float(data[p["sym"]].close.iloc[-1])
                r_mult = p["dir"] * (px - p["entry_ref"]) / p["stop_dist"]
                st.setdefault("closed", []).append(dict(sym=p["sym"], family=p["family"], entry_date=p["entry_date"],
                                                        exit_date=str(today.date()), r=r_mult,
                                                        pnl_eur=r_mult * p["risk_eur"]))
            log(dict(type="decision_sortie", sym=p["sym"], idee=p["family"], raison=why, seances=bars))
            st["positions"].pop(k)

    # --- entrées
    orders = []
    open_risk = sum(p.get("risk_eur") or 0 for p in st["positions"].values())
    exp = {}
    for p in st["positions"].values():
        for c_, sgn in zip(currencies_of(UNIVERSE[p["sym"]]), (1, -1)):
            exp[c_] = exp.get(c_, 0.0) + sgn * p["dir"]
    for s in sorted(sig, key=lambda x: x["family"]):
        if s["entry"] == 0 or any(p["sym"] == s["sym"] for p in st["positions"].values()):
            continue
        inst = UNIVERSE[s["sym"]]
        base = dict(type="decision_entree", sym=s["sym"], idee=s["family"], sens=s["entry"], date_signal=s["date"])
        if s["date"] != today:
            continue
        if blocked:
            log({**base, "refus": "coupe-circuit actif"}); continue
        ccys = currencies_of(inst)
        nb = [news_block.get(c) for c in ccys if news_block.get(c) in ("bloquer_entrees", "couper_position")]
        if nb:
            log({**base, "refus": f"news : {nb[0]}"}); continue
        want = equity * cfg.risk_per_trade
        if any(news_block.get(c) == "reduire_taille" for c in ccys):
            want *= 0.5
        loss_per_lot = s["stop_dist"] * inst.contract * conv[inst.quote]
        lots = np.floor(want / loss_per_lot / LOT_STEP) * LOT_STEP
        if lots < MIN_LOT:
            if MIN_LOT * loss_per_lot <= cfg.max_round_up * want:
                lots = MIN_LOT
            else:
                log({**base, "refus": f"lot minimum trop gros : 0,01 lot = {MIN_LOT * loss_per_lot:.2f} € de risque "
                                      f"pour {want:.2f} € voulus"}); continue
        risk = lots * loss_per_lot
        ok_ccy = all(abs(exp.get(c_, 0) + sgn * s["entry"]) <= cfg.max_ccy_units for c_, sgn in zip(ccys, (1, -1)))
        if len(st["positions"]) + len(orders) >= cfg.max_positions or open_risk + risk > cfg.max_open_risk * equity \
                or not ok_ccy:
            log({**base, "refus": "limites de portefeuille (positions / risque total / exposition par devise)"})
            continue
        why = {"idx_dip": "RSI(2) < 5 : forte baisse sur 2 jours, rachat de repli",
               "carry_safe": "écart de taux > 2 % et VIX bas et en baisse : on encaisse le swap"}[s["family"]]
        orders.append(OrderIntent(s["sym"], s["entry"], float(lots), s["stop_dist"], f"v2 {s['family']}"))
        log({**base, "lots": lots, "risque_eur": round(risk, 2), "stop_prix": s["stop_dist"], "raison": why,
             "score_ia": None, "prix_cloture": s["price"]})
        open_risk += risk
        for c_, sgn in zip(ccys, (1, -1)):
            exp[c_] = exp.get(c_, 0.0) + sgn * s["entry"]
        st["positions"][f"{s['family']}|{s['sym']}"] = dict(sym=s["sym"], family=s["family"], dir=s["entry"],
                                                            entry_date=str(today.date()), lots=float(lots),
                                                            stop_dist=s["stop_dist"], risk_eur=risk,
                                                            entry_ref=s["price"])
    res = broker.execute(orders, closes)
    for r in res:
        log(dict(type="execution", **r))
    # rattacher les identifiants de position renvoyés par le broker
    if mode != "paper":
        snap2 = broker.snapshot()
        for p in snap2["positions"]:
            for q in st["positions"].values():
                if our_symbol(p["sym"]) == q["sym"] and q.get("position_id") is None:
                    q["position_id"] = p["position_id"]
    save_state(st)
    return dict(ordres=len(orders), fermetures=len(closes))


if __name__ == "__main__":
    print(run())
