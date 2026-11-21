# INSTALLATION

Target platform: Linux Mint (also Ubuntu/Debian/Fedora/Arch), Intel Core i3-5005U, Intel HD 5500, 8 GB RAM, 256 GB SSD. Nothing is compiled, no model is downloaded, no GPU is used.

## 1. Prerequisites

| Requirement | Why | Install |
|---|---|---|
| Python 3.10+ | Backend | `sudo apt install python3 python3-venv python3-pip` |
| FFmpeg + FFprobe | Normalising and joining clips, duration validation | `sudo apt install ffmpeg` |
| Node 18+ (build only) | Compiling the React studio once | `sudo apt install nodejs npm` |
| ~2 GB free disk | Clips for one two-minute project are 100–400 MB; the final MP4 adds ~20–80 MB | — |

Check:

```bash
python3 -V && ffmpeg -version | head -n1 && node -v
```

## 2. Install

```bash
git clone <your-repo-url> omnireel
cd omnireel
./scripts/install.sh
```

The script creates `.venv`, installs backend dependencies, builds the frontend into `frontend/dist`, copies `.env.example` to `.env`, and copies `config/providers.json.example` to `config/providers.json`.

If you prefer doing it by hand:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..
cp .env.example .env
```

## 3. Add a provider key

Open `.env` and set at least:

```ini
AGNES_API_KEY=your-key-here
```

Free key: <https://platform.agnes-ai.com>. If your key was issued on the China site, also set `AGNES_BASE_URL=https://api.agnes-ai.cn`.

Never commit `.env` — it is git-ignored, and `LICENSE`/docs never include keys.

## 4. First run

```bash
./scripts/run.sh
```

Open <http://127.0.0.1:8000>. The header shows how many providers are configured and whether FFmpeg was found. The **Providers** tab lists every model, its clip lengths, its free status and its quota ledger.

## 5. Verify before spending quota

```bash
.venv/bin/python -m pytest backend -q                  # logic tests, no network
.venv/bin/python scripts/smoke_run.py --minutes 2      # full pipeline on mock providers
```

The smoke run prints the final path and duration; it should finish at ~120.0 s. Mock clips are colour cards — this proves the plumbing, not the AI.

Then start small: build a **60-second** project (6 × 10 s) and watch it through before attempting 120 s.

## 6. Everyday use

| Task | Command |
|---|---|
| Run the app | `./scripts/run.sh` |
| Develop UI with hot reload | `./scripts/dev.sh` (API :8000, Vite :5173) |
| Rebuild UI after frontend edits | `cd frontend && npm run build` |
| Run tests | `.venv/bin/python -m pytest backend -q` |
| Offline end-to-end check | `.venv/bin/python scripts/smoke_run.py --minutes 2` |
| Stop everything | `Ctrl+C` |

Generation continues in the background while the app runs; closing the terminal stops the worker (segments already on disk are kept, and restarting resumes).

## 7. Data locations

| Path | Contents |
|---|---|
| `data/omnireel.sqlite` | Projects, scenes, attempts, jobs, quota ledger, usage history |
| `data/clips/<project>/scene_NNN.mp4` | Raw per-scene clips, kept forever |
| `data/frames/<project>/` | Extracted continuity reference frames |
| `data/outputs/<project>/` | Normalised clips and the joined intermediate |
| `data/outputs/<project>.mp4` | **The finished two-minute film** |
| `data/audio/<project>/` | Synthesised narration |

Backing up `data/` is enough to keep every project and resume anywhere.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ffmpeg: not found` at assembly | FFmpeg missing | `sudo apt install ffmpeg`, restart |
| Project stuck on `waiting` | No provider eligible: quota cooling, health backoff, or missing key | Check the Providers tab; wait for `next_retry_at` or press Start again |
| Every scene fails with 401 | Key belongs to the other site | Set `AGNES_BASE_URL` to the site that issued the key |
| Scenes fail with queue-full | Provider's free queue is saturated | Not an error on your side; failover moves the scene, otherwise retry later |
| Storyboard says "no eligible free model" | Enabled models cannot make that clip length | Enable another provider or re-plan with a different target |
| Assembly fails with a music error | Bad `music_path` | Clear it or point at a real file |
| Narration fails | No speech engine | `sudo apt install espeak-ng`, or clear the narration script |
| UI shows a blank page | Frontend never built | `cd frontend && npm install && npm run build`, or use `./scripts/dev.sh` |
| Machine slow during generation | Normal: it is only downloading, not inferring | Lower `OMNIREEL_MAX_CONCURRENT` to 1 |

Logs go to stdout; run the app in a terminal to watch them, or `./scripts/run.sh > omnireel.log 2>&1 &`.

## 9. Autostart (optional)

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/omnireel.service <<EOF
[Unit]
Description=OmniReel AI video studio
After=network-online.target

[Service]
WorkingDirectory=$HOME/omnireel
ExecStart=$HOME/omnireel/.venv/bin/python backend/main.py
Restart=on-failure

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now omnireel
```

## 10. Upgrading

```bash
git pull
source .venv/bin/activate && pip install -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..
```

The database schema is additive; existing projects keep working. If a release ever needs a schema change, it will be noted in `CHANGELOG.md` with a migration command.
