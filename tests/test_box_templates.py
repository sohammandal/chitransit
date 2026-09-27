"""Guards for infra/box/setup.sh.tftpl, which Terraform renders with templatefile() into the
Lightsail instance's user_data.

- Any `${...}` is interpolated by Terraform, even inside a comment. An unescaped `${deploy_sh}`
  in a comment once pasted the whole deploy script into the file.
- Lightsail appends user_data to its own `#!/bin/sh` launch script (it is NOT cloud-config): a
  cloud-config file once ran as shell commands. The outer part must stay POSIX sh and never exit.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "infra/box/setup.sh.tftpl"
ALLOWED = {"${chomp(deploy_sh)}"}


def test_only_the_intended_interpolation_is_in_the_setup_template():
    text = TEMPLATE.read_text()
    # `$${` is Terraform's escape for a literal `${`; `%{` would start a directive
    found = re.findall(r"(?<!\$)\$\{[^}]*\}", text)
    assert set(found) <= ALLOWED, f"unexpected template interpolation: {set(found) - ALLOWED}"
    assert found.count("${chomp(deploy_sh)}") == 1
    assert "%{" not in text


def test_setup_is_not_cloud_config_and_never_exits_lightsails_script():
    text = TEMPLATE.read_text()
    assert "#cloud-config" not in text
    outer = text.split("GHOST_LEDGER_SETUP_EOF")[-1]  # what runs directly in Lightsail's sh
    assert "exit" not in outer


def test_heredoc_terminators_cannot_collide_with_the_deploy_script():
    deploy = (ROOT / "infra/box/deploy.sh").read_text()
    for marker in ("GHOST_LEDGER_SETUP_EOF", "GHOST_LEDGER_DEPLOY_EOF"):
        assert marker not in deploy


def test_deploy_gives_cosign_a_home_under_systemd():
    # systemd runs deploy.sh with no HOME, so cosign couldn't find docker's registry login and
    # every signed image was refused as "not signed" (Sep 27). HOME must be set before cosign
    # or docker runs, and cosign's own error must reach the log.
    script = (ROOT / "infra/box/deploy.sh").read_text()
    home = script.index('export HOME="${HOME:-/root}"')
    assert home < script.index("| docker login ghcr.io") and home < script.index('"$COSIGN" verify')
    assert 'log "cosign: ' in script
