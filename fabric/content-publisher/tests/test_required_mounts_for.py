"""Unit tests for the ``content-publisher.service`` systemd unit template.

Decision 10 / AC15: ``RequiresMountsFor=`` must reach the rendered unit file
with a CONCRETE volume id resolved by Ansible, never the literal ``*`` glob
and never unrendered Jinja braces. Empty fact must fail the render.

We render the Jinja template directly with ``jinja2.Template`` so the test
runs at unit-level — no Ansible engine required.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

# tests/ -> content-publisher/ -> fabric/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = (
    REPO_ROOT
    / "infrastructure/ansible/roles/content-publisher/templates"
    / "content-publisher.service.j2"
)


def _render(**vars_: object) -> str:
    template_text = TEMPLATE_PATH.read_text()
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    return env.from_string(template_text).render(**vars_)


def _baseline_vars(hetzner_volume_id: str = "105783873") -> dict[str, object]:
    """Minimum vars to render the unit. Real values come from defaults/main.yml + facts."""
    volume_mount = f"/mnt/HC_Volume_{hetzner_volume_id}"
    unit_name = volume_mount.lstrip("/").replace("/", "-") + ".mount"
    return {
        "content_publisher_volume_mount": volume_mount,
        "content_publisher_volume_mount_unit": unit_name,
        "base_operator_user": "op",
        "blogger_install_dir": "/opt/blogger",
        "content_publisher_work_dir": "/var/lib/content-publisher/work",
    }


def test_unit_template_renders_concrete_volume_id() -> None:
    """AC15: concrete volume id reaches RequiresMountsFor= verbatim."""
    rendered = _render(**_baseline_vars(hetzner_volume_id="105783873"))
    assert (
        "RequiresMountsFor=/mnt/HC_Volume_105783873/content-publisher" in rendered
    )


def test_unit_template_no_literal_wildcard() -> None:
    """AC15: a literal '*' must never reach the unit (silent root-disk fallback)."""
    rendered = _render(**_baseline_vars(hetzner_volume_id="105783873"))
    # No '*' anywhere in the RequiresMountsFor line; no unrendered Jinja braces.
    for line in rendered.splitlines():
        if line.startswith("RequiresMountsFor="):
            assert "*" not in line
            assert "{{" not in line
            assert "}}" not in line
            break
    else:
        raise AssertionError("RequiresMountsFor= line missing from rendered unit")
    assert "HC_Volume_*" not in rendered


def test_empty_volume_mount_fact_fails_render() -> None:
    """Empty fact -> StrictUndefined raises during render (fail-loud)."""
    # Drop the volume mount var entirely -> StrictUndefined kicks in.
    vars_ = _baseline_vars()
    del vars_["content_publisher_volume_mount"]
    with pytest.raises(jinja2.UndefinedError):
        _render(**vars_)
