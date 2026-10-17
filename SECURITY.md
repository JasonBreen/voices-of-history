# Is it safe? — Security & transparency

> The original Player2 path remains local by default. Optional cloud providers send the
> selected briefing and dialogue to their configured service. ChatGPT sign-in stores tokens
> with Windows DPAPI in ignored `middleware/user-data/`; compatible-provider credentials
> are read only from an explicitly named environment variable. Never commit either.
> Usage/action journals and generated-installation backups are local runtime data.
> Read [extension boundaries](docs/EXTENSIONS.md) before enabling them.

This project is **open source** so you don't have to take our word for it: you can read every line,
run it from source, or build the launcher yourself. This page explains exactly what the companion
app does on your PC, what it touches, and — just as importantly — what it does **not** do.

## What the companion app does
The companion app (the `middleware/` chat window, started by the launcher) connects the in-game mod
to a **local AI**. Concretely, while you play it:

- **Reads** Hearts of Iron IV's log file `…/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log`.
  That's how it sees the game state the mod writes out (your nation, the world, your decisions).
- **Sends commands to the game** the only way a HoI4 mod can without an official API: it writes a
  small text file (`aidiplo.txt`) inside your HoI4 folder and makes the game run it by briefly
  focusing the HoI4 window and typing the console command `run aidiplo.txt`. The file's contents are
  plain text you can open and read — only standard HoI4 console effects/events, nothing else.
- While it types that one command, it **briefly blocks keyboard/mouse input** (a fraction of a
  second) so a stray keypress can't corrupt the command. This is the only reason it asks for
  **administrator rights** (the UAC prompt). You can decline — it still works, just with weaker
  typing protection.
- **Writes** the mod's localisation file (the dynamic leader/nation names shown in events).
- **Talks to your local AI** through the **Player2** app over `localhost` (your own machine).
- **Stores a small local file** (`ai_diplo_memory.sqlite3`) with the chat/plan history for your save.

## What it does NOT do
- **No app telemetry or analytics.** Conversations stay local with the default local endpoint.
  Optional cloud providers receive the selected context; account and provider policies apply.
- **Network access:** the configured local/remote model endpoint, optional OpenAI authorization/
  JWKS/model/inference endpoints, and dependency installation from PyPI. Source references
  are read locally, and only bounded matched excerpts join an opted-in provider request.
- Runtime writes are limited to configured game/mod command/localisation paths and app data,
  including local memory, budgets, action journals, optional encrypted tokens and owned-install backups.
- **It does not read or send your personal files.**

## Dependencies
Standard, widely-used PyPI packages (see `middleware/requirements.txt`):
`requests`, `pydirectinput`, `PyGetWindow`, `pyautogui`, `pyperclip`. They are used to read the log,
focus the game window and simulate the keystrokes for the `run` command.

## How to verify it yourself
- **Read the code.** It's all here — `middleware/` (the app) and `launcher.py` (the launcher). Plain
  Python, no obfuscation.
- **Run from source instead of the .exe**, if you prefer not to trust a binary:
  ```
  cd middleware
  pip install -r requirements.txt
  python main.py
  ```
- **Build the launcher yourself** so the `.exe` is one you produced from this source:
  ```
  python build_launcher.py
  ```
- **Inspect what gets typed into the game**: open the generated `aidiplo.txt` in your HoI4 folder —
  it only ever contains standard console commands.

## Reporting an issue
Found something concerning? Please open a GitHub Issue (or a private security advisory) with the
details and we'll address it.
