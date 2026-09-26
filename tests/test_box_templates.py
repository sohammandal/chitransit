"""Guards for infra/box: Terraform renders cloud-init.yaml.tftpl with templatefile(), where
any `${...}` is interpolated, even inside a YAML comment. An unescaped `${deploy_sh}` in a
comment once pasted the whole deploy script, unindented, into the file and broke first boot.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "infra/box/cloud-init.yaml.tftpl"
ALLOWED = {"${indent(6, deploy_sh)}"}


def test_only_the_intended_interpolation_is_in_the_cloud_init_template():
    text = TEMPLATE.read_text()
    # `$${` is Terraform's escape for a literal `${`; `%{` would start a directive
    found = re.findall(r"(?<!\$)\$\{[^}]*\}", text)
    assert set(found) <= ALLOWED, f"unexpected template interpolation: {set(found) - ALLOWED}"
    assert found.count("${indent(6, deploy_sh)}") == 1
    assert "%{" not in text
