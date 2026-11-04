# Faire tourner le Swingbot sous MetaTrader 5 (compte DÉMO)

**Système par défaut : v4** (`SWINGBOT_SYSTEM=v4`), une tendance lente sur les **futures CFD sans swap**
WTI, BRENT, DXY et GC (or), proposés par IC Markets **uniquement sur MT4/MT5**.
Vérifiez dans l'Observateur de marché (clic droit, « Symboles ») le nom exact de ces 4 futures. S'il diffère
(par exemple avec un mois d'échéance), indiquez-le :
`$env:SWINGBOT_MT5_MAP='{"WTIF":"WTI_Z6","BRENTF":"BRENT_F7","DXYF":"DXY_Z6","GOLDF":"GC_Z6"}'`
Ces futures **expirent** : le broker clôture la position au prix de règlement. Au passage suivant, le bot
le détecte et rouvre la position sur le contrat suivant si la tendance est toujours là. À surveiller les premiers mois.
Le volume est recalculé par MT5 à partir de la valeur du tick : le risque en euros est donc exact.
L'ancien système v2 reste disponible avec `SWINGBOT_SYSTEM=v2` (déconseillé : il a échoué sur le 2e univers réservé).

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

---

## 🆕 L'Expert MT5 « SwingbotV4 » (recommandé : tourne en continu dans MT5)

1. Récupérer `SwingbotV4.ex5`. Il est déjà compilé dans l'onglet **Actions** de GitHub (dernière exécution, section « Artifacts »). Sinon, ouvrir `mt5/SwingbotV4.mq5` dans MetaEditor (touche F4 dans MT5) puis appuyer sur F7.
2. Dans MT5 : **Fichier → Ouvrir le dossier des données → MQL5 → Experts**, et y copier `SwingbotV4.ex5` (ou le `.mq5`).
3. Dans le Navigateur (Ctrl+N), clic droit sur « Experts » puis **Actualiser** : *SwingbotV4* apparaît.
4. Ouvrir un graphique (n'importe lequel, par exemple XAUUSD D1) et glisser *SwingbotV4* dessus.
   Onglet « Commun » : cocher **Autoriser le trading algorithmique**. Onglet « Paramètres » : tous les réglages sont là.
5. Dans la barre d'outils, le bouton **Trading algorithmique** doit être vert.

**Deux modes** (paramètre `InpMode`) :
- **Automatique** (défaut) : l'Expert cherche chez le broker les futures compatibles (WTI, BRENT, GC, DXY), ouvre un **graphique D1 pour chacun**, y dessine les canaux et l'état, et trade chacun **indépendamment**. Il suffit de le poser sur **un seul** graphique.
- **Graphique** : posez l'Expert sur chaque graphique (WTI, BRENT, GC, DXY) ; chaque copie ne gère que son instrument. Si une copie « Automatique » tourne déjà, les copies « Graphique » ne tradent pas, pour éviter les doublons.

**Le panneau** (en haut à gauche) affiche le compte, l'équité, le drawdown, l'état du coupe-circuit et chaque instrument. Trois boutons : **Pause des entrées**, **Réarmer le coupe-circuit**, **Fermer les positions du bot**.

**Rester actif 24 h/24** : l'Expert tourne tant que MT5 est ouvert. Pour ne jamais l'éteindre, utilisez un VPS : MT5 → clic droit sur le compte → « Enregistrer un serveur virtuel ». Sinon, laissez le PC allumé avec MT5 ouvert. À la réouverture de MT5, les graphiques et l'Expert reviennent tout seuls.

**Journal** : `MQL5\Files\swingbot_journal.csv`, plus l'onglet « Experts » de la boîte à outils.
Au démarrage, l'Expert écrit pour chaque future : la taille du contrat, le lot minimum, la valeur du tick et l'échéance. **Lisez ces lignes** : si le lot minimum représente trop de risque pour votre capital, les signaux seront refusés et le journal l'indiquera.

## 🆕 L'application Windows « Swingbot.exe » (alternative en Python)
- Récupérez-la dans l'onglet **Actions** de GitHub (Artifacts « Swingbot-windows ») et placez-la dans un dossier à elle.
- Double-clic : une fenêtre de réglages s'ouvre (système, mode papier/démo, capital, risque, heure, symboles…).
- **Démarrer le service** : l'application reste ouverte et lance le bot chaque jour de semaine à l'heure choisie.
- **Tester MT5** vérifie la connexion au terminal (le terminal doit être ouvert et connecté au compte démo).
- Données et journaux sont créés à côté du `.exe`.

⚠️ N'utilisez **pas** l'Expert et l'application en même temps sur le même compte : vous auriez des positions en double.

## Erreur « (-6, 'Terminal: Authorization failed') » dans Swingbot.exe
Le module MT5 s'est branché sur un terminal **qui n'est connecté à aucun compte**.
- Gardez **ouvert** le MT5 connecté à votre compte **démo IC Markets** (en bas à droite : la connexion doit afficher des Ko/s, pas « Pas de connexion »).
- L'application essaie toute seule **chaque MT5 ouvert** et garde celui qui est connecté. Si plusieurs MT5 sont installés, vous pouvez aussi choisir le bon `terminal64.exe` dans la liste « Terminal MT5 ».
- Vous pouvez saisir **identifiant + serveur + mot de passe** pour que l'application connecte le terminal elle-même. Le mot de passe n'est **jamais** enregistré.
- Le bouton **Tester MT5** affiche le compte trouvé et vérifie que WTI, BRENT, DXY et GC existent chez le broker. S'il manque un instrument, il liste les symboles proches.
- Les fichiers de l'application vont dans le dossier `SwingbotData`, à côté du `.exe`.
