"""
free_miner.py - free Autolykos2 GPU miner (ERG), with a disclosed 5% developer fee.

Mining runs to the USER's own wallet, entered on first launch and saved locally.
For 5% of total runtime, mining is pointed at the developer's wallet instead.
This is shown continuously in the window while it runs - never hidden.

Requires: lolMiner (auto-detected or pick via file dialog), NVIDIA GPU, Windows.
"""
import os, sys, json, glob, time, threading, subprocess, urllib.request, tkinter as tk
from pathlib import Path

# ===================== DEV SETTINGS (fill in before distributing) =====================
# The dev fee pays out in BTC (via Unmineable's auto-convert), regardless of which
# algo the user picks to mine with - so one BTC address covers every algo below.
DEV_BTC_WALLET = "16oaeAiYkEcoGJxBbDJwFxEKNyCpi6fEBh"
DEV_FEE_PCT    = 5              # % of total runtime that mines to DEV_BTC_WALLET
API_PORT       = 4074
REF            = ""             # optional Unmineable referral code, blank = none
CONFIG_PATH    = Path(os.getenv("APPDATA", str(Path.home()))) / "free_miner_config.json"

# All algos confirmed working on an RTX 5060 in the companion dashboard (etc_miner).
# sub = Unmineable's real subdomain for that algo; everything routes through
# Unmineable so the SAME dev BTC address works no matter which algo mines.
ALGOS = {
    "AUTOLYKOS2": {"label": "Autolykos2 (ERGO) - fastest confirmed, ~113 MH/s", "sub": "autolykos"},
    "ETCHASH":    {"label": "Etchash (ETC)",      "sub": "etchash"},
    "ETHASH":     {"label": "Ethash (ETHW)",       "sub": "ethash"},
    "FISHHASH":   {"label": "FishHash (IRON)",     "sub": "fishhash"},
    "OCTOPUS":    {"label": "Octopus (CFX)",       "sub": "octopus"},
    "NEXAPOW":    {"label": "NexaPoW (NEXA)",      "sub": "nexapow"},
    "BEAM":       {"label": "BeamHash III (BEAM)", "sub": "beamhashiii"},
}
DEFAULT_ALGO = "AUTOLYKOS2"

# Coins the USER can choose to be paid in (Unmineable auto-converts for them, same
# as it does for the dev fee below). Add/remove freely - any Unmineable coin works.
PAYOUT_COINS = ["BTC", "ETH", "LTC", "DOGE", "SOL", "XMR", "ERG"]
DEFAULT_PAYOUT = "BTC"
# =========================================================================================

def unmineable_pool_user(coin, addr, sub):
    host = f"{sub}.unmineable.com"
    tag = f"{addr}.free_miner" + (f"#{REF}" if REF else "")
    return host, 3333, f"{coin}:{tag}"

def unmineable_pool_dev(sub):
    host = f"{sub}.unmineable.com"
    tag = f"{DEV_BTC_WALLET}.free_miner_fee" + (f"#{REF}" if REF else "")
    return host, 3333, f"BTC:{tag}"

def find_lolminer():
    here = os.path.dirname(os.path.abspath(__file__))
    home = str(Path.home())
    for r in [here, os.getcwd(), home, str(Path.home() / "Desktop"), str(Path.home() / "Downloads")]:
        for depth in ("", "*", os.path.join("*", "*")):
            hits = glob.glob(os.path.join(r, depth, "lolMiner.exe"))
            if hits:
                return hits[0]
    try:
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw()
        path = filedialog.askopenfilename(title="Select lolMiner.exe", filetypes=[("lolMiner", "lolMiner.exe")])
        root.destroy()
        return path or None
    except Exception:
        return None

def load_wallet():
    if CONFIG_PATH.exists():
        try:
            d = json.loads(CONFIG_PATH.read_text())
            if d.get("wallet") and d.get("coin") and d.get("algo") in ALGOS:
                return d
        except Exception:
            pass
    return None

def save_wallet(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg))

def ask_setup():
    result = {}
    win = tk.Tk(); win.title("Free Miner - Setup"); win.configure(bg="#1b1b1b")

    tk.Label(win, text="Your wallet address:", bg="#1b1b1b", fg="white", font=("Segoe UI", 11)).pack(padx=20, pady=(20, 4))
    entry = tk.Entry(win, width=50); entry.pack(padx=20, pady=2)

    tk.Label(win, text="Pay me in:", bg="#1b1b1b", fg="white", font=("Segoe UI", 10)).pack(padx=20, pady=(10, 2))
    coin_var = tk.StringVar(value=DEFAULT_PAYOUT)
    tk.OptionMenu(win, coin_var, *PAYOUT_COINS).pack(padx=20)

    tk.Label(win, text="Algorithm to mine:", bg="#1b1b1b", fg="white", font=("Segoe UI", 10)).pack(padx=20, pady=(10, 2))
    algo_var = tk.StringVar(value=DEFAULT_ALGO)
    tk.OptionMenu(win, algo_var, *ALGOS.keys()).pack(padx=20)

    tk.Label(win, text=f"This miner is free to use.\nA {DEV_FEE_PCT}% developer fee runs in the background to support ongoing development\n"
                        "(you'll see this clearly in the main window at all times).",
             bg="#1b1b1b", fg="#aaaaaa", font=("Segoe UI", 9), justify="left").pack(padx=20, pady=10)

    def go():
        result["wallet"] = entry.get().strip()
        result["coin"] = coin_var.get()
        result["algo"] = algo_var.get()
        win.destroy()
    tk.Button(win, text="Start Mining", command=go, bg="#2a7a2a", fg="white", padx=10, pady=5).pack(pady=(0, 20))
    win.mainloop()
    return result if result.get("wallet") else None

def fee_schedule(total_cycle_sec=1200):
    """Returns True if the CURRENT moment should mine to the dev wallet."""
    dev_sec = total_cycle_sec * DEV_FEE_PCT / 100
    pos = time.time() % total_cycle_sec
    return pos < dev_sec

class MinerApp:
    def __init__(self, exe, user_wallet, coin, algo_key):
        self.exe = exe
        self.user_wallet = user_wallet
        self.coin = coin
        self.algo_key = algo_key
        self.algo_sub = ALGOS[algo_key]["sub"]
        self.proc = None
        self.current_target = "user"
        self.root = tk.Tk()
        self.root.title(f"Free Miner ({ALGOS[algo_key]['label']})")
        self.root.configure(bg="#1b1b1b")
        self.root.geometry("480x220")

        tk.Label(self.root, text="FREE MINER", bg="#1b1b1b", fg="#ffd24d", font=("Segoe UI", 14, "bold")).pack(pady=(14, 2))
        self.fee_label = tk.Label(self.root, text=f"Developer fee: {DEV_FEE_PCT}% of runtime mines to the developer's wallet",
                                   bg="#1b1b1b", fg="#ff8080", font=("Segoe UI", 10, "bold"))
        self.fee_label.pack(pady=2)
        self.status = tk.Label(self.root, text=f"Mining {ALGOS[algo_key]['label']}, paid in {coin}", bg="#1b1b1b", fg="white", font=("Segoe UI", 10))
        self.status.pack(pady=6)
        self.hash_label = tk.Label(self.root, text="Hashrate: --", bg="#1b1b1b", fg="#7ad0ff", font=("Segoe UI", 11))
        self.hash_label.pack(pady=2)
        self.target_label = tk.Label(self.root, text="Mining to: your wallet", bg="#1b1b1b", fg="#7aff8a", font=("Segoe UI", 10))
        self.target_label.pack(pady=2)

        self.running = True
        threading.Thread(target=self.mine_loop, daemon=True).start()
        threading.Thread(target=self.poll_loop, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    def launch(self, to_dev):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
        if to_dev:
            host, port, user = unmineable_pool_dev(self.algo_sub)
        else:
            host, port, user = unmineable_pool_user(self.coin, self.user_wallet, self.algo_sub)
        pool = f"{host}:{port}"
        cmd = [self.exe, "--algo", self.algo_key, "--pool", pool, "--user", user,
               "--pass", "x", "--apiport", str(API_PORT)]
        self.proc = subprocess.Popen(cmd, cwd=os.path.dirname(self.exe),
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def mine_loop(self):
        while self.running:
            to_dev = fee_schedule()
            target = "dev" if to_dev else "user"
            if target != self.current_target:
                self.current_target = target
                self.launch(to_dev)
                label = "developer's wallet (fee, BTC)" if to_dev else f"your wallet ({self.coin})"
                color = "#ff8080" if to_dev else "#7aff8a"
                self.root.after(0, lambda l=label, c=color: self.target_label.config(text=f"Mining to: {l}", fg=c))
            time.sleep(10)

    def poll_loop(self):
        while self.running:
            hr = "--"
            try:
                d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}", timeout=3).read())
                hr = str(d["Algorithms"][0]["Total_Performance"][0]) + " MH/s"
            except Exception:
                pass
            self.root.after(0, lambda h=hr: self.hash_label.config(text=f"Hashrate: {h}"))
            time.sleep(5)

    def on_close(self):
        self.running = False
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.root.destroy()

def main():
    if DEV_BTC_WALLET.startswith("PASTE"):
        print("Set DEV_BTC_WALLET at the top of this file before distributing it."); input(); return
    exe = find_lolminer()
    if not exe:
        print("lolMiner.exe not found."); input(); return
    cfg = load_wallet()
    if not cfg:
        cfg = ask_setup()
        if not cfg:
            return
        save_wallet(cfg)
    MinerApp(exe, cfg["wallet"], cfg["coin"], cfg["algo"])

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        input("\nPress Enter to close...")
