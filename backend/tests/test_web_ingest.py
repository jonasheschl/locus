import httpx
import pytest

from app.ingest import HTML_EXTRACTOR_VERSION, IngestIndexer, extract_ingest_text
from app.database import Database
from app.web_ingest import WebIngestError, download_web_source, extract_http_urls


@pytest.mark.asyncio
async def test_download_web_source_preserves_html_and_title() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.com/article"
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=b"<html><head><title>A &amp; B</title></head><body>Durable source text.</body></html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        source = await download_web_source(
            "https://example.com/article", client=client, validate_network=False
        )

    assert source.extension == ".html"
    assert source.media_type == "text/html"
    assert source.title == "A & B"
    assert source.content.endswith(b"</html>")


@pytest.mark.asyncio
async def test_download_web_source_rejects_private_networks() -> None:
    with pytest.raises(WebIngestError, match="private-network"):
        await download_web_source("http://127.0.0.1/private")


def test_extract_http_urls_deduplicates_and_trims_punctuation() -> None:
    assert extract_http_urls(
        "Read https://example.com/a, then https://example.com/b). Again: https://example.com/a"
    ) == ["https://example.com/a", "https://example.com/b"]


def test_html_is_extracted_as_structured_docling_markdown(tmp_path) -> None:
    source = tmp_path / "ingest" / "structured.html"
    source.parent.mkdir()
    source.write_text(
        """
        <html><head><title>Structured source</title></head><body>
          <main><h1>Agent patterns</h1>
          <p>Read the <a href="https://example.com/guide">implementation guide</a>.</p>
          <ul><li>Preserve sources</li><li>Record provenance</li></ul>
          <table><tr><th>Pattern</th><th>Value</th></tr><tr><td>Links</td><td>Context</td></tr></table>
          </main><script>do_not_index()</script>
        </body></html>
        """,
        encoding="utf-8",
    )

    content, error = extract_ingest_text(source)

    assert error is None
    assert "# Agent patterns" in content
    assert "[implementation guide](https://example.com/guide)" in content
    assert "- Preserve sources" in content
    assert "| Pattern" in content
    assert "do_not_index" not in content

    database = Database(tmp_path / "wiki.sqlite3")
    database.initialize()
    assert IngestIndexer(tmp_path, database).scan()["indexed"] == 1
    assert database.fetch_one(
        "SELECT extractor_version FROM ingest_items WHERE path = ?",
        ("ingest/structured.html",),
    )["extractor_version"] == HTML_EXTRACTOR_VERSION
