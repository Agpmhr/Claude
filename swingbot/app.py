"""Swingbot : application Windows (Swingbot.exe) avec fenêtre de réglages.

- reste ouverte et exécute le bot chaque jour de semaine à l'heure choisie (UTC), après la clôture D1 ;
- se connecte toute seule au terminal MetaTrader 5 ouvert (ou cTrader) ;
- tous les réglages sont dans la fenêtre et enregistrés dans swingbot_settings.json (à côté du .exe) ;
- le mode RÉEL reste verrouillé : il faut taper la phrase de confirmation.
Aucun mot de passe n'est enregistré : connectez d'abord votre terminal MT5 à votre compte DÉMO.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime

from .paths import ROOT

SETTINGS = ROOT / "swingbot_settings.json"
DEFAULTS = dict(systeme="v4", broker="mt5", mode="paper", capital=10000.0, risque_pct=0.25, heure_utc="22:15",
                mt5_suffixe="", mt5_map="", mt5_path="", news="none", confirm_reel="", demarrage_auto=False)


def load_settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS.exists():
        try:
            s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
        except Exception:
            pass
    return s


def apply_env(s: dict):
    """Les réglages de la fenêtre deviennent les variables lues par le bot."""
    os.environ["SWINGBOT_SYSTEM"] = s["systeme"]
    os.environ["SWINGBOT_BROKER"] = s["broker"]
    os.environ["SWINGBOT_MODE"] = s["mode"]
    os.environ["SWINGBOT_CAPITAL"] = str(s["capital"])
    os.environ["SWINGBOT_RISQUE_PCT"] = str(s["risque_pct"])
    os.environ["SWINGBOT_MT5_SUFFIX"] = s["mt5_suffixe"]
    os.environ["NEWS_LLM"] = s["news"]
    if s["mt5_map"].strip():
        os.environ["SWINGBOT_MT5_MAP"] = s["mt5_map"].strip()
    else:
        os.environ.pop("SWINGBOT_MT5_MAP", None)
    if s["mt5_path"].strip():
        os.environ["MT5_PATH"] = s["mt5_path"].strip()
    if s["mode"] == "live":
        os.environ["SWINGBOT_LIVE_CONFIRM"] = s["confirm_reel"]
    else:
        os.environ.pop("SWINGBOT_LIVE_CONFIRM", None)


def make_app():
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk

    class App(tk.Tk):
      def __init__(self):
          super().__init__()
          self.title("Swingbot v4 — tendance lente sur futures sans swap")
          self.geometry("880x640")
          self.s = load_settings()
          self.q: queue.Queue[str] = queue.Queue()
          self.stop = threading.Event()
          self.worker: threading.Thread | None = None
          self.vars: dict[str, tk.Variable] = {}
          self._build()
          self.after(300, self._pump)
          if self.s.get("demarrage_auto"):
              self.after(1000, self.start)

      # ------------------------------------------------------------------ interface
      def _field(self, parent, row, label, key, kind="entry", values=None, width=28, help_=""):
          ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=3)
          if kind == "check":
              v = tk.BooleanVar(value=bool(self.s[key]))
              w = ttk.Checkbutton(parent, variable=v)
          elif kind == "combo":
              v = tk.StringVar(value=str(self.s[key]))
              w = ttk.Combobox(parent, textvariable=v, values=values, width=width - 3, state="readonly")
          else:
              v = tk.StringVar(value=str(self.s[key]))
              w = ttk.Entry(parent, textvariable=v, width=width)
          w.grid(row=row, column=1, sticky="w", padx=6, pady=3)
          if help_:
              ttk.Label(parent, text=help_, foreground="#666").grid(row=row, column=2, sticky="w", padx=6)
          self.vars[key] = v

      def _build(self):
          top = ttk.LabelFrame(self, text="Réglages")
          top.pack(fill="x", padx=8, pady=6)
          r = 0
          self._field(top, r, "Système", "systeme", "combo", ["v4", "v2"], help_="v4 = recommandé (voir RAPPORT.md)"); r += 1
          self._field(top, r, "Plateforme", "broker", "combo", ["mt5", "ctrader"], help_="MT5 : terminal ouvert et connecté"); r += 1
          self._field(top, r, "Mode", "mode", "combo", ["paper", "demo", "live"],
                      help_="paper = aucun ordre ; demo = compte démo vérifié"); r += 1
          self._field(top, r, "Capital (mode papier)", "capital"); r += 1
          self._field(top, r, "Risque par signal (%)", "risque_pct", help_="v4 testé à 0,25 %. Ne pas dépasser 1 %."); r += 1
          self._field(top, r, "Heure d'exécution (UTC)", "heure_utc", help_="22:15 = après la clôture D1 (23:15 Paris l'hiver)"); r += 1
          self._field(top, r, "Suffixe des symboles MT5", "mt5_suffixe", help_="ex. .a  (vide si aucun)"); r += 1
          self._field(top, r, "Correspondance symboles (JSON)", "mt5_map", width=40,
                      help_='ex. {"WTIF":"WTI_Z6"}  (vide = automatique)'); r += 1
          self._field(top, r, "Chemin terminal64.exe", "mt5_path", width=40, help_="vide = terminal déjà ouvert"); r += 1
          self._field(top, r, "Lecteur de news (défense)", "news", "combo", ["none", "ollama", "anthropic"]); r += 1
          self._field(top, r, "Confirmation mode RÉEL", "confirm_reel", width=40,
                      help_="taper JE_COMPRENDS_LES_RISQUES (déconseillé)"); r += 1
          self._field(top, r, "Démarrer le service à l'ouverture", "demarrage_auto", "check"); r += 1

          btn = ttk.Frame(self)
          btn.pack(fill="x", padx=8)
          for text, cmd in [("💾 Enregistrer", self.save), ("▶ Démarrer le service", self.start),
                            ("⏹ Arrêter", self.halt), ("⚡ Exécuter maintenant", self.run_now),
                            ("🔌 Tester MT5", self.test_mt5), ("📂 Dossier", self.open_folder)]:
              ttk.Button(btn, text=text, command=cmd).pack(side="left", padx=3, pady=4)
          self.status = tk.StringVar(value="Service arrêté")
          ttk.Label(self, textvariable=self.status, foreground="#0a5").pack(fill="x", padx=10)
          self.log = scrolledtext.ScrolledText(self, height=18, font=("Consolas", 9))
          self.log.pack(fill="both", expand=True, padx=8, pady=6)
          self.write(f"Dossier de travail : {ROOT}")

      # ------------------------------------------------------------------ actions
      def read_form(self) -> dict:
          s = dict(self.s)
          for k, v in self.vars.items():
              s[k] = v.get()
          try:
              s["capital"] = float(str(s["capital"]).replace(",", "."))
              s["risque_pct"] = float(str(s["risque_pct"]).replace(",", "."))
          except ValueError:
              raise ValueError("Capital et risque doivent être des nombres")
          if not 0 < s["risque_pct"] <= 1.0:
              raise ValueError("Risque par signal : entre 0 et 1 %")
          datetime.strptime(s["heure_utc"], "%H:%M")
          if s["mt5_map"].strip():
              json.loads(s["mt5_map"])
          if s["mode"] == "live" and s["confirm_reel"] != "JE_COMPRENDS_LES_RISQUES":
              raise ValueError("Mode RÉEL verrouillé : tapez la phrase de confirmation exacte.")
          return s

      def save(self) -> bool:
          try:
              self.s = self.read_form()
          except Exception as e:
              messagebox.showerror("Swingbot", str(e))
              return False
          SETTINGS.write_text(json.dumps({k: v for k, v in self.s.items() if k != "confirm_reel"}, indent=1),
                              encoding="utf-8")
          apply_env(self.s)
          self.write("Réglages enregistrés.")
          return True

      def write(self, msg: str):
          self.q.put(f"{datetime.now():%H:%M:%S}  {msg}")

      def _pump(self):
          while not self.q.empty():
              self.log.insert("end", self.q.get() + "\n")
              self.log.see("end")
          self.after(300, self._pump)

      def _run_kwargs(self):
          return dict(capital=self.s["capital"], system=self.s["systeme"])

      def start(self):
          if self.worker and self.worker.is_alive():
              self.write("Le service tourne déjà.")
              return
          if not self.save():
              return
          from .live import loop
          self.stop.clear()

          def work():
              loop(self.s["heure_utc"], self.stop, log=self.write, run_kwargs=self._run_kwargs())
              self.write("Service arrêté.")
          self.worker = threading.Thread(target=work, daemon=True)
          self.worker.start()
          self.status.set(f"Service ACTIF — système {self.s['systeme']}, mode {self.s['mode']}, "
                          f"chaque jour de semaine à {self.s['heure_utc']} UTC")

      def halt(self):
          self.stop.set()
          self.status.set("Service arrêté")

      def run_now(self):
          if not self.save():
              return

          def work():
              try:
                  from .live import run
                  self.write("Exécution immédiate…")
                  self.write(f"Résultat : {run(**self._run_kwargs())}")
              except Exception as e:
                  self.write(f"ERREUR : {e!r}")
          threading.Thread(target=work, daemon=True).start()

      def test_mt5(self):
          if not self.save():
              return

          def work():
              try:
                  from .mt5_broker import MT5Broker
                  mode = self.s["mode"] if self.s["mode"] != "paper" else "demo"
                  snap = MT5Broker(mode).snapshot()
                  self.write(f"MT5 OK : compte {'DÉMO' if snap.get('is_demo') else 'RÉEL'}, équité {snap['balance']:.2f} "
                             f"{snap.get('currency', '')}, {len(snap['positions'])} position(s) du bot")
              except Exception as e:
                  self.write(f"MT5 : échec — {e}")
          threading.Thread(target=work, daemon=True).start()

      def open_folder(self):
          if sys.platform.startswith("win"):
              os.startfile(str(ROOT))  # noqa: S606
          else:
              subprocess.Popen(["xdg-open", str(ROOT)])

    return App()


def main():
    make_app().mainloop()


if __name__ == "__main__":
    main()
