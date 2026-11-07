"""Bundles, bitstreams and the format registry"""

import pytest

from dspace_api.exceptions import NotFoundError

from .helpers import unsupported_on, values

CONTENT = b"Integration test file content\n"


@pytest.fixture
def original(bundles, new_item):
    item = new_item()
    return item, bundles.get_or_create_bundle(item["uuid"])


@pytest.mark.feature("bundles.crud")
def test_bundle_create_find_delete(bundles, new_item):
    item = new_item()
    created = bundles.create_bundle(item["uuid"], "IT-EXTRA")
    assert bundles.find_bundle(item["uuid"], "IT-EXTRA")["uuid"] == created["uuid"]
    assert bundles.get_or_create_bundle(item["uuid"], "IT-EXTRA")["uuid"] == created["uuid"]
    assert bundles.delete_bundle(created["uuid"]) is True
    assert bundles.find_bundle(item["uuid"], "IT-EXTRA") is None


@pytest.mark.feature("bitstreams.upload")
def test_upload_and_download(bundles, original):
    _, bundle = original
    bitstream = bundles.upload_bitstream(bundle["uuid"], CONTENT, name="it.txt", mime_type="text/plain",
                                         description="integration test")    # verifies MD5/size
    assert bitstream["sizeBytes"] == len(CONTENT)
    assert bundles.download_bitstream(bitstream["uuid"]) == CONTENT
    assert [b["uuid"] for b in bundles.find_bitstreams_by_name(bundle["uuid"], "it.txt")] == [bitstream["uuid"]]


@pytest.mark.feature("bitstreams.metadata")
def test_bitstream_metadata_and_rename(bundles, original):
    _, bundle = original
    bitstream = bundles.upload_bitstream(bundle["uuid"], CONTENT, name="meta.txt")
    bundles.update_bitstream_metadata(bitstream["uuid"], "add", {"dc.description": "first"})
    bundles.update_bitstream_metadata(bitstream["uuid"], "replace", {"dc.description": "second"})
    bundles.rename_bitstream(bitstream["uuid"], "renamed.txt")
    fetched = bundles.get_bitstream(bitstream["uuid"])
    assert values(fetched, "dc.description") == ["second"]
    assert fetched["name"] == "renamed.txt"


@pytest.mark.feature("bitstreams.replace")
def test_replace_bitstream(bundles, original):
    _, bundle = original
    old = bundles.upload_bitstream(bundle["uuid"], b"v1", name="doc.txt")
    new = bundles.replace_bitstream(bundle["uuid"], b"version 2", name="doc.txt")
    remaining = bundles.find_bitstreams_by_name(bundle["uuid"], "doc.txt")
    assert [b["uuid"] for b in remaining] == [new["uuid"]]
    with pytest.raises(NotFoundError):
        bundles.get_bitstream(old["uuid"])


@pytest.mark.feature("bitstreams.primary")
def test_primary_bitstream(bundles, original):
    _, bundle = original
    first = bundles.upload_bitstream(bundle["uuid"], b"1", name="one.txt")
    second = bundles.upload_bitstream(bundle["uuid"], b"2", name="two.txt")
    with unsupported_on(404, 405, what="primaryBitstream"):
        bundles.set_primary_bitstream(bundle["uuid"], first["uuid"])            # POST
        assert bundles.get_primary_bitstream(bundle["uuid"])["uuid"] == first["uuid"]
        bundles.set_primary_bitstream(bundle["uuid"], second["uuid"])           # PUT
        assert bundles.get_primary_bitstream(bundle["uuid"])["uuid"] == second["uuid"]
        assert bundles.clear_primary_bitstream(bundle["uuid"]) is True
        assert bundles.get_primary_bitstream(bundle["uuid"]) is None


@pytest.mark.feature("bitstreams.move")
def test_move_bitstream(bundles, original):
    item, bundle = original
    extra = bundles.create_bundle(item["uuid"], "IT-MOVE")
    bitstream = bundles.upload_bitstream(bundle["uuid"], b"x", name="move.txt")
    bundles.move_bitstream_to_bundle(bitstream["uuid"], extra["uuid"])
    assert bundles.get_bitstream_bundle(bitstream["uuid"])["uuid"] == extra["uuid"]


@pytest.mark.feature("bitstreams.formats")
def test_format_registry(bundles, original):
    _, bundle = original
    plain = bundles.find_bitstream_format_by_mimetype("text/plain")
    assert plain, "text/plain missing from the format registry"
    bitstream = bundles.upload_bitstream(bundle["uuid"], b"x", name="noext", mime_type="text/plain",
                                         enforce_mime_format=True)
    assert bundles.get_bitstream_format(bitstream["uuid"])["id"] == plain["id"]


@pytest.mark.feature("bitstreams.large_upload")
def test_large_streamed_upload(bundles, original, tmp_path):
    """
    Streams a DSPACE_IT_LARGE_UPLOAD_MB file (default 64 MB; set e.g. 1024 for
    a real large-file test - the test stacks allow up to 4GB) and downloads it
    again. 0 skips the test.
    """
    import hashlib
    import os

    size_mb = int(os.getenv("DSPACE_IT_LARGE_UPLOAD_MB", "64"))
    if size_mb <= 0:
        pytest.skip("DSPACE_IT_LARGE_UPLOAD_MB=0")
    block = os.urandom(1024 * 1024)
    source = tmp_path / "large.bin"
    digest = hashlib.md5()
    with open(source, "wb") as fh:
        for i in range(size_mb):
            chunk = block[i % 256:] + block[:i % 256]   # vary the data per MB
            fh.write(chunk)
            digest.update(chunk)

    _, bundle = original
    seen = []
    bitstream = bundles.upload_bitstream(bundle["uuid"], str(source), mime_type="application/octet-stream",
                                         progress=lambda done, total: seen.append(done))
    assert bitstream["sizeBytes"] == size_mb * 1024 * 1024
    assert bitstream["checkSum"]["value"].lower() == digest.hexdigest()
    assert seen and seen[-1] == size_mb * 1024 * 1024

    target = tmp_path / "back.bin"
    bundles.download_bitstream(bitstream["uuid"], str(target))
    back = hashlib.md5()
    with open(target, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            back.update(chunk)
    assert back.hexdigest() == digest.hexdigest()
