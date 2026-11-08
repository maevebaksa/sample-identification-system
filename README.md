# Sample Identification System

A companion app for **Mainsail + Moonraker** that keeps a permanent record of your print tests.

- **Auto-import** – finished prints are pulled from Moonraker's job history; each gets a permanent **sample number** to write on the part.
- **Settings capture** – the slicer config embedded in the gcode (PrusaSlicer, SuperSlicer, OrcaSlicer, Bambu Studio; best-effort Cura) plus Moonraker's file metadata.
- **Safe copy** – every gcode is copied to `data_dir/gcodes/`, so deleting it from Mainsail loses nothing.
- **One-tap reprint** – re-uploads the archived file if it's gone from Mainsail, then starts it.
- **Success / fail**, title, tags and notes per sample.
- **Compare** any number of samples side by side, differing settings highlighted.
- **Export** CSV (results + key settings, or every setting) or full JSON.
- **Phone photos** – scan the QR code (or open the sample on your phone) and tap *Take photo*.
- **Printable labels** with the number and a QR code.

## Install (on the Pi running Klipper/Moonraker)

```bash
git clone https://github.com/<you>/sample-identification-system.git
cd sample-identification-system
bash deploy/install.sh
```

That creates a virtualenv, writes `config.json`, and installs a `sample-identification` systemd service on port **8420**.
Open `http://<printer-address>:8420` (from your phone too — same Wi-Fi).

Running by hand instead (also works on Windows/macOS):

```bash
pip install -r requirements.txt
python run.py            # reads ./config.json if present
```

## Configuration (`config.json`, see `config.example.json`)

| key | default | meaning |
|---|---|---|
| `moonraker_url` | `http://localhost:7125` | Moonraker API |
| `moonraker_api_key` | `""` | only if Moonraker requires one |
| `port` / `host` | `8420` / `0.0.0.0` | web UI |
| `public_url` | `""` | address phones should use in QR codes, e.g. `http://voron.local:8420` |
| `data_dir` | `~/sample-identification-data` | database, gcode archive, photos — **back this folder up** |
| `poll_seconds` | `30` | how often to check for finished prints |
| `initial_import_count` | `10` | on first run, import this many recent prints (later ones are always imported) |

Any key can also be set with an env var, e.g. `SIS_PORT=9000`.

## Link it from Mainsail

Mainsail supports custom sidebar entries via `.theme/navi.json` in your printer's config folder
(`~/printer_data/config/.theme/navi.json`). `deploy/install.sh` prints the snippet; it looks like:

```json
[{"title": "Samples", "href": "http://<printer-address>:8420", "target": "_blank", "position": 90,
  "icon": "M12,2L1,21H23M12,6L19.5,19H4.5"}]
```

## Workflow

1. Print a test. When it finishes (or is cancelled) it shows up as `#N` within ~30 s.
2. Write `N` on the part (or print the label).
3. Open the sample, hit **Success** / **Failed**, add a note, snap a photo.
4. Tick several samples → **Compare** to see exactly which settings differ → **Export CSV** when you want it in a spreadsheet.

Printing the same gcode again creates a new sample number automatically marked "reprint of #N".

## Notes & limits

- Deleting a sample *record* never deletes the archived gcode.
- If a gcode was already deleted from Mainsail before it could be archived, the sample is still created (with Moonraker's metadata) but is flagged "gcode not archived" and can't be reprinted.
- Photos are downscaled to 2000 px in the browser before upload.
- There is no login; run it on your trusted LAN only.

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest tests
python tests/mock_moonraker.py   # fake Moonraker on :7125 for trying the UI
```

MIT licensed.
