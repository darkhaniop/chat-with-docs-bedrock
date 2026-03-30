"""moto mocks the boto3/botocore transport, not arbitrary HTTP clients — so presigned-URL tests
here assert URL *shape* via the same client, and exercise object lifecycle via the client
directly (`s3_client.put_object`) rather than actually issuing an HTTP PUT to the presigned
URL, which moto would not intercept."""

from __future__ import annotations

from common.storage import DocumentsStore, render_key, source_key


def test_source_key_and_render_key_match_the_documented_layout() -> None:
    assert source_key("proj1", "doc1", "pdf") == "raw/proj1/doc1/source.pdf"
    assert render_key("proj1", "doc1", 7) == "pages/proj1/doc1/0007.png"


def test_presign_put_produces_a_url_scoped_to_the_declared_key(store: DocumentsStore) -> None:
    url = store.presign_put("raw/p/d/source.pdf", content_type="application/pdf")
    assert "raw/p/d/source.pdf" in url
    assert url.startswith("https://")


def test_presign_get_produces_a_url_scoped_to_the_key(store: DocumentsStore) -> None:
    url = store.presign_get("raw/p/d/source.pdf")
    assert "raw/p/d/source.pdf" in url
    assert url.startswith("https://")


def test_object_exists_reflects_the_bucket_contents(
    store: DocumentsStore, s3_client: object
) -> None:
    key = "raw/p/d/source.pdf"
    assert store.object_exists(key) is False

    s3_client.put_object(Bucket="cwd-documents-test-111122223333", Key=key, Body=b"%PDF-1.4")  # type: ignore[attr-defined]

    assert store.object_exists(key) is True


def test_delete_prefix_removes_every_object_under_it(
    store: DocumentsStore, s3_client: object
) -> None:
    bucket = "cwd-documents-test-111122223333"
    key_a = "raw/p/d/source.pdf"
    key_b = "raw/p/d/extra.pdf"
    other_document_key = "raw/p/other-doc/source.pdf"
    for key in (key_a, key_b, other_document_key):
        s3_client.put_object(Bucket=bucket, Key=key, Body=b"x")  # type: ignore[attr-defined]

    store.delete_prefix("raw/p/d/")

    assert store.object_exists(key_a) is False
    assert store.object_exists(key_b) is False
    assert store.object_exists(other_document_key) is True
