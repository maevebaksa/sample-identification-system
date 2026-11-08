# Sample Identification System

A companion app for **Mainsail + Moonraker** that keeps a permanent record of your print tests.

- **Auto-import** – finished prints are pulled from Moonraker's job history; each gets a permanent **sample number** to write on the part.
- **Multiple printers** – add any Moonraker printer on your network (Printers page); all their prints share one sample list, and you can reprint a sample on *any* printer.
- **Settings capture** – the slicer config embedded in the gcode (PrusaSlicer, SuperSlicer, OrcaSlicer, Bambu Studio, **Cura**) plus Moonraker's file metadata and the temperatures commanded by the start G-code.
- **Safe copy** – every gcode is copied to `data_dir/gcodes/`, so deleting it from Mainsail loses nothing.
- **One-tap reprint** – re-uploads the archived file if it's gone from Mainsail, then starts it.
- **Success / fail**, title, tags and notes per sample.
- **Archive** – hide unrated/old samples you no longer have (restorable, excluded from default exports).
- **Grouped settings** – temperatures, speeds, infill, walls, cooling, retraction, etc. instead of one long list.
- **Same-model tracking** – the source model (STL/3MF) is read from the gcode (PrusaSlicer/Orca/Bambu "label objects", Cura `;MESH:`, Klipper `EXCLUDE_OBJECT`), falling back to the filename. Samples of the same model are grouped: filter the list by model, or open a sample and hit *Compare all* to line up every attempt at that part. The name is editable if it was guessed wrong.
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

## Run with Docker

A prebuilt multi-arch image (amd64, arm64, armv7) is published to GitHub Container Registry on every push to `main`:

```bash
docker run -d --name sample-identification-system --restart unless-stopped   -p 8420:8420 -v sis-data:/data ghcr.io/maevebaksa/sample-identification-system:latest
```

Or with Compose (pulls the prebuilt image; add `--build` to build from source instead):

```bash
git clone https://github.com/maevebaksa/sample-identification-system.git
cd sample-identification-system
docker compose up -d
```

Open `http://<docker-host>:8420`, go to **Printers** and add your printers by IP or hostname
(`http://192.168.1.50:7125`, or just `http://voron.local`). Data (database, gcode archive, photos)
is stored in `./data` next to the compose file.

Notes:
- Inside a container `localhost` is the container itself, so always use the printer's LAN address.
- Moonraker must allow the Docker host: add its IP (or the whole subnet, e.g. `192.168.1.0/24`) to
  `trusted_clients` in each printer's `moonraker.conf`, or give the printer an API key on the Printers page.
- Optional env vars: `SIS_MOONRAKER_URL` (pre-seed the first printer), `SIS_PUBLIC_URL` (address used in QR codes),
  `SIS_POLL_SECONDS`.

## Configuration (`config.json`, see `config.example.json`)

| key | default | meaning |
|---|---|---|
| `moonraker_url` | `http://localhost:7125` | first printer, created on first run (more can be added in the UI; leave empty to add them all in the UI) |
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
[{"title": "Sample Identification System", "href": "http://<printer-address>:8420", "target": "_blank", "position": 90,
  "icon": "M12,2L1,21H23M12,6L19.5,19H4.5"}]
```

## Workflow

1. Print a test. When it finishes (or is cancelled) it shows up as `#N` within ~30 s.
2. Write `N` on the part (or print the label).
3. Open the sample, hit **Success** / **Failed**, add a note, snap a photo.
4. Tick several samples → **Compare** to see exactly which settings differ → **Export CSV** when you want it in a spreadsheet.

Printing the same gcode again creates a new sample number automatically marked "reprint of #N".

## Cura notes

Cura only writes the settings you *changed* from the base profile into the gcode, so those (plus the profile name,
print time, layer height and the temperatures in the start G-code) are what you'll see. For the most complete
records use a slicer that embeds its whole config (PrusaSlicer/Orca/Bambu), or change the settings you care about
away from the profile defaults in Cura so they get recorded.

## Notes & limits

- Printers are polled in parallel; one being offline doesn't block the others. A printer with samples is disabled rather than deleted.

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
