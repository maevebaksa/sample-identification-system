import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sis.gcode_meta import parse_gcode_settings  # noqa: E402

GLOBAL = ("[general]\nversion = 4\nname = Standard Quality #2\ndefinition = creality_ender3\n\n"
          "[metadata]\ntype = quality_changes\nquality_type = standard\n\n"
          "[values]\nadhesion_type = brim\nlayer_height = 0.28\n")
EXTRUDER = ("[general]\nversion = 4\nname = Standard Quality #2\n\n"
            "[values]\ninfill_sparse_density = 30\nmaterial_print_temperature = 215\n")


def make_cura_file(path, double_escape=True):
    blob = json.dumps({"global_quality": GLOBAL, "extruder_quality": [EXTRUDER]})
    if double_escape:                       # Cura double-escapes newlines inside the JSON strings
        blob = blob.replace("\\n", "\\\\n")
    chunks = [blob[i:i + 70] for i in range(0, len(blob), 70)]   # ...and wraps the long line
    text = (";FLAVOR:Marlin\n;TIME:1234\n;Filament used: 1.23m\n;Layer height: 0.28\n"
            ";Generated with Cura_SteamEngine 5.7.1\nM140 S60\nM190 S60\nM104 S215\nM109 S215\nG28\n"
            + "G1 X1\n" * 50 + "".join(";SETTING_3 " + c + "\n" for c in chunks))
    path.write_text(text)


def check(s):
    assert s["slicer"] == "Cura" and s["slicer_version"] == "5.7.1"
    assert s["layer_height"] == "0.28" and s["infill_sparse_density"] == "30"
    assert s["material_print_temperature"] == "215" and s["adhesion_type"] == "brim"
    assert s["cura.profile_name"] == "Standard Quality #2" and s["cura.time"] == "1234"
    assert s["gcode.first_nozzle_temp"] == "215" and s["gcode.first_bed_temp"] == "60"


def test_cura_double_escaped(tmp_path):
    p = tmp_path / "c.gcode"
    make_cura_file(p, True)
    check(parse_gcode_settings(p))


def test_cura_single_escaped(tmp_path):
    p = tmp_path / "c.gcode"
    make_cura_file(p, False)
    check(parse_gcode_settings(p))
