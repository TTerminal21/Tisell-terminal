# Moving the terminal to the Windows PC

Everything is cross-platform Python; the only thing that cannot travel is the
virtual environment, which holds compiled macOS binaries.

## 1. What to copy

Copy the whole `Tisell terminal` folder **except `.venv`**.

| Item | Copy? | Why |
| --- | --- | --- |
| `.venv/` (743 MB) | **No** | macOS binaries. Rebuilt on Windows in step 3. |
| `data-layer/terminal.duckdb` (152 MB) | Yes | All your history. DuckDB files are platform-independent. |
| `.env` | Yes | **Your API keys.** Secret — see the warning below. |
| `backups/` (18 MB) | Optional | Old database copies. |
| everything else | Yes | Code, config, watchlist. Small. |

That comes to about **61 MB zipped** (the database compresses well), against 914 MB for the folder as it sits.

Quickest way, from the project folder on the Mac:

```bash
zip -r ~/Desktop/tisell-terminal.zip . -x ".venv/*" "backups/*" "**/__pycache__/*" ".DS_Store"
```

> **About `.env`** — it holds your Tiingo, FMP, FRED and Fiscal.ai keys in plain
> text. Move it over a channel you control (direct cable, your own cloud drive,
> a drive you keep). Don't email it or leave it on a USB stick you might lose.
> If it ever does get loose, regenerate the keys at each provider — they're all
> free-tier and take a minute to rotate.

## 2. Install Python

Get **Python 3.11 or newer** from [python.org/downloads](https://www.python.org/downloads/).

During install, tick **"Add python.exe to PATH"** on the first screen. This is
the step people miss, and without it nothing below works.

Verify in a new Command Prompt:

```
python --version
```

## 3. Build the environment

Open Command Prompt **in the project folder** (type `cmd` in Explorer's address
bar and press Enter), then:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

This takes a few minutes and downloads roughly 700 MB of packages.

## 4. Run it

Double-click **`Tisell Terminal.bat`**.

It starts both halves, waits, and opens your browser at
<http://localhost:8501>. Right-click it → *Pin to Start* or send a shortcut to
your desktop if you want one-click access.

**Keep the black window open** — closing it stops the terminal. Minimise it.

## Notes for the Windows machine

- **Firewall.** Windows Defender may ask to allow Python on first run. Private
  networks only is enough; nothing needs to reach the internet inbound.
- **Scheduled refresh.** Task Scheduler can run the daily update. Action:
  `C:\path\to\.venv\Scripts\python.exe`, arguments `data-layer\refresh.py`,
  "Start in" set to the project folder.
- **Backups.** `backup.py` works the same. Point it somewhere real:
  `.venv\Scripts\python data-layer\backup.py --dest D:\backups --keep 6`
- **The GPU.** The brief planned CuPy Monte Carlo on the RTX card. Nothing uses
  it yet — the CPU path is fast enough at current sizes — so there is nothing
  extra to install for now.
- **Both machines.** They are independent copies. If you use the Mac too, they
  will drift apart; copy `terminal.duckdb` across, or just run a refresh on
  whichever machine you are on.

## If the database will not open

DuckDB refuses a file written by a *newer* version than the one installed.
If that happens, either pin the version:

```
.venv\Scripts\pip install duckdb==1.5.5
```

...or skip copying `terminal.duckdb` entirely and rebuild the data on Windows:

```
.venv\Scripts\python data-layer\refresh.py --full --full-history
```

That takes about 40 minutes and costs no API quota for prices.
