# Swingbot : rapport de travail (tenu à jour au fil de l'eau)

> ⚠️ **Accès aux données**
> - **Dukascopy est bloqué** depuis l'environnement cloud (HTTP 429 « Too Many Requests », connexions coupées). L'outil `npx dukascopy-node` n'a pas pu être lancé non plus.
> - Source de remplacement : **Yahoo Finance**. On a **16 ans de bougies D1** (2010 à nov. 2026) pour les 16 instruments, mais **seulement 2 ans de H1** (Yahoo ne fournit pas plus).
> - Conséquence : l'étude principale se fait en **D1**. Le H1 ne sert qu'à des contrôles.
> - Le **push GitHub est refusé** (l'application Claude n'a pas les droits sur `Agpmhr/claude`). Le travail est commité en local, sur la branche `swingbot`.

## Étape 1 : données ✅

- Script `scripts/fetch_yahoo.py` : D1 depuis 2010 et H1 sur 730 jours, pour 16 instruments (7 majeures, EURJPY, GBPJPY, AUDJPY, EURGBP, or, argent, US500, NAS100, GER40).
- Script `scripts/fetch_rates.py` : taux courts des 8 devises (FRED / OCDE), qui servent au carry et aux swaps. Les séries mensuelles sont décalées d'un mois pour qu'on n'utilise jamais un taux avant sa publication.
- Contrôle qualité (`swingbot/data.py`) :
  - doublons : 0 ;
  - bougies de week-end supprimées : 518 par paire FX (Yahoo publie des bougies le dimanche) ;
  - mèches aberrantes rabotées : 25 à 224 par paire (Yahoo FX a des mèches fantômes) ;
  - pics aberrants : 1 seul retiré en D1.
- **Piège évité** : une première version du filtre anti-pics effaçait les vrais krachs (mars 2020 sur le S&P, interventions sur le yen en 2026). Avec ça, le backtest aurait été trop beau. Le filtre est maintenant strict : un pic n'est retiré que s'il s'annule presque entièrement le lendemain.
- Limites de Yahoo :
  - l'or et l'argent viennent des **contrats à terme continus** (GC=F, SI=F), avec de petits sauts à chaque roulement ;
  - les indices sont les indices **au comptant** (heures de bourse seulement), alors que les CFD cotent presque 24 h/24.
- Calendrier économique (`swingbot/calendar_events.py`) :
  - **FOMC** : 133 décisions officielles (2010-2026), lues sur le site de la Fed ;
  - **NFP** : 204 dates reconstruites avec la règle du BLS ;
  - **BCE, BoE, BoJ et CPI** : pas d'historique accessible (sites dynamiques ou erreur 403), donc pas de test en backtest pour eux.

## Étape 2 : moteur de backtest (en cours)
