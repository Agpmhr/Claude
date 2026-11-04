//+------------------------------------------------------------------+
//|  SwingbotV4.mq5                                                   |
//|  Tendance lente (Donchian 55/100/150) sur futures CFD SANS swap   |
//|  (IC Markets : WTI, BRENT, GC, DXY) — DÉMO par défaut.            |
//|                                                                    |
//|  Signaux : calculés sur la série CONTINUE (ex. XTIUSD au comptant)|
//|  Ordres  : passés sur le future du broker (ex. WTI), stop en % du |
//|            prix, volume calculé avec la valeur du tick (risque     |
//|            exact en devise du compte).                             |
//|  Échéances : la position est roulée sur le contrat suivant N jours|
//|            avant l'expiration.                                     |
//|  Sécurité : refuse un compte réel sauf réglage volontaire ;       |
//|            coupe-circuit jour / semaine / drawdown max ;           |
//|            ne touche QUE ses positions (numéros magiques).         |
//+------------------------------------------------------------------+
#property copyright   "Swingbot — Olivier & Yanis"
#property version     "1.00"
#property description "Swingbot v4 : tendance lente sur futures CFD sans swap (WTI, Brent, or, dollar)."
#property description "Démo par défaut. Voir mt5/LISEZMOI_MT5.md"

#include <Trade\Trade.mqh>

enum ENUM_SB_MODE
{
   SB_AUTO  = 0,   // Automatique : tous les instruments compatibles (un graphique ouvert pour chacun)
   SB_CHART = 1    // Seulement l'instrument de CE graphique (poser l'Expert sur chaque graphique)
};

//--- paramètres modifiables dans MT5 (double-clic sur l'Expert > onglet Paramètres)
input group "=== Instruments ==="
input ENUM_SB_MODE InpMode   = SB_AUTO;
input bool   InpOpenCharts   = true;   // mode automatique : ouvrir un graphique D1 par instrument
input string InpPairs        = "";     // vide = détection auto ; sinon « Signal>Future;... » ex. XTIUSD>WTI;XAUUSD>GC
input string InpCompatible   = "XTIUSD>WTI;XBRUSD>BRENT;XAUUSD>GC;SYNTH_DXY>DXY"; // table de compatibilité testée
input bool   InpSpotFallback = false;  // si aucun future : trader le CFD au comptant (SWAPS PAYÉS, non validé !)
input string InpFxSuffix     = "";        // suffixe des paires forex (ex. ".a") pour le dollar synthétique
input group "=== Stratégie (ne pas modifier sans re-tester) ==="
input string InpLookbacks    = "55,100,150"; // canaux de Donchian (jours)
input double InpStopATR      = 3.0;          // stop = N x ATR
input int    InpATRPeriod    = 20;
input int    InpMaxBars      = 250;          // sortie au temps (séances)
input group "=== Risque ==="
input double InpRiskPct      = 0.25;   // % du capital risqué par signal
input double InpMaxOpenRisk  = 4.0;    // % de risque total ouvert maximum
input int    InpMaxPositions = 12;
input double InpMaxRoundUp   = 1.5;    // lot minimum accepté s'il risque <= 1,5x le risque voulu
input double InpDailyStop    = 3.0;    // % de perte du jour -> plus d'entrée aujourd'hui
input double InpWeeklyStop   = 6.0;    // % de perte de la semaine -> plus d'entrée cette semaine
input double InpMaxDD        = 20.0;   // % de drawdown -> ARRÊT (bouton « Réarmer » pour repartir)
input group "=== Exécution ==="
input bool   InpTradingOn    = true;   // interrupteur général des NOUVELLES entrées
input bool   InpPaperMode    = false;  // true = signaux seulement, aucun ordre
input int    InpRollDays     = 3;      // rouler la position N jours avant l'échéance du future
input int    InpDeviation    = 30;     // glissement max (points)
input ulong  InpMagicBase    = 20261105;
input int    InpTimerSec     = 60;
input group "=== Sécurité compte réel ==="
input bool   InpAllowReal    = false;  // autoriser un compte RÉEL
input string InpRealConfirm  = "";     // taper exactement : JE_COMPRENDS_LES_RISQUES

//--- état
#define MAXP 8
#define MAXL 4
string   gSig[MAXP], gTrPrefix[MAXP], gTr[MAXP];
datetime gLastBar[MAXP];
string   gLastInfo[MAXP];
long     gChart[MAXP];
double   gLvl[MAXP][4];        // canal haut/bas (le plus long), canal de sortie haut/bas
int      gLook[MAXL];
int      gNP = 0, gNL = 0;
bool     gPaused = false;
CTrade   trade;
string   PFX = "SBv4_";

//+------------------------------------------------------------------+
void Log(string what)
{
   Print("[Swingbot] ", what);
   int h = FileOpen("swingbot_journal.csv", FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_SHARE_READ|FILE_SHARE_WRITE, ';');
   if(h != INVALID_HANDLE)
   {
      FileSeek(h, 0, SEEK_END);
      FileWrite(h, TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS), AccountInfoDouble(ACCOUNT_EQUITY), what);
      FileClose(h);
   }
}

double GV(string name, double def)
{
   string n = PFX + name + "_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   if(GlobalVariableCheck(n)) return GlobalVariableGet(n);
   return def;
}
void GVSet(string name, double v)
{
   GlobalVariableSet(PFX + name + "_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)), v);
}

//+------------------------------------------------------------------+
//| Choix du contrat future : nom exact, ou le contrat le plus proche  |
//| de l'échéance (mais à plus de InpRollDays jours) parmi les noms    |
//| qui commencent par le préfixe (ex. WTI -> WTI_Z6, WTI_F7...).      |
//+------------------------------------------------------------------+
string ResolveTrade(string prefix, datetime after)
{
   string best = "";
   datetime bestExp = 0;
   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
   {
      string s = SymbolName(i, false);
      if(StringFind(s, prefix) != 0) continue;
      if(s != prefix)
      {
         ushort c = StringGetCharacter(s, StringLen(prefix));
         if(c != '_' && c != '.' && c != '-' && c != ' ' && !(c >= '0' && c <= '9')) continue;
      }
      datetime exp = (datetime)SymbolInfoInteger(s, SYMBOL_EXPIRATION_TIME);
      if(exp == 0)
      {
         if(best == "") best = s;            // symbole sans échéance : accepté s'il n'y a rien de mieux
         continue;
      }
      if(exp <= after) continue;
      if(bestExp == 0 || exp < bestExp) { best = s; bestExp = exp; }
   }
   if(best != "") SymbolSelect(best, true);
   return best;
}

datetime RollLimit() { return TimeCurrent() + InpRollDays * 86400; }

// Une instance « automatique » qui tourne interdit aux instances « graphique » de trader (pas de doublons)
bool AutoInstanceAlive() { return TimeCurrent() - (datetime)GV("autoBeat", 0) < 5 * 60; }

// Liste les symboles du broker qui ressemblent à du pétrole, de l'or ou du dollar (pour trouver les bons noms)
string Diagnose()
{
   string keys[] = {"WTI", "BRENT", "OIL", "XTI", "XBR", "CL", "GC", "GOLD", "XAU", "DXY", "USDX", "DOLLAR"};
   string out = "";
   int found = 0, total = SymbolsTotal(false);
   for(int i = 0; i < total && found < 40; i++)
   {
      string sname = SymbolName(i, false);
      string up = sname; StringToUpper(up);
      string desc = SymbolInfoString(sname, SYMBOL_DESCRIPTION); StringToUpper(desc);
      bool hit = false;
      for(int k = 0; k < ArraySize(keys) && !hit; k++)
         hit = StringFind(up, keys[k]) == 0 || StringFind(desc, keys[k]) >= 0;
      if(!hit) continue;
      datetime exp = (datetime)SymbolInfoInteger(sname, SYMBOL_EXPIRATION_TIME);
      string line = StringFormat("%s  [%s]  %s%s", sname, SymbolInfoString(sname, SYMBOL_PATH),
                                 SymbolInfoString(sname, SYMBOL_DESCRIPTION),
                                 exp > 0 ? "  échéance " + TimeToString(exp, TIME_DATE) : "");
      Log("Symbole candidat : " + line);
      out += sname + (exp > 0 ? "(" + TimeToString(exp, TIME_DATE) + ")" : "") + "  ";
      found++;
   }
   Log(StringFormat("Diagnostic : %d symboles candidats sur %d chez ce broker", found, total));
   return out;
}

bool SignalAvailable(string sig)
{
   if(sig == "SYNTH_DXY") return SymbolSelect("EURUSD" + InpFxSuffix, true) && SymbolSelect("USDJPY" + InpFxSuffix, true);
   return SymbolSelect(sig, true);
}

//+------------------------------------------------------------------+
//| Séries D1 du signal : index 0 = dernière bougie CLÔTURÉE           |
//+------------------------------------------------------------------+
bool GetSeries(string sig, int count, double &hi[], double &lo[], double &cl[], datetime &barTime)
{
   ArrayResize(hi, count); ArrayResize(lo, count); ArrayResize(cl, count);
   if(sig == "SYNTH_DXY")
   {
      // Indice dollar ICE reconstruit à partir des paires forex (clôtures seulement)
      string fx[6] = {"EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF"};
      double w[6]  = {-0.576, 0.136, -0.119, 0.091, 0.042, 0.036};
      for(int k = 0; k < 6; k++) SymbolSelect(fx[k] + InpFxSuffix, true);
      barTime = iTime(fx[0] + InpFxSuffix, PERIOD_D1, 1);
      if(barTime == 0) return false;
      for(int j = 0; j < count; j++)
      {
         double v = 50.14348112;
         for(int k = 0; k < 6; k++)
         {
            double c = iClose(fx[k] + InpFxSuffix, PERIOD_D1, j + 1);
            if(c <= 0) { if(k == 4) continue; return false; }   // USDSEK facultatif
            v *= MathPow(c, w[k]);
         }
         hi[j] = v; lo[j] = v; cl[j] = v;
      }
      return true;
   }
   SymbolSelect(sig, true);
   double h[], l[], c[];
   ArraySetAsSeries(h, true); ArraySetAsSeries(l, true); ArraySetAsSeries(c, true);
   if(CopyHigh(sig, PERIOD_D1, 1, count, h) != count) return false;
   if(CopyLow(sig, PERIOD_D1, 1, count, l) != count) return false;
   if(CopyClose(sig, PERIOD_D1, 1, count, c) != count) return false;
   for(int j = 0; j < count; j++) { hi[j] = h[j]; lo[j] = l[j]; cl[j] = c[j]; }
   barTime = iTime(sig, PERIOD_D1, 1);
   return barTime > 0;
}

// ATR de Wilder (comme le backtest Python), à la dernière bougie clôturée
double WilderATR(const double &hi[], const double &lo[], const double &cl[], int n)
{
   int cnt = ArraySize(cl);
   if(cnt < n + 2) return 0;
   double atr = 0;
   int start = cnt - 2;                       // plus ancienne bougie utilisable (index série)
   double alpha = 1.0 / n;
   bool init = false;
   for(int j = start; j >= 0; j--)
   {
      double pc = cl[j + 1];
      double tr = MathMax(hi[j] - lo[j], MathMax(MathAbs(hi[j] - pc), MathAbs(lo[j] - pc)));
      if(!init) { atr = tr; init = true; }
      else atr = alpha * tr + (1 - alpha) * atr;
   }
   return atr;
}

double Highest(const double &a[], int from, int n) { double m = -DBL_MAX; for(int j = from; j < from + n; j++) m = MathMax(m, a[j]); return m; }
double Lowest(const double &a[], int from, int n)  { double m =  DBL_MAX; for(int j = from; j < from + n; j++) m = MathMin(m, a[j]); return m; }

//+------------------------------------------------------------------+
//| Positions du bot                                                   |
//+------------------------------------------------------------------+
bool IsOurMagic(long mg) { return mg >= (long)InpMagicBase && mg < (long)InpMagicBase + MAXL; }

ulong FindPos(int p, int l, long &dir, datetime &opened, string &sym)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)(InpMagicBase + l)) continue;
      string s = PositionGetString(POSITION_SYMBOL);
      if(StringFind(s, gTrPrefix[p]) != 0) continue;
      dir = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
      opened = (datetime)PositionGetInteger(POSITION_TIME);
      sym = s;
      return tk;
   }
   return 0;
}

int CountOurPositions(double &openRisk)
{
   int n = 0; openRisk = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || !IsOurMagic(PositionGetInteger(POSITION_MAGIC))) continue;
      n++;
      string s = PositionGetString(POSITION_SYMBOL);
      double sl = PositionGetDouble(POSITION_SL), po = PositionGetDouble(POSITION_PRICE_OPEN);
      double ts = SymbolInfoDouble(s, SYMBOL_TRADE_TICK_SIZE), tv = SymbolInfoDouble(s, SYMBOL_TRADE_TICK_VALUE_LOSS);
      if(sl > 0 && ts > 0) openRisk += MathAbs(po - sl) / ts * tv * PositionGetDouble(POSITION_VOLUME);
   }
   return n;
}

//+------------------------------------------------------------------+
//| Ouvre une position : stop en % du prix, volume par valeur du tick |
//+------------------------------------------------------------------+
bool OpenPos(string sym, int dir, double stopPct, double riskMoney, ulong magic, string why)
{
   if(InpPaperMode) { Log("PAPIER " + why + " " + sym + (dir > 0 ? " ACHAT" : " VENTE")); return true; }
   MqlTick t;
   if(!SymbolInfoTick(sym, t)) { Log("Pas de prix pour " + sym); return false; }
   double price = dir > 0 ? t.ask : t.bid;
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double dist = MathMax(price * stopPct, (double)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) * point * 1.5);
   double ts = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double tv = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE_LOSS);
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN), vstep = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   if(ts <= 0 || tv <= 0 || vstep <= 0) { Log("Spécifications invalides pour " + sym); return false; }
   double lossPerLot = dist / ts * tv;
   double vol = MathFloor(riskMoney / lossPerLot / vstep) * vstep;
   if(vol < vmin)
   {
      if(vmin * lossPerLot <= InpMaxRoundUp * riskMoney) vol = vmin;
      else
      {
         Log(StringFormat("REFUS %s %s : lot minimum %.2f = %.2f de risque pour %.2f voulus", why, sym, vmin,
                          vmin * lossPerLot, riskMoney));
         return true;   // pas une erreur réessayable
      }
   }
   vol = MathMin(vol, vmax);
   double sl = NormalizeDouble(price - dir * dist, digits);
   trade.SetExpertMagicNumber(magic);
   trade.SetDeviationInPoints(InpDeviation);
   trade.SetTypeFillingBySymbol(sym);
   bool ok = dir > 0 ? trade.Buy(vol, sym, 0, sl, 0, "SBv4 " + why) : trade.Sell(vol, sym, 0, sl, 0, "SBv4 " + why);
   Log(StringFormat("%s %s %s vol=%.2f stop=%s risque=%.2f retcode=%d %s", ok ? "OUVERT" : "ECHEC", why,
                    sym, vol, DoubleToString(sl, digits), vol * lossPerLot, trade.ResultRetcode(),
                    trade.ResultRetcodeDescription()));
   return ok;
}

bool ClosePos(ulong ticket, string why)
{
   if(InpPaperMode) { Log("PAPIER fermeture " + why); return true; }
   trade.SetDeviationInPoints(InpDeviation);
   bool ok = trade.PositionClose(ticket, InpDeviation);
   Log(StringFormat("%s fermeture #%I64u : %s (retcode %d)", ok ? "OK" : "ECHEC", ticket, why, trade.ResultRetcode()));
   return ok;
}

//+------------------------------------------------------------------+
//| Coupe-circuit                                                      |
//+------------------------------------------------------------------+
string gBreaker = "";
bool EntriesAllowed()
{
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   double today = (double)(dt.year * 10000 + dt.mon * 100 + dt.day);
   if(GV("day", 0) != today) { GVSet("day", today); GVSet("dayEq", eq); }
   // semaine : on repère le lundi
   datetime monday = TimeCurrent() - ((dt.day_of_week + 6) % 7) * 86400;
   MqlDateTime dm; TimeToStruct(monday, dm);
   double wk = (double)(dm.year * 10000 + dm.mon * 100 + dm.day);
   if(GV("week", 0) != wk) { GVSet("week", wk); GVSet("weekEq", eq); }
   double peak = MathMax(GV("peak", eq), eq);
   GVSet("peak", peak);
   if(eq <= peak * (1 - InpMaxDD / 100.0)) GVSet("halted", 1);
   gBreaker = "";
   if(GV("halted", 0) > 0) gBreaker = StringFormat("ARRÊT : drawdown >= %.0f %% (bouton Réarmer)", InpMaxDD);
   else if(eq <= GV("dayEq", eq) * (1 - InpDailyStop / 100.0)) gBreaker = "pause : perte du jour";
   else if(eq <= GV("weekEq", eq) * (1 - InpWeeklyStop / 100.0)) gBreaker = "pause : perte de la semaine";
   return gBreaker == "" && InpTradingOn && !gPaused;
}

//+------------------------------------------------------------------+
//| Traitement d'un instrument à chaque nouvelle bougie D1             |
//+------------------------------------------------------------------+
bool ProcessPair(int p)
{
   int maxL = 0;
   for(int l = 0; l < gNL; l++) maxL = MathMax(maxL, gLook[l]);
   int need = maxL + 320;
   double hi[], lo[], cl[];
   datetime bt;
   if(!GetSeries(gSig[p], need, hi, lo, cl, bt)) { gLastInfo[p] = "historique insuffisant / non chargé"; return false; }
   if(bt == gLastBar[p]) return true;   // déjà traité
   double atr = WilderATR(hi, lo, cl, InpATRPeriod);
   if(atr <= 0) { gLastInfo[p] = "ATR indisponible"; return false; }
   double stopPct = InpStopATR * atr / cl[0];
   int nMax = gLook[0];
   for(int l = 1; l < gNL; l++) nMax = MathMax(nMax, gLook[l]);
   gLvl[p][0] = Highest(hi, 0, nMax); gLvl[p][1] = Lowest(lo, 0, nMax); gLvl[p][3] = cl[0];
   gTr[p] = ResolveTrade(gTrPrefix[p], RollLimit());
   if(gTr[p] == "") { gLastInfo[p] = "future « " + gTrPrefix[p] + " » introuvable"; return false; }
   if(InpMode == SB_CHART && AutoInstanceAlive())
   {
      gLastInfo[p] = "une instance AUTOMATIQUE tourne déjà : cette copie ne trade pas (pas de doublon)";
      return true;
   }
   bool allow = EntriesAllowed();
   double openRisk; int nPos = CountOurPositions(openRisk);
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   bool retry = false;
   string info = StringFormat("ATR %.2f %%  stop %.2f %%", 100 * atr / cl[0], 100 * stopPct);
   for(int l = 0; l < gNL; l++)
   {
      int n = gLook[l];
      int m = MathMax(5, n / 2);
      double hiN = Highest(hi, 1, n), loN = Lowest(lo, 1, n);
      double hiM = Highest(hi, 1, m), loM = Lowest(lo, 1, m);
      int entry = cl[0] > hiN ? 1 : (cl[0] < loN ? -1 : 0);
      bool exitLong = cl[0] < loM, exitShort = cl[0] > hiM;
      long dir; datetime opened; string psym;
      ulong tk = FindPos(p, l, dir, opened, psym);
      ulong magic = InpMagicBase + l;
      string tag = StringFormat("D%d %s", n, gSig[p]);
      if(tk > 0)
      {
         int held = iBarShift(gSig[p] == "SYNTH_DXY" ? "EURUSD" + InpFxSuffix : gSig[p], PERIOD_D1, opened, false);
         string why = "";
         if((dir > 0 && exitLong) || (dir < 0 && exitShort)) why = "sortie canal " + IntegerToString(m) + " j";
         else if(held >= InpMaxBars) why = "durée max";
         else if(entry == -dir) why = "retournement";
         if(why != "")
         {
            if(!ClosePos(tk, tag + " " + why)) retry = true;
            else if(entry == -dir && allow && nPos < InpMaxPositions)
            {
               if(!OpenPos(gTr[p], entry, stopPct, eq * InpRiskPct / 100.0, magic, tag + " retournement")) retry = true;
            }
            continue;
         }
         // roulement : la position est sur un contrat qui expire bientôt
         datetime exp = (datetime)SymbolInfoInteger(psym, SYMBOL_EXPIRATION_TIME);
         if(exp > 0 && exp <= RollLimit() && psym != gTr[p])
         {
            if(ClosePos(tk, tag + " roulement " + psym + " -> " + gTr[p]))
            {
               if(!OpenPos(gTr[p], (int)dir, stopPct, eq * InpRiskPct / 100.0, magic, tag + " roulement")) retry = true;
            }
            else retry = true;
         }
         info += StringFormat(" | D%d:%s", n, dir > 0 ? "LONG" : "SHORT");
         continue;
      }
      if(entry == 0) { info += StringFormat(" | D%d:-", n); continue; }
      if(!allow) { info += StringFormat(" | D%d:signal bloqué", n); Log(tag + " signal bloqué : " + (gBreaker != "" ? gBreaker : "pause / interrupteur")); continue; }
      double risk = eq * InpRiskPct / 100.0;
      if(nPos >= InpMaxPositions || openRisk + risk > eq * InpMaxOpenRisk / 100.0)
      {
         Log(tag + " refusé : limites de portefeuille");
         continue;
      }
      if(OpenPos(gTr[p], entry, stopPct, risk, magic, tag + (entry > 0 ? " cassure haute" : " cassure basse")))
      { nPos++; openRisk += risk; }
      else retry = true;
      info += StringFormat(" | D%d:ENTRÉE", n);
   }
   gLastInfo[p] = info;
   if(!retry) gLastBar[p] = bt;   // sinon on réessaie au prochain passage (marché fermé, requote...)
   return !retry;
}

//+------------------------------------------------------------------+
//| Graphiques par instrument (mode automatique)                       |
//+------------------------------------------------------------------+
void OpenCharts()
{
   for(int p = 0; p < gNP; p++)
   {
      long found = 0;
      for(long c = ChartFirst(); c >= 0; c = ChartNext(c))
         if(ChartSymbol(c) == gTr[p]) { found = c; break; }
      if(found == 0 && gTr[p] != _Symbol) found = ChartOpen(gTr[p], PERIOD_D1);
      if(gTr[p] == _Symbol) found = ChartID();
      gChart[p] = found;
      if(found > 0) ChartSetInteger(found, CHART_SHOW_GRID, false);
   }
}

void HLine(long chart, string name, double price, color clr, ENUM_LINE_STYLE style, string text)
{
   if(chart <= 0 || price <= 0) return;
   if(ObjectFind(chart, name) < 0) ObjectCreate(chart, name, OBJ_HLINE, 0, 0, price);
   ObjectSetDouble(chart, name, OBJPROP_PRICE, price);
   ObjectSetInteger(chart, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(chart, name, OBJPROP_STYLE, style);
   ObjectSetString(chart, name, OBJPROP_TEXT, text);
   ObjectSetString(chart, name, OBJPROP_TOOLTIP, text);
}

void DrawInstrument(int p)
{
   long c = gChart[p];
   if(c <= 0) return;
   // niveaux convertis du signal vers le prix du future (même rapport que le dernier cours)
   double sigLast = gLvl[p][0] > 0 ? 1 : 0;
   string nm = PFX + gTrPrefix[p] + "_";
   if(sigLast > 0)
   {
      double ratio = SymbolInfoDouble(gTr[p], SYMBOL_BID) / MathMax(gLvl[p][3], 1e-12);
      if(gSig[p] == gTr[p]) ratio = 1;
      HLine(c, nm + "hi", gLvl[p][0] * ratio, clrLimeGreen, STYLE_DASH, "Swingbot : cassure HAUTE (achat)");
      HLine(c, nm + "lo", gLvl[p][1] * ratio, clrTomato, STYLE_DASH, "Swingbot : cassure BASSE (vente)");
   }
   string lbl = nm + "info";
   if(ObjectFind(c, lbl) < 0) ObjectCreate(c, lbl, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(c, lbl, OBJPROP_CORNER, CORNER_LEFT_LOWER);
   ObjectSetInteger(c, lbl, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(c, lbl, OBJPROP_YDISTANCE, 30);
   ObjectSetInteger(c, lbl, OBJPROP_COLOR, clrGold);
   ObjectSetInteger(c, lbl, OBJPROP_FONTSIZE, 10);
   ObjectSetString(c, lbl, OBJPROP_TEXT, "SWINGBOT v4 [" + gSig[p] + "] " + gLastInfo[p]);
   ChartRedraw(c);
}

//+------------------------------------------------------------------+
//| Panneau et boutons                                                 |
//+------------------------------------------------------------------+
void Button(string name, string text, int x, int y, color bg)
{
   if(ObjectFind(0, name) < 0) ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, 150);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, 26);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clrWhite);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_STATE, false);
}

void DrawPanel()
{
   bool demo = AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO;
   double eq = AccountInfoDouble(ACCOUNT_EQUITY), peak = GV("peak", eq);
   double openRisk; int n = CountOurPositions(openRisk);
   string s = "SWINGBOT v4  —  tendance lente sur futures sans swap  —  mode " +
              (InpMode == SB_AUTO ? "AUTOMATIQUE (tous les instruments compatibles)" : "GRAPHIQUE (" + _Symbol + ")") + "\n";
   s += StringFormat("Compte %s  |  %s  |  Équité %.2f %s  |  Pic %.2f  |  DD %.1f %%\n",
                     demo ? "DÉMO" : "RÉEL", InpPaperMode ? "MODE PAPIER (aucun ordre)" : "ordres actifs",
                     eq, AccountInfoString(ACCOUNT_CURRENCY), peak, 100 * (eq / peak - 1));
   s += StringFormat("Positions du bot : %d  |  risque ouvert %.2f (%.2f %%)  |  risque/signal %.2f %%\n",
                     n, openRisk, 100 * openRisk / MathMax(eq, 1), InpRiskPct);
   string st = gPaused ? "EN PAUSE (bouton)" : (!InpTradingOn ? "entrées désactivées (paramètre)" :
               (gBreaker != "" ? gBreaker : "actif"));
   s += "État : " + st + "   |   AutoTrading : " +
        (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && MQLInfoInteger(MQL_TRADE_ALLOWED) ? "OK" : "DÉSACTIVÉ !") + "\n";
   for(int p = 0; p < gNP; p++)
      s += StringFormat("%-10s -> %-10s  %s\n", gSig[p], gTr[p] == "" ? "?" : gTr[p], gLastInfo[p]);
   s += "Journal : Fichier > Ouvrir le dossier des données > MQL5\\Files\\swingbot_journal.csv";
   Comment(s);
   Button(PFX + "pause", gPaused ? "▶ Reprendre" : "⏸ Pause entrées", 160, 30, gPaused ? clrSeaGreen : clrDarkOrange);
   Button(PFX + "rearm", "Réarmer coupe-circuit", 160, 60, clrSteelBlue);
   Button(PFX + "close", "✖ Fermer positions bot", 160, 90, clrFireBrick);
   ChartRedraw();
}

//+------------------------------------------------------------------+
int OnInit()
{
   bool demo = AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO;
   if(!demo && !(InpAllowReal && InpRealConfirm == "JE_COMPRENDS_LES_RISQUES"))
   {
      Alert("Swingbot : compte RÉEL détecté. Refusé par sécurité (paramètres InpAllowReal + InpRealConfirm).");
      return INIT_FAILED;
   }
   string parts[], lk[];
   string src = InpPairs != "" ? InpPairs : InpCompatible;
   int nAll = MathMin(StringSplit(src, ';', parts), MAXP);
   gNP = 0;
   string chartSym = _Symbol;
   for(int i = 0; i < nAll; i++)
   {
      string ab[];
      if(StringSplit(parts[i], '>', ab) != 2) { Alert("Paire mal écrite : ", parts[i]); return INIT_PARAMETERS_INCORRECT; }
      StringTrimLeft(ab[0]); StringTrimRight(ab[0]); StringTrimLeft(ab[1]); StringTrimRight(ab[1]);
      if(InpMode == SB_CHART && StringFind(chartSym, ab[1]) != 0 && chartSym != ab[0]) continue;
      string tr = ResolveTrade(ab[1], RollLimit());
      bool sigOk = SignalAvailable(ab[0]);
      if(tr == "" && InpSpotFallback && sigOk && ab[0] != "SYNTH_DXY")
      {
         tr = ab[0];
         Log("ATTENTION : future « " + ab[1] + " » absent -> repli sur le CFD au comptant " + tr +
             " (swaps payés chaque nuit : ce n'est PAS la stratégie validée par le backtest)");
      }
      if(tr == "" || !sigOk)
      {
         Log("Non compatible (ignoré) : " + ab[0] + " > " + ab[1] + " : " +
             (tr == "" ? "aucun future dont le nom commence par « " + ab[1] + " »" : "") +
             (!sigOk ? " ; série de signal « " + ab[0] + " » introuvable" : ""));
         continue;
      }
      int k = gNP++;
      gSig[k] = ab[0]; gTrPrefix[k] = ab[1]; gTr[k] = tr; gLastBar[k] = 0; gLastInfo[k] = "en attente"; gChart[k] = 0;
      Log(StringFormat("%s -> %s : contrat=%.2f lot min=%.2f pas=%.2f tick=%g valeur tick=%.4f %s échéance=%s",
          gSig[k], gTr[k], SymbolInfoDouble(gTr[k], SYMBOL_TRADE_CONTRACT_SIZE),
          SymbolInfoDouble(gTr[k], SYMBOL_VOLUME_MIN), SymbolInfoDouble(gTr[k], SYMBOL_VOLUME_STEP),
          SymbolInfoDouble(gTr[k], SYMBOL_TRADE_TICK_SIZE), SymbolInfoDouble(gTr[k], SYMBOL_TRADE_TICK_VALUE_LOSS),
          AccountInfoString(ACCOUNT_CURRENCY),
          TimeToString((datetime)SymbolInfoInteger(gTr[k], SYMBOL_EXPIRATION_TIME), TIME_DATE)));
   }
   if(gNP == 0)
   {
      string cand = Diagnose();
      Alert("Swingbot : aucun future compatible trouvé. Symboles proches chez ce broker : ",
            cand == "" ? "(aucun)" : cand,
            "  -> voir l'onglet Experts, puis renseigner le paramètre InpPairs (ex. XTIUSD>NOM_DU_FUTURE).");
      Comment("SWINGBOT v4 : aucun future compatible trouvé.\n"
              "Symboles proches : " + (cand == "" ? "(aucun)" : cand) + "\n"
              "Ouvrez l'onglet « Experts » (lignes « Symbole candidat ») et renseignez InpPairs,\n"
              "ou activez InpSpotFallback (CFD au comptant, swaps payés, NON validé).");
      return INIT_SUCCEEDED;   // on reste attaché pour que le message reste visible (aucun trade)
   }
   if(InpMode == SB_AUTO && InpOpenCharts) OpenCharts();
   gNL = MathMin(StringSplit(InpLookbacks, ',', lk), MAXL);
   for(int l = 0; l < gNL; l++) gLook[l] = (int)StringToInteger(lk[l]);
   gPaused = GV("paused", 0) > 0;
   EventSetTimer(MathMax(10, InpTimerSec));
   Log(StringFormat("Démarrage : compte %s, %d instruments, canaux %s, risque %.2f %%/signal, %s",
       demo ? "DÉMO" : "RÉEL", gNP, InpLookbacks, InpRiskPct, InpPaperMode ? "MODE PAPIER" : "ordres actifs"));
   OnTimer();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(InpMode == SB_AUTO) GVSet("autoBeat", 0);
   for(int p = 0; p < gNP; p++) if(gChart[p] > 0) ObjectsDeleteAll(gChart[p], PFX);
   ObjectsDeleteAll(0, PFX);
   Comment("");
}

void OnTimer()
{
   if(InpMode == SB_AUTO) GVSet("autoBeat", (double)TimeCurrent());
   EntriesAllowed();
   for(int p = 0; p < gNP; p++)
   {
      ProcessPair(p);
      DrawInstrument(p);
   }
   DrawPanel();
}

void OnTick() { }   // tout est piloté par le minuteur (indépendant du graphique choisi)

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id != CHARTEVENT_OBJECT_CLICK) return;
   if(sparam == PFX + "pause")
   {
      gPaused = !gPaused; GVSet("paused", gPaused ? 1 : 0);
      Log(gPaused ? "Pause des entrées (bouton)" : "Reprise des entrées (bouton)");
   }
   else if(sparam == PFX + "rearm")
   {
      double eq = AccountInfoDouble(ACCOUNT_EQUITY);
      GVSet("halted", 0); GVSet("peak", eq); GVSet("dayEq", eq); GVSet("weekEq", eq);
      Log("Coupe-circuit réarmé manuellement");
   }
   else if(sparam == PFX + "close")
   {
      if(MessageBox("Fermer TOUTES les positions du Swingbot ?", "Swingbot", MB_YESNO | MB_ICONWARNING) == IDYES)
      {
         for(int i = PositionsTotal() - 1; i >= 0; i--)
         {
            ulong tk = PositionGetTicket(i);
            if(tk > 0 && IsOurMagic(PositionGetInteger(POSITION_MAGIC))) ClosePos(tk, "fermeture manuelle (bouton)");
         }
         gPaused = true; GVSet("paused", 1);
      }
   }
   DrawPanel();
}
//+------------------------------------------------------------------+
