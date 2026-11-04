# Faire tourner le Swingbot v2 sous MetaTrader 5 (compte DÉMO)

1. **Installer** (une fois) :
   ```powershell
   cd C:\chemin\vers\Claude
   python -m venv venv ; venv\Scripts\activate
   pip install pandas numpy scipy lightgbm scikit-learn matplotlib pytest MetaTrader5
   python scripts\fetch_rates.py ; python scripts\fetch_yahoo.py ; python -m swingbot.calendar_events
   ```
2. **Ouvrir MetaTrader 5**, se connecter au compte **DÉMO** IC Markets, et cocher
   *Outils → Options → Expert Advisors → « Autoriser le trading algorithmique »*.
3. **Vérifier les noms des symboles** dans l'Observateur de marché : `EURUSD`, `XAUUSD`, `US500`, `USTEC`,
   `DE40`, `UK100`, `JP225`, `F40`, `STOXX50`, `US30`, `AUS200`. S'ils ont un suffixe (ex. `EURUSD.a`) :
   `$env:SWINGBOT_MT5_SUFFIX=".a"`.
4. **Recaler le backtest sur les bougies du broker** (recommandé) :
   ```powershell
   $env:SWINGBOT_MODE="demo"; python scripts\export_mt5_history.py ; python scripts\ideas_final.py
   ```
5. **Test à blanc** (aucun ordre) : `python -m swingbot.live`, puis lire `logs\live_decisions.jsonl`.
6. **Démo** : `$env:SWINGBOT_BROKER="mt5"; $env:SWINGBOT_MODE="demo"; python -m swingbot.live`.
7. **Automatiser** : Planificateur de tâches → exécuter `mt5\lancer_swingbot_mt5.bat`, du lundi au vendredi, à 23h15.
8. **Contrôler chaque semaine** : `python scripts\compare_live.py` (démo vs backtest).

Sécurité :
- le bot ne touche **que** ses positions (numéro magique 20261105), jamais vos trades manuels ;
- en mode `demo`, il **refuse de trader** si le compte connecté est un compte réel ;
- le mode réel exige `SWINGBOT_MODE=live` **et** `SWINGBOT_LIVE_CONFIRM=JE_COMPRENDS_LES_RISQUES`.
  Ne le faites pas avant 2 à 3 mois de démo conformes au backtest.
