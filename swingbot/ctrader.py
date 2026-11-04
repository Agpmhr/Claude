"""Connecteur cTrader Open API (IC Markets) : DÉMO par défaut, réel VERROUILLÉ.

Identifiants : UNIQUEMENT dans des variables d'environnement (jamais dans le code ni dans Git) :
  CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET   -> application créée sur https://openapi.ctrader.com
  CTRADER_ACCESS_TOKEN                       -> jeton obtenu via l'autorisation OAuth de votre compte
  CTRADER_ACCOUNT_ID                         -> ctidTraderAccountId du compte DÉMO

Modes (variable SWINGBOT_MODE) :
  paper (défaut) : aucune connexion, les ordres sont seulement journalisés
  demo           : serveur demo.ctraderapi.com ; refuse de trader si le compte est un compte réel
  live           : exige EN PLUS SWINGBOT_LIVE_CONFIRM=JE_COMPRENDS_LES_RISQUES ; sinon arrêt immédiat
"""
from __future__ import annotations

import os
from dataclasses import dataclass

LIVE_CONFIRM = "JE_COMPRENDS_LES_RISQUES"


def trading_mode() -> str:
    mode = os.environ.get("SWINGBOT_MODE", "paper").lower()
    if mode not in ("paper", "demo", "live"):
        raise ValueError(f"SWINGBOT_MODE inconnu : {mode}")
    if mode == "live" and os.environ.get("SWINGBOT_LIVE_CONFIRM") != LIVE_CONFIRM:
        raise PermissionError("Mode RÉEL refusé : il faut SWINGBOT_LIVE_CONFIRM=" + LIVE_CONFIRM +
                              " (réglage volontaire). Le bot reste désactivé.")
    return mode


@dataclass
class OrderIntent:
    sym: str
    side: int            # +1 achat, -1 vente
    lots: float
    stop_dist: float     # distance du stop en unités de prix
    comment: str = ""


@dataclass
class CloseIntent:
    sym: str
    position_id: int | None
    comment: str = ""


class PaperBroker:
    """Aucun ordre envoyé : utile pour vérifier le comportement avant la démo."""
    name = "paper"

    def __init__(self, equity: float):
        self.equity = equity

    def snapshot(self):
        return dict(equity=self.equity, positions=[])

    def execute(self, orders: list[OrderIntent], closes: list[CloseIntent]):
        return [dict(action="ordre", statut="simulé", **o.__dict__) for o in orders] + \
               [dict(action="fermeture", statut="simulé", **c.__dict__) for c in closes]


class CTraderBroker:
    """Une session par exécution : connexion -> authentification -> lecture -> ordres -> déconnexion."""
    name = "ctrader"

    def __init__(self, mode: str):
        self.mode = mode
        self.client_id = os.environ["CTRADER_CLIENT_ID"]
        self.client_secret = os.environ["CTRADER_CLIENT_SECRET"]
        self.token = os.environ["CTRADER_ACCESS_TOKEN"]
        self.account_id = int(os.environ["CTRADER_ACCOUNT_ID"])

    def _session(self, work):
        """Exécute `work(client, ctx)` (générateur inlineCallbacks) dans le réacteur Twisted et renvoie son résultat."""
        from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
        from ctrader_open_api.messages import OpenApiMessages_pb2 as M
        from twisted.internet import defer, reactor

        host = EndPoints.PROTOBUF_LIVE_HOST if self.mode == "live" else EndPoints.PROTOBUF_DEMO_HOST
        client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
        box = {}

        @defer.inlineCallbacks
        def run(_):
            try:
                yield client.send(M.ProtoOAApplicationAuthReq(clientId=self.client_id, clientSecret=self.client_secret))
                acc = Protobuf.extract((yield client.send(M.ProtoOAGetAccountListByAccessTokenReq(accessToken=self.token))))
                mine = [a for a in acc.ctidTraderAccount if a.ctidTraderAccountId == self.account_id]
                if not mine:
                    raise RuntimeError("Compte introuvable pour ce jeton")
                if self.mode == "demo" and mine[0].isLive:
                    raise PermissionError("SÉCURITÉ : ce compte est un compte RÉEL alors que le mode est 'demo'.")
                yield client.send(M.ProtoOAAccountAuthReq(ctidTraderAccountId=self.account_id, accessToken=self.token))
                box["result"] = yield work(client, M, Protobuf)
            except Exception as e:  # remonté à l'appelant
                box["error"] = e
            finally:
                reactor.callLater(0.5, reactor.stop)

        client.setConnectedCallback(lambda c: run(c))
        client.startService()
        reactor.run(installSignalHandlers=False)
        if "error" in box:
            raise box["error"]
        return box.get("result")

    # ------------------------------------------------------------------ lecture
    def snapshot(self):
        from twisted.internet import defer

        @defer.inlineCallbacks
        def work(client, M, P):
            tr = P.extract((yield client.send(M.ProtoOATraderReq(ctidTraderAccountId=self.account_id)))).trader
            rec = P.extract((yield client.send(M.ProtoOAReconcileReq(ctidTraderAccountId=self.account_id))))
            syms = P.extract((yield client.send(M.ProtoOASymbolsListReq(ctidTraderAccountId=self.account_id))))
            names = {s.symbolId: s.symbolName for s in syms.symbol}
            pos = [dict(position_id=p.positionId, sym=names.get(p.tradeData.symbolId, str(p.tradeData.symbolId)),
                        side=1 if p.tradeData.tradeSide == 1 else -1, volume=p.tradeData.volume, price=p.price,
                        stop=p.stopLoss, label=p.tradeData.label, open_ts=p.tradeData.openTimestamp)
                   for p in rec.position]
            return dict(balance=tr.balance / 10 ** tr.moneyDigits, positions=pos,
                        symbols={v: k for k, v in names.items()})
        return self._session(work)

    # ------------------------------------------------------------------ ordres
    def execute(self, orders: list[OrderIntent], closes: list[CloseIntent]):
        from twisted.internet import defer

        @defer.inlineCallbacks
        def work(client, M, P):
            syms = P.extract((yield client.send(M.ProtoOASymbolsListReq(ctidTraderAccountId=self.account_id))))
            ids = {s.symbolName: s.symbolId for s in syms.symbol}
            out = []
            for c in closes:
                rec = P.extract((yield client.send(M.ProtoOAReconcileReq(ctidTraderAccountId=self.account_id))))
                for p in rec.position:
                    if p.positionId == c.position_id:
                        r = yield client.send(M.ProtoOAClosePositionReq(ctidTraderAccountId=self.account_id,
                                                                        positionId=p.positionId,
                                                                        volume=p.tradeData.volume))
                        out.append(dict(action="fermeture", sym=c.sym, position_id=p.positionId,
                                        reponse=str(P.extract(r))[:300], raison=c.comment))
            for o in orders:
                sid = ids.get(broker_symbol(o.sym))
                if sid is None:
                    out.append(dict(action="ordre", sym=o.sym, statut="symbole introuvable chez le broker"))
                    continue
                info = P.extract((yield client.send(M.ProtoOASymbolByIdReq(ctidTraderAccountId=self.account_id,
                                                                          symbolId=[sid])))).symbol[0]
                vol = int(round(o.lots * info.lotSize / info.stepVolume)) * info.stepVolume
                if vol < info.minVolume:
                    out.append(dict(action="ordre", sym=o.sym, statut=f"volume {vol} < minimum {info.minVolume}"))
                    continue
                r = yield client.send(M.ProtoOANewOrderReq(
                    ctidTraderAccountId=self.account_id, symbolId=sid, orderType=1,  # MARKET
                    tradeSide=1 if o.side > 0 else 2, volume=vol,
                    relativeStopLoss=int(round(o.stop_dist * 100000)), label="swingbot_v2",
                    comment=o.comment[:100]), responseTimeoutInSeconds=15)
                out.append(dict(action="ordre", sym=o.sym, volume=vol, reponse=str(P.extract(r))[:300]))
            return out
        return self._session(work)


# noms des symboles chez IC Markets (cTrader) quand ils diffèrent des nôtres
BROKER_NAMES = {"NAS100": "USTEC", "GER40": "DE40"}


def broker_symbol(sym: str) -> str:
    return BROKER_NAMES.get(sym, sym)


def our_symbol(name: str) -> str:
    inv = {v: k for k, v in BROKER_NAMES.items()}
    return inv.get(name, name)


def make_broker(equity_hint: float = 1000.0):
    mode = trading_mode()
    return PaperBroker(equity_hint) if mode == "paper" else CTraderBroker(mode)
