"""Card 1353: identifier findings fail CI via hygiene_gate.yml, not ci.yml."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = ("si4ai/.github/.github/workflows/hygiene.yml@"
       "23c056780f193d140e042491ac81a59cafb71a14")


def test_hygiene_gate_is_strict_and_ci_yml_stays_report_only():
    gate = (ROOT / ".github/workflows/hygiene_gate.yml").read_text()
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert PIN in gate and "strict: true" in gate
    assert PIN in ci
    assert "strict: true" not in ci
