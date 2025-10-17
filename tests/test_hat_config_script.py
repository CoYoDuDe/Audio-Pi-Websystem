import shlex
import subprocess
from pathlib import Path


def test_hat_config_noninteractive(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "hat_config.sh"
    assert script_path.exists(), "hat_config.sh muss vorhanden sein"

    command = " ; ".join(
        [
            "set -e",
            f"export HAT_MODEL=hifiberry_dacplus",
            "export HAT_DEFAULT_SINK_HINT='default-sink'",
            f"source {shlex.quote(str(script_path))}",
            "hat_select_profile",
            'printf "key=%s\\n" "$HAT_SELECTED_KEY"',
            'printf "overlay=%s\\n" "$HAT_SELECTED_OVERLAY"',
            'printf "sink=%s\\n" "$HAT_SELECTED_SINK_HINT"',
            'printf "disable=%s\\n" "$HAT_SELECTED_DISABLE_ONBOARD"',
        ]
    )

    result = subprocess.run(
        ["bash", "-c", command],
        text=True,
        capture_output=True,
        check=True,
    )

    stdout_lines = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in result.stdout.strip().splitlines()}
    assert stdout_lines["key"] == "hifiberry_dacplus"
    assert stdout_lines["overlay"] == "hifiberry-dacplus"
    assert stdout_lines["disable"] == "1"
    assert stdout_lines["sink"].startswith("pattern:")

    stderr_output = result.stderr
    assert "Ausgewählter HAT: HiFiBerry DAC+" in stderr_output
    assert "dtoverlay-Vorschlag" in stderr_output
    assert "PulseAudio-Sink/Muster" in stderr_output
