"""Connecteur MetaTrader 5 (paquet Python officiel « MetaTrader5 », Windows + terminal MT5 ouvert).

  pip install MetaTrader5
  SWINGBOT_BROKER=mt5  SWINGBOT_MODE=demo  python -m swingbot.live

Identifiants (facultatifs si le terminal est déjà connecté) — UNIQUEMENT en variables d'environnement :
  MT5_PATH (chemin de terminal64.exe), MT5_LOGIN, MT5_PASSWORD, MT5_SERVER (ex. ICMarketsEU-Demo)
  SWINGBOT_MT5_SUFFIX : suffixe éventuel des symboles chez le broker (ex. ".a"), vide par défaut

Sécurité :
  - mode 'demo' : refuse de trader si le compte connecté n'est PAS un compte démo ;
  - mode 'live' : exige SWINGBOT_LIVE_CONFIRM (voir ctrader.trading_mode) ET un compte réel ;
  - seules les positions portant le numéro magique du bot sont gérées (vos trades manuels ne sont pas touchés).
"""
from __future__ import annotations

import os

import pandas as pd

from .ctrader import CloseIntent, OrderIntent, broker_symbol, our_symbol

MAGIC = 20261105
LABEL = "swingbot_v2"


def _mt5():
    import MetaTrader5 as mt5  # noqa: N813  (Windows uniquement)
    return mt5


def mt5_symbol(sym: str) -> str:
    return broker_symbol(sym) + os.environ.get("SWINGBOT_MT5_SUFFIX", "")


def from_mt5_symbol(name: str) -> str:
    sfx = os.environ.get("SWINGBOT_MT5_SUFFIX", "")
    if sfx and name.endswith(sfx):
        name = name[: -len(sfx)]
    return our_symbol(name)


def running_terminals() -> list[str]:
    """Chemins des terminaux MT5 (terminal64.exe) ouverts en ce moment (Windows)."""
    import subprocess
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-Process terminal64 -ErrorAction SilentlyContinue).Path"],
                             capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return []
    return [line.strip() for line in out.splitlines() if line.strip().lower().endswith("terminal64.exe")]


def installed_terminals() -> list[str]:
    """Terminaux MT5 installés dans les dossiers habituels (pour la liste de l'application)."""
    import glob
    found = []
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", ""),
                 os.path.expanduser("~")):
        if base:
            found += glob.glob(os.path.join(base, "*", "terminal64.exe"))
    return sorted(set(found))


AUTH_HELP = ("Le terminal MT5 utilisé n'est connecté à aucun compte (erreur -6). Causes fréquentes : "
             "(1) plusieurs MT5 installés et ce n'est pas le bon : ouvrez le MT5 connecté à votre compte démo "
             "IC Markets, ou choisissez son terminal64.exe dans l'application ; "
             "(2) le terminal affiche « Pas de connexion » ou « Compte invalide » : reconnectez-vous ; "
             "(3) indiquez identifiant + serveur + mot de passe dans l'application pour une connexion automatique.")


def connect(mode: str):
    """Connexion « toute seule » : chemin indiqué, sinon chaque terminal MT5 OUVERT sur le PC,
    sinon le terminal par défaut. On garde le premier qui est connecté à un compte."""
    mt5 = _mt5()
    creds = {}
    if os.environ.get("MT5_LOGIN"):
        creds = dict(login=int(os.environ["MT5_LOGIN"]), password=os.environ.get("MT5_PASSWORD", ""),
                     server=os.environ.get("MT5_SERVER", ""))
    paths = [os.environ["MT5_PATH"]] if os.environ.get("MT5_PATH") else running_terminals() + [None]
    errors = []
    for path in paths:
        kw = dict(creds)
        if path:
            kw["path"] = path
        if mt5.initialize(**kw):
            if mt5.account_info() is not None:
                break
            errors.append((path, "aucun compte connecté"))
            mt5.shutdown()
        else:
            errors.append((path, mt5.last_error()))
    else:
        detail = " | ".join(f"{p or 'terminal par défaut'} : {e}" for p, e in errors)
        auth = any("-6" in str(e) or "Authorization" in str(e) or "aucun compte" in str(e) for _, e in errors)
        raise RuntimeError(f"MT5 : connexion impossible ({detail}). " + (AUTH_HELP if auth else
                           "Le terminal MetaTrader 5 est-il ouvert ?"))
    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError("MT5 : aucun compte connecté")
    is_demo = acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    if mode == "demo" and not is_demo:
        mt5.shutdown()
        raise PermissionError("SÉCURITÉ : le compte MT5 connecté est un compte RÉEL alors que le mode est 'demo'.")
    if mode == "live" and is_demo:
        mt5.shutdown()
        raise PermissionError("Mode 'live' demandé mais le compte connecté est un compte démo.")
    return mt5, acc


class MT5Broker:
    name = "mt5"

    def __init__(self, mode: str):
        self.mode = mode

    def snapshot(self):
        mt5, acc = connect(self.mode)
        try:
            pos = []
            for p in mt5.positions_get() or []:
                if p.magic != MAGIC:
                    continue
                pos.append(dict(position_id=p.ticket, sym=from_mt5_symbol(p.symbol),
                                side=1 if p.type == mt5.POSITION_TYPE_BUY else -1, volume=p.volume,
                                price=p.price_open, stop=p.sl, label=LABEL, open_ts=p.time))
            return dict(balance=float(acc.equity), currency=acc.currency, positions=pos, is_demo=acc.trade_mode ==
                        mt5.ACCOUNT_TRADE_MODE_DEMO, login=getattr(acc, "login", "?"),
                        server=getattr(acc, "server", "?"))
        finally:
            mt5.shutdown()

    def close_reason(self, position_id):
        """Pourquoi une position a disparu : 'stop' (stop touché), 'manuel', ou 'autre' (ex. expiration
        d'un futures CFD, clôturé par le broker au prix de règlement)."""
        mt5, _ = connect(self.mode)
        try:
            deals = mt5.history_deals_get(position=int(position_id)) or []
            outs = [d for d in deals if getattr(d, "entry", 0) in (1, 3)]   # DEAL_ENTRY_OUT / OUT_BY
            if not outs:
                return None
            r = outs[-1].reason
            if r == getattr(mt5, "DEAL_REASON_SL", 4) or r == getattr(mt5, "DEAL_REASON_SO", 6):
                return "stop"
            if r in (getattr(mt5, "DEAL_REASON_CLIENT", 0), getattr(mt5, "DEAL_REASON_MOBILE", 1),
                     getattr(mt5, "DEAL_REASON_WEB", 2)):
                return "manuel"
            return "autre"
        finally:
            mt5.shutdown()

    @staticmethod
    def _filling(mt5, info):
        fm = getattr(info, "filling_mode", 0)
        if fm & 1:
            return mt5.ORDER_FILLING_FOK
        if fm & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def execute(self, orders: list[OrderIntent], closes: list[CloseIntent]):
        mt5, _ = connect(self.mode)
        out = []
        try:
            for c in closes:
                for p in mt5.positions_get(ticket=int(c.position_id)) if c.position_id else []:
                    if p.magic != MAGIC:
                        continue
                    tick = mt5.symbol_info_tick(p.symbol)
                    buy = p.type == mt5.POSITION_TYPE_BUY
                    info = mt5.symbol_info(p.symbol)
                    req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=p.symbol, volume=p.volume, position=p.ticket,
                               type=mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
                               price=tick.bid if buy else tick.ask, deviation=20, magic=MAGIC,
                               comment=c.comment[:31], type_filling=self._filling(mt5, info),
                               type_time=mt5.ORDER_TIME_GTC)
                    r = mt5.order_send(req)
                    out.append(dict(action="fermeture", sym=c.sym, position_id=p.ticket,
                                    retcode=getattr(r, "retcode", None), ok=getattr(r, "retcode", None) ==
                                    mt5.TRADE_RETCODE_DONE, raison=c.comment))
            for o in orders:
                name = resolve_symbol(mt5, mt5_symbol(o.sym))
                if not name:
                    out.append(dict(action="ordre", sym=o.sym, statut=f"symbole {mt5_symbol(o.sym)} introuvable"))
                    continue
                info = mt5.symbol_info(name)
                step = info.volume_step
                vol = round(int(o.lots / step + 1e-9) * step, 8)
                if vol < info.volume_min and not o.risk_eur:
                    out.append(dict(action="ordre", sym=o.sym, statut=f"volume {vol} < minimum {info.volume_min}"))
                    continue
                tick = mt5.symbol_info_tick(name)
                price = tick.ask if o.side > 0 else tick.bid
                min_dist = info.trade_stops_level * info.point
                dist = price * o.stop_pct if o.stop_pct else o.stop_dist
                dist = max(dist, min_dist * 1.5)
                if o.risk_eur:   # volume recalculé avec la valeur du tick du broker (dans la devise du compte)
                    loss_per_lot = dist / info.trade_tick_size * info.trade_tick_value
                    vol = round(int(o.risk_eur / loss_per_lot / step + 1e-9) * step, 8)
                    if vol < info.volume_min and info.volume_min * loss_per_lot <= 1.5 * o.risk_eur:
                        vol = info.volume_min
                    if vol < info.volume_min:
                        out.append(dict(action="ordre", sym=o.sym, statut=f"lot minimum trop gros : "
                                        f"{info.volume_min * loss_per_lot:.2f} de risque pour {o.risk_eur:.2f} voulus"))
                        continue
                sl = round(price - o.side * dist, info.digits)
                req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=name, volume=vol,
                           type=mt5.ORDER_TYPE_BUY if o.side > 0 else mt5.ORDER_TYPE_SELL, price=price, sl=sl,
                           deviation=20, magic=MAGIC, comment=o.comment[:31], type_time=mt5.ORDER_TIME_GTC,
                           type_filling=self._filling(mt5, info))
                chk = mt5.order_check(req)
                if chk is None or chk.retcode not in (0, mt5.TRADE_RETCODE_DONE):
                    out.append(dict(action="ordre", sym=o.sym, statut="refusé par order_check",
                                    retcode=getattr(chk, "retcode", None), detail=getattr(chk, "comment", "")))
                    continue
                r = mt5.order_send(req)
                out.append(dict(action="ordre", sym=o.sym, volume=vol, prix=price, stop=sl,
                                retcode=getattr(r, "retcode", None),
                                ok=getattr(r, "retcode", None) == mt5.TRADE_RETCODE_DONE,
                                ticket=getattr(r, "order", None)))
        finally:
            mt5.shutdown()
        return out


def resolve_symbol(mt5, name: str, roll_days: int = 3):
    """Nom exact s'il existe, sinon le contrat future le plus proche (pas à moins de roll_days de l'échéance)
    parmi les symboles qui commencent par `name` suivi de _ . - ou d'un chiffre (ex. WTI -> WTI_Z6)."""
    import time as _t
    if mt5.symbol_info(name) is not None:
        mt5.symbol_select(name, True)
        return name
    best, best_exp = None, None
    for si in mt5.symbols_get() or []:
        n = si.name
        if not n.startswith(name) or len(n) == len(name):
            continue
        c = n[len(name)]
        if c not in "_.- " and not c.isdigit():
            continue
        exp = getattr(si, "expiration_time", 0) or 0
        if exp and exp < _t.time() + roll_days * 86400:
            continue
        if best is None or (exp and (best_exp is None or exp < best_exp)):
            best, best_exp = n, exp
    if best:
        mt5.symbol_select(best, True)
    return best


def check_symbols(mode: str, syms: list[str] | None = None) -> list[str]:
    """Vérifie que les instruments du système existent chez le broker ; sinon liste les symboles proches."""
    from .live import SYSTEMS, SYSTEM_NAME
    syms = syms or SYSTEMS[SYSTEM_NAME]["universe"]
    mt5, _ = connect(mode)
    lines = []
    try:
        missing = False
        for s in syms:
            r = resolve_symbol(mt5, mt5_symbol(s))
            lines.append(f"{s:7s} -> {r if r else 'INTROUVABLE (' + mt5_symbol(s) + ')'}")
            missing |= r is None
        if missing:
            keys = ("WTI", "BRENT", "OIL", "XTI", "XBR", "GC", "GOLD", "XAU", "DXY", "USDX")
            cand = [si.name for si in (mt5.symbols_get() or [])
                    if any(si.name.upper().startswith(k) or k in (si.description or "").upper() for k in keys)]
            lines.append("Symboles proches chez ce broker : " + (", ".join(cand[:30]) or "(aucun)"))
            lines.append("-> si vous êtes sur le serveur de démo MetaQuotes, ouvrez un compte démo IC Markets "
                         "(Fichier > Ouvrir un compte) : les futures sans swap n'existent que là.")
    finally:
        mt5.shutdown()
    return lines


def export_history(syms: list[str], bars: int = 5000, out_dir=None, mode: str = "demo"):
    """Exporte l'historique D1 du broker dans data/mt5/<SYM>_D1.csv (prioritaire sur Yahoo au chargement).
    Les bougies MT5 d'IC Markets clôturent à 17h New York : c'est le « vrai » jour de trading forex."""
    from pathlib import Path
    mt5, _ = connect(mode)
    from .paths import ROOT
    out = Path(out_dir or ROOT / "data" / "mt5")
    out.mkdir(parents=True, exist_ok=True)
    done = []
    try:
        for s in syms:
            name = mt5_symbol(s)
            mt5.symbol_select(name, True)
            rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_D1, 0, bars)
            if rates is None or len(rates) == 0:
                continue
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            df[["time", "open", "high", "low", "close", "tick_volume"]].rename(
                columns={"tick_volume": "volume"}).to_csv(out / f"{s}_D1.csv", index=False)
            done.append(s)
    finally:
        mt5.shutdown()
    return done
