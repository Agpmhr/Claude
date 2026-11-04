# Swingbot : rapport final (nuit du 4 au 5 novembre 2026)

## Verdict en une phrase

**Non : aucune configuration n'atteint les critères sur le bloc de test.** Le résultat est un Sharpe de 0,03 et un profit factor de 1,01 sur 2024-2026, après coûts. Ça ne vaut pas le coup de passer en démo **en espérant un revenu**. Une démo n'aurait d'intérêt que pour apprendre.

| Critère (bloc test 2024-11 → 2026-11, après tous les coûts) | Exigé | Obtenu | ✔/✘ |
|---|---|---|---|
| Profit factor | > 1,3 | 1,01 | ✘ |
| Sharpe annualisé | > 0,8 | 0,03 | ✘ |
| Drawdown max | < 20 % | 5,9 % | ✔ (mais il y a peu de trades) |
| Rentable la majorité des années et instruments | oui | non (7 instruments sur 16 gagnants, 5 années sur 14) | ✘ |
| Robustesse | oui | non (résultats instables selon les paramètres) | ✘ |
| Probabilité que le Sharpe soit réel (Deflated Sharpe, 52 essais) | élevée | **2,5 %** | ✘ |

Sur la même période, **conserver simplement** l'or (+43 %), le S&P 500 (+32 %) ou le DAX (+34 %) aurait fait bien mieux. Ces chiffres sont des variations de prix, sans frais. Attention, ce n'est pas comparable à un CFD, qui paie environ 10 % par an de financement sur les indices.

![équité](results/equity_curve.png)

*Courbe d'équité de la variante retenue, après tous les coûts. Chaque bloc est hors échantillon : l'entraînement 2013-2018 est en walk-forward, la validation est en 2019-2024 et le test, figé, en 2024-2026. La courbe est plate : le seul gros gain vient de 2015 (USD/JPY), le reste fait du surplace.*

---

## Ce qui a été fait

### Étape 1 : les données
- ⚠️ **Dukascopy était bloqué** depuis le cloud (erreur HTTP 429). J'ai utilisé **Yahoo Finance** à la place : **D1 de 2010 à 2026** pour 16 instruments (7 majeures, EURJPY, GBPJPY, AUDJPY, EURGBP, or, argent, US500, NAS100, GER40), et seulement 2 ans de H1. L'étude est donc faite en **D1**. Les fichiers que vous avez déposés (`data_bars`, `data_daily`) sont eux aussi des données Yahoo, sur des périodes plus courtes. Dans `data_h1/_raw`, il n'y a que 21 mois de Dukascopy, sur l'or seulement.
- Taux directeurs des 8 devises (FRED/OCDE) pour le carry et les swaps. Calendrier FOMC officiel (133 décisions) et NFP (204 dates reconstruites). **Pas d'historique BCE, BoE, BoJ ni CPI** accessible.
- Contrôle qualité : pas de doublons, bougies du dimanche supprimées, mèches fantômes de Yahoo rabotées. **Les vrais krachs sont conservés** : une première version du filtre les effaçait par erreur, ce qui aurait rendu le backtest trop beau.

### Étape 2 : le moteur (`swingbot/backtest.py`, `swingbot/costs.py`)
- Signal à la clôture, exécution à l'ouverture suivante, stop en ATR. Gap au-delà du stop : exécution à l'ouverture. Stop et objectif touchés dans la même bougie : on suppose le stop d'abord.
- **Vrais frais IC Markets EU**, relevés le 4 nov. 2026 sur la page officielle *Spreads and Swaps* :
  - commission cTrader de 3 $ par 100 000 $ et par côté ;
  - spreads moyens Raw : EURUSD 0,06, GBPUSD 0,26, USDJPY 0,22, or 0,08 $, US500 0,36 pt, USTEC 1,57 pt, DE40 1,46 pt ;
  - marges de swap déduites des swaps publiés : **forex ≈ 1 %/an, or ≈ 0,75 %/an, indices ≈ 5,5 %/an**. Une position acheteuse sur US500 paie environ **10 % par an** de financement.
  - Prudence ajoutée : spread ×1,5, plus 0,5 spread de glissement à l'entrée et 1,5 spread sur les stops.
- Swaps débités chaque nuit, triplés le mercredi (forex et métaux) ou le vendredi (indices).
- **Tests automatiques** (`tests/test_engine.py`, 6 tests, tous verts) :
  - aucune fuite du futur (on modifie le futur et on vérifie que les signaux passés ne bougent pas) ;
  - exécution à l'ouverture suivante ;
  - gap au-delà du stop ;
  - stop d'une vente ;
  - triple swap.

### Étape 3 : les stratégies (`swingbot/strategies.py`)
9 familles, **49 configurations** au total. Les grilles sont volontairement petites.
- Donchian, croisement de moyennes, momentum série temporelle, et sa version lente sur 12 mois, rebalancée chaque mois.
- Retour à la moyenne (z-score en range), RSI(2) dans le sens de la tendance.
- Momentum transversal, carry FX avec filtre de tendance.
- « Tendance longue » : position acheteuse uniquement sur les indices et l'or, tant que le prix est au-dessus de sa moyenne à 100 ou 200 jours. Horizon de plusieurs mois.
- Ciblage de volatilité : chaque trade risque le même montant, grâce au stop en ATR et au risque fixe en %.

**Constat, avant tout filtre IA** (R moyen par trade, entraînement → validation) : le suivi de tendance est légèrement positif **avant frais** sur 2010-2018 (+0,05 à +0,35 R). **Après frais et swaps**, il devient négatif, puis franchement négatif sur 2019-2024. Ce qui marchait sur un bloc ne marche plus sur le suivant.

### Étape 4 : la couche IA (`swingbot/ai.py`, `swingbot/portfolio.py`, `swingbot/news.py`)
- **Régime de marché** : rang de volatilité, efficacité de la tendance, volatilité moyenne de l'univers. Ce sont des seuils simples, que le modèle utilise comme caractéristiques.
- **Méta-labellisation** : LightGBM (8 feuilles, fortement régularisé), réentraîné chaque année sur le passé seulement, avec purge des trades à cheval sur deux périodes.
  - Il prédit bien la **probabilité de gain** : 25 % de gagnants dans le décile bas, 56 % dans le décile haut.
  - Mais il ne prédit **pas l'espérance** : le décile haut fait tout juste 0 R. Il trie surtout « des petits gains fréquents » contre « des gros gains rares », sans trouver d'avantage.
- **Allocateur** : classement par espérance en R, au plus 8 positions, au plus 5 % de risque ouvert, **exposition nette au plus 2 trades par devise**, marge au plus 50 %.
- **Coupe-circuit** : -3 % sur une journée, -6 % sur une semaine, -20 % de drawdown (arrêt complet).
- **Filtre calendrier** (pas d'entrée la veille ou le jour d'un FOMC/NFP) : testé, il **n'améliore pas** le résultat, donc il n'est pas retenu.
- **Lecteur de news par IA**, uniquement en défense. Il lit le calendrier de la semaine (ForexFactory) et les flux RSS (Fed, BCE, FXStreet, Investing). Le LLM est interchangeable (Ollama en local ou API Anthropic). Le code **ignore toute action autre que** bloquer, réduire, resserrer ou couper, et chaque décision est journalisée. **Il ne peut pas être backtesté** : il ne sera jugé qu'en démo.

### Étape 5 : le risque
Risque par trade de 0,75 % (modulé entre 0,5 et 1 % par le score IA), plus les limites ci-dessus. Tout se règle dans `RiskConfig` (`swingbot/portfolio.py`).

---

## Méthodologie et honnêteté

- **Trois blocs chronologiques** : entraînement 2010-2018, validation 2019 → oct. 2024, **test nov. 2024 → nov. 2026**. Le test n'a été lu **qu'une seule fois**, par `scripts/final_test.py`, avec une règle de choix figée avant : le meilleur Sharpe en validation, donc la variante D (sélection walk-forward, score IA, pas de vente des indices).
- **Walk-forward** : chaque année, pour chaque famille, on reprend la meilleure configuration des 4 années précédentes, mais seulement si elle était positive.
- **Nombre d'essais** : 49 configurations, 6 variantes de portefeuille et 3 scénarios de coûts, soit **≈ 52 à 58 essais**. Avec autant d'essais, **le hasard seul peut produire un Sharpe d'environ 1,4**. C'est pour ça que le Deflated Sharpe ne vaut que 2,5 %.
- **Variantes de portefeuille en validation** (capital 10 000 €) : aucune ne dépasse un Sharpe de 0,06 (voir `results/research_variants.csv`).
- **Petite instabilité à signaler** : la même variante D donne un Sharpe de +0,06 en validation dans `run_research.py` et de -0,29 dans `final_test.py`. La seule différence est l'inclusion des trades de validation qui se terminent pendant le test. Qu'un détail pareil fasse basculer le résultat confirme qu'il n'y a **pas d'avantage réel**, juste du bruit.

### Tests de robustesse (variante D)
| Test | Validation : Sharpe / PF | Test : Sharpe / PF |
|---|---|---|
| Base | -0,29 / 0,86 | 0,03 / 1,01 |
| Coûts ×1,5 | 0,51 / 2,51 (seulement 23 trades : le filtre IA bloque presque tout) | 0,01 / 1,04 (3 trades) |
| Paramètres -20 % | 0,22 / 1,11 | 0,64 / 1,40 |
| Paramètres +20 % | -0,23 / 0,88 | 0,55 / 1,84 |

Les résultats partent dans tous les sens selon de petites variations. C'est la signature du **bruit**, pas d'une stratégie solide. Ne vous laissez pas tenter par la ligne « -20 % » : la choisir après coup, c'est exactement le surapprentissage qu'on veut éviter.

- **Monte Carlo** sur l'ordre des trades : drawdown médian -21 %, pire cas à 5 % de probabilité : -30 %, pire cas à 1 % : -33 %.
- **Par instrument** (2013-2026) : 7 instruments gagnants sur 16. USDJPY à lui seul fait +1 505 € sur 10 000 €, NZDUSD perd -820 €, l'argent -673 €.
- **Par année** : 5 années positives sur 14. 2015 fait l'essentiel du gain total.

### Ce que ça donnerait en euros (rendements mensuels hors échantillon)
| | Mauvais mois (10 %) | Mois médian | Bon mois (90 %) |
|---|---|---|---|
| Rendement mensuel | -1,9 % | +0,08 % | +1,5 % |
| Sur 1 000 € | -19 € | +0,8 € | +15 € |
| Sur 10 000 € | -190 € | +8 € | +150 € |

Pire drawdown probable (Monte Carlo à 95 %) : **-30 %, soit -300 € sur 1 000 € et -3 000 € sur 10 000 €**, pour un gain médian proche de zéro.

### Le problème des petits comptes (votre question des 200 € / 500 €)
En swing, le stop est large : souvent 1 à 3 % du prix. Or le **lot minimum de 0,01** représente déjà 10 à 50 € de risque sur un stop de ce genre. Avec 0,75 % de risque par trade :
| Capital | Signaux refusés faute de lot assez petit (test) | Trades pris | Gain sur le test |
|---|---|---|---|
| 200 € | 98 | 6 | -3,6 € |
| 500 € | 95 | 9 | -1,2 € |
| 1 000 € | 62 | 14 | +11 € |
| 10 000 € | 15 | 36 | +8 € |

En dessous de 2 000 à 3 000 €, **le bot ne peut tout simplement pas respecter sa propre gestion du risque**. Il faudrait risquer 3 à 10 % par trade, et là, c'est du casino.

---

## Limites et risques identifiés
1. Les données Yahoo ne valent pas Dukascopy : l'or et l'argent viennent de contrats à terme continus, les indices des cours au comptant. Mais en D1, l'effet est faible devant l'ampleur des frais.
2. Les marges de swap sont celles de **novembre 2026**, appliquées à tout l'historique.
3. Pas d'historique BCE, BoE, BoJ ni CPI pour le filtre calendrier.
4. La couche news n'est pas testable en backtest.
5. **Fond du problème** : sur les CFD, les frais de financement (surtout sur les indices, ≈ 10 %/an pour une position acheteuse) mangent l'avantage connu du suivi de tendance et de la prime de risque. Ce sont des avantages faibles, qui existent sur les marchés à terme institutionnels, à frais quasi nuls.

## Étape 7 (connecteur cTrader) : **non réalisée, volontairement**
Le cahier des charges dit : « seulement si l'étape 6 est concluante ». Elle ne l'est pas. Brancher un bot perdant sur un compte, même de démo, donnerait une fausse impression de progrès.

---

## Pour reprendre demain

**État du projet** : branche `swingbot` du dépôt `Agpmhr/claude`. Elle n'a **pas pu être poussée** : l'application GitHub de Claude n'a pas les droits en écriture. Une archive git complète vous a été envoyée (`swingbot.bundle`).

```bash
# récupérer le travail depuis l'archive
git clone swingbot.bundle swingbot && cd swingbot   # ou : git fetch swingbot.bundle swingbot:swingbot
python -m venv venv && venv\Scripts\activate          # (Windows)
pip install pandas numpy scipy lightgbm scikit-learn matplotlib pytest

python scripts/fetch_rates.py        # taux FRED
python scripts/fetch_yahoo.py        # prix (ou déposez vos CSV Dukascopy dans data/dukascopy/<SYM>_D1.csv : prioritaires)
python -m swingbot.calendar_events   # calendrier FOMC/NFP
python -m pytest -q tests            # 6 tests du moteur
python scripts/run_research.py       # recherche (entraînement + validation)
python scripts/final_test.py         # ⚠️ le test final : il a DÉJÀ été consommé, le relancer ne sert qu'à reproduire
set NEWS_LLM=none & python -m swingbot.news   # lecteur de news (ollama / anthropic possibles)
```

**Si vous voulez continuer** (mes recommandations, sans enjoliver) :
1. **Avec Dukascopy complet** (H1 sur 10 ans), on pourrait tester du swing H4. Mais il faut un **nouveau bloc test** : celui de 2024-2026 est grillé. Concrètement, attendre 12 mois de données nouvelles, ou tester sur des instruments jamais regardés.
2. **Vérifier les swaps réels** dans cTrader (Symbol info), en particulier ceux des indices.
3. **Pour des petits capitaux (200 à 1 000 €)**, un bot de CFD n'est pas le bon outil. Les données le disent clairement : le seul rendement régulier de ces 15 ans, c'est la **prime de risque des actions** (et de l'or), et elle se capte sans levier ni financement nocturne, par un **ETF large acheté chaque mois** (PEA/CTO). C'est ennuyeux, mais c'est ce qui a rapporté « à la fin de l'année », pas un pari de bot. (Information générale, pas un conseil personnalisé.)
4. Le moteur, les tests anti-fuite, le lecteur de news et la méthode restent réutilisables pour toute nouvelle idée. **Chaque nouvelle idée doit passer le même tamis.**
