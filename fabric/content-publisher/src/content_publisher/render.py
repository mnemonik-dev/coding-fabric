"""Preview rendering — Decision 4 (byte-equality with publish path).

Three import lines, copied verbatim from tech-spec Decision 4. ``Platform`` is
pulled from ``mnemonik_blogger.config`` ONLY — never from
``mnemonik_blogger.content.voice`` (an older draft of upstream re-exported it
there; importing the alias is a regression risk).

``run_campaign_from_article`` internally calls ``render(Platform.TELEGRAM, post)``
on the same ``SourcePost`` produced by ``ingest_article(path)``, so this output
is structurally byte-identical to what gets published.

Imports are inside ``render_preview`` (not at module top) so that test modules
that only need to inspect ``render_preview``'s source code can be collected
even on a dev-venv where the upstream ``mnemonik_blogger`` snapshot is missing
``ingest_article`` (Decision 11's install-time assertion is what guarantees
the imports succeed in production).
"""

from __future__ import annotations

from pathlib import Path


def render_preview(article_path: Path) -> list[str]:
    """Return the preview segments for ``article.md`` as a list of strings."""
    from mnemonik_blogger.config import Platform
    from mnemonik_blogger.content.formatters import render
    from mnemonik_blogger.content.ingest import ingest_article

    src = ingest_article(article_path)
    rendered = render(Platform.TELEGRAM, src)
    return list(rendered.segments)
