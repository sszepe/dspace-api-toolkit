"""
Tests for DSpace bundles module (bundles, bitstreams, bitstream formats)

Uses the `http` (responses) + `http_auth` (real requests.Session) fixtures, so
the real request pipeline runs: multipart bodies, headers and query strings
are what DSpace would actually receive.
"""

import hashlib
import io
import json
from unittest.mock import Mock

import pytest
import responses

from dspace_api.bundles import (
    BundleManager, ORIGINAL, _format_metadata, _format_metadata_field,
)
from dspace_api.exceptions import BadRequestError, DSpaceError, NotFoundError, ServerError

pytestmark = pytest.mark.bundles

CONTENT = b"hello bitstream"
MD5 = hashlib.md5(CONTENT).hexdigest()


def bitstream(uuid="bs-1", name="file.txt", content=CONTENT, md5=None, algorithm="MD5"):
    return {
        "uuid": uuid, "name": name, "type": "bitstream",
        "sizeBytes": len(content),
        "checkSum": {"checkSumAlgorithm": algorithm, "value": md5 or hashlib.md5(content).hexdigest()},
    }


@pytest.fixture
def mgr(http_auth):
    return BundleManager(http_auth)


def last(http):
    return http.calls[-1].request


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

class TestMetadataHelpers:
    def test_format_metadata_field_variants(self):
        assert _format_metadata_field("x") == [{"value": "x"}]
        assert _format_metadata_field({"value": "x", "language": "de"}) == [{"value": "x", "language": "de"}]
        assert _format_metadata_field(["a", {"value": "b"}, 3]) == [{"value": "a"}, {"value": "b"}, {"value": "3"}]
        assert _format_metadata_field(42) == [{"value": "42"}]

    def test_format_metadata(self):
        assert _format_metadata(None) == {}
        assert _format_metadata({"dc.title": "T"}) == {"dc.title": [{"value": "T"}]}


# ---------------------------------------------------------------------------
# Construction and request plumbing
# ---------------------------------------------------------------------------

class TestBundleManagerBasics:
    def test_init(self, mock_auth):
        manager = BundleManager(mock_auth)
        assert manager.auth == mock_auth
        assert manager.session == mock_auth.session
        assert manager.config == mock_auth.config

    def test_session_follows_auth_after_session_replacement(self, http_auth):
        manager = BundleManager(http_auth)
        new_session = Mock()
        http_auth.session = new_session  # what DSpaceAuth.logout() does
        assert manager.session is new_session

    def test_headers_override_content_type_and_accept(self, mgr):
        h = mgr._headers("text/uri-list", accept="*/*")
        assert h["Content-Type"] == "text/uri-list" and h["Accept"] == "*/*"
        assert "Content-Type" not in mgr._headers(content_type=None)

    def test_request_refreshes_csrf_token(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/core/bundles/b1"), json={"uuid": "b1"},
                 headers={"DSPACE-XSRF-TOKEN": "fresh"})
        mgr.get_bundle("b1")
        assert mgr.session.headers["X-XSRF-Token"] == "fresh"

    def test_request_without_update_token_on_auth(self, dspace_config, http, api_url):
        """Auth objects without _update_token (e.g. test doubles) are tolerated"""
        import requests
        fake_auth = Mock(spec=["session", "config"])
        fake_auth.session = requests.Session()
        fake_auth.config = dspace_config
        http.add(responses.GET, api_url("/core/bundles/b1"), json={"uuid": "b1"})
        assert BundleManager(fake_auth).get_bundle("b1") == {"uuid": "b1"}

    @pytest.mark.parametrize("status,exc", [(400, BadRequestError), (404, NotFoundError), (500, ServerError)])
    def test_request_error_mapping(self, mgr, http, api_url, status, exc):
        http.add(responses.GET, api_url("/core/bundles/b1"), status=status, body="err")
        with pytest.raises(exc):
            mgr.get_bundle("b1")


class TestPagination:
    URL = "/core/items/i1/bundles"

    def test_follows_all_pages(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(self.URL), json=hal_page("bundles", [{"uuid": "a"}], total_pages=2))
        http.add(responses.GET, api_url(self.URL), json=hal_page("bundles", [{"uuid": "b"}], total_pages=2))
        assert [b["uuid"] for b in mgr.get_item_bundles("i1")] == ["a", "b"]
        assert [c.request.params["page"] for c in http.calls] == ["0", "1"]

    def test_stops_on_empty_page(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(self.URL), json=hal_page("bundles", [], total_pages=5))
        assert mgr.get_item_bundles("i1") == []
        assert len(http.calls) == 1

    def test_without_page_info_stops_on_short_page(self, mgr, http, api_url):
        http.add(responses.GET, api_url(self.URL), json={"_embedded": {"bundles": [{"uuid": "a"}]}})
        assert len(mgr.get_item_bundles("i1")) == 1
        assert len(http.calls) == 1

    def test_without_page_info_continues_on_full_page(self, mgr, http, api_url):
        mgr.DEFAULT_PAGE_SIZE = 1
        http.add(responses.GET, api_url(self.URL), json={"_embedded": {"bundles": [{"uuid": "a"}]}})
        http.add(responses.GET, api_url(self.URL), json={"_embedded": {"bundles": []}})
        assert len(mgr.get_item_bundles("i1")) == 1
        assert len(http.calls) == 2


class TestReadSource:
    def test_bytes_and_bytearray(self):
        assert BundleManager._read_source(b"x", "a.txt") == ("a.txt", b"x")
        assert BundleManager._read_source(bytearray(b"x"), "a.txt") == ("a.txt", b"x")

    def test_path_uses_basename(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"pdf")
        assert BundleManager._read_source(str(f), None) == ("doc.pdf", b"pdf")
        assert BundleManager._read_source(f, "other.pdf") == ("other.pdf", b"pdf")

    def test_binary_file_object(self, tmp_path):
        f = tmp_path / "x.bin"
        f.write_bytes(b"123")
        with open(f, "rb") as fh:
            assert BundleManager._read_source(fh, None) == ("x.bin", b"123")

    def test_text_mode_file_rejected(self):
        with pytest.raises(TypeError, match="binary mode"):
            BundleManager._read_source(io.StringIO("text"), "a.txt")

    def test_unsupported_type_rejected(self):
        with pytest.raises(TypeError, match="Unsupported"):
            BundleManager._read_source(123, "a.txt")

    def test_name_required_for_anonymous_sources(self):
        with pytest.raises(ValueError, match="name is required"):
            BundleManager._read_source(b"x", None)
        with pytest.raises(ValueError):
            BundleManager._read_source(io.BytesIO(b"x"), None)


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------

class TestBundles:
    def test_find_bundle(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/items/i1/bundles"),
                 json=hal_page("bundles", [{"uuid": "t", "name": "THUMBNAIL"}, {"uuid": "o", "name": ORIGINAL}]))
        assert mgr.find_bundle("i1")["uuid"] == "o"
        assert mgr.find_bundle("i1", "LICENSE") is None

    def test_get_bundle(self, mgr, http, api_url, sample_bundle_data):
        http.add(responses.GET, api_url("/core/bundles/b1"), json=sample_bundle_data)
        assert mgr.get_bundle("b1") == sample_bundle_data

    def test_create_bundle(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/core/items/i1/bundles"), status=201, json={"uuid": "b9", "name": "EXTRA"})
        out = mgr.create_bundle("i1", "EXTRA", metadata={"dc.description": "x"})
        assert out["uuid"] == "b9"
        req = last(http)
        assert req.headers["Content-Type"] == "application/json"
        assert json.loads(req.body) == {"name": "EXTRA", "metadata": {"dc.description": [{"value": "x"}]}}

    def test_create_bundle_error(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/core/items/i1/bundles"), status=403, body="no")
        with pytest.raises(DSpaceError):
            mgr.create_bundle("i1")

    def test_get_or_create_returns_existing(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/items/i1/bundles"),
                 json=hal_page("bundles", [{"uuid": "o", "name": ORIGINAL}]))
        assert mgr.get_or_create_bundle("i1")["uuid"] == "o"
        assert all(c.request.method == "GET" for c in http.calls)

    def test_get_or_create_creates_missing(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/items/i1/bundles"), json=hal_page("bundles", []))
        http.add(responses.POST, api_url("/core/items/i1/bundles"), status=201, json={"uuid": "new"})
        assert mgr.get_or_create_bundle("i1")["uuid"] == "new"

    def test_patch_bundle_200(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url("/core/bundles/b1"), json={"uuid": "b1", "patched": True})
        assert mgr.patch_bundle("b1", [{"op": "x"}])["patched"] is True
        assert last(http).headers["Content-Type"] == "application/json-patch+json"

    def test_patch_bundle_204_refetches(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url("/core/bundles/b1"), status=204)
        http.add(responses.GET, api_url("/core/bundles/b1"), json={"uuid": "b1"})
        assert mgr.patch_bundle("b1", [])["uuid"] == "b1"

    def test_move_bitstream_in_bundle(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url("/core/bundles/b1"), json={"uuid": "b1"})
        mgr.move_bitstream_in_bundle("b1", 2, 0)
        assert json.loads(last(http).body) == [
            {"op": "move", "from": "/_links/bitstreams/2/href", "path": "/_links/bitstreams/0/href"}]

    def test_delete_bundle(self, mgr, http, api_url):
        http.add(responses.DELETE, api_url("/core/bundles/b1"), status=204)
        assert mgr.delete_bundle("b1") is True

    def test_delete_bundle_error(self, mgr, http, api_url):
        http.add(responses.DELETE, api_url("/core/bundles/b1"), status=404)
        with pytest.raises(NotFoundError):
            mgr.delete_bundle("b1")


class TestPrimaryBitstream:
    URL = "/core/bundles/b1/primaryBitstream"

    def test_get_primary_204(self, mgr, http, api_url):
        http.add(responses.GET, api_url(self.URL), status=204)
        assert mgr.get_primary_bitstream("b1") is None

    def test_get_primary_200_empty_body(self, mgr, http, api_url):
        http.add(responses.GET, api_url(self.URL), status=200, body=b"")
        assert mgr.get_primary_bitstream("b1") is None

    def test_get_primary_200(self, mgr, http, api_url):
        http.add(responses.GET, api_url(self.URL), json={"uuid": "bs"})
        assert mgr.get_primary_bitstream("b1") == {"uuid": "bs"}

    def test_set_primary_posts_when_none(self, mgr, http, api_url):
        http.add(responses.GET, api_url(self.URL), status=204)
        http.add(responses.POST, api_url(self.URL), status=201, json={"uuid": "b1"})
        mgr.set_primary_bitstream("b1", "bs")
        req = last(http)
        assert req.method == "POST"
        assert req.headers["Content-Type"] == "text/uri-list"
        assert req.body == api_url("/core/bitstreams/bs")

    def test_set_primary_puts_when_existing_and_refetches_on_empty_body(self, mgr, http, api_url):
        http.add(responses.GET, api_url(self.URL), json={"uuid": "old"})
        http.add(responses.PUT, api_url(self.URL), status=200, body=b"")
        http.add(responses.GET, api_url("/core/bundles/b1"), json={"uuid": "b1"})
        assert mgr.set_primary_bitstream("b1", "bs") == {"uuid": "b1"}
        assert [c.request.method for c in http.calls] == ["GET", "PUT", "GET"]

    def test_clear_primary_none_set(self, mgr, http, api_url):
        http.add(responses.GET, api_url(self.URL), status=204)
        assert mgr.clear_primary_bitstream("b1") is False

    def test_clear_primary(self, mgr, http, api_url):
        http.add(responses.GET, api_url(self.URL), json={"uuid": "bs"})
        http.add(responses.DELETE, api_url(self.URL), status=204)
        assert mgr.clear_primary_bitstream("b1") is True


# ---------------------------------------------------------------------------
# Bitstreams in a bundle: listing, upload, replace
# ---------------------------------------------------------------------------

class TestBundleBitstreams:
    def test_find_bitstreams_by_name_returns_duplicates(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/bundles/b1/bitstreams"), json=hal_page("bitstreams", [
            {"uuid": "1", "name": "a.pdf"}, {"uuid": "2", "name": "b.pdf"}, {"uuid": "3", "name": "a.pdf"}]))
        assert [b["uuid"] for b in mgr.find_bitstreams_by_name("b1", "a.pdf")] == ["1", "3"]


class TestUpload:
    URL = "/core/bundles/b1/bitstreams"

    def test_upload_bytes_multipart_request(self, mgr, http, api_url):
        http.add(responses.POST, api_url(self.URL), status=201, json=bitstream(name="a.txt"))
        out = mgr.upload_bitstream("b1", CONTENT, name="a.txt", description="desc",
                                   metadata={"dc.rights": "CC BY"})
        assert out["uuid"] == "bs-1"
        req = last(http)
        assert req.headers["Content-Type"].startswith("multipart/form-data; boundary=")
        assert int(req.headers["Content-Length"]) == len(req.body)   # no chunked encoding
        assert b'name="file"; filename="a.txt"' in req.body and CONTENT in req.body
        props = json.loads(req.body.split(b'name="properties"')[1].split(b"\r\n\r\n", 1)[1].split(b"\r\n--")[0])
        assert props == {"name": "a.txt", "metadata": {
            "dc.rights": [{"value": "CC BY"}], "dc.description": [{"value": "desc"}]}}

    def test_description_does_not_override_explicit_metadata(self, mgr, http, api_url):
        http.add(responses.POST, api_url(self.URL), status=201, json=bitstream())
        mgr.upload_bitstream("b1", CONTENT, name="a.txt", description="ignored",
                             metadata={"dc.description": "explicit"})
        assert b"explicit" in last(http).body and b"ignored" not in last(http).body

    def test_mime_type_guessed_or_octet_stream(self, mgr, http, api_url):
        http.add(responses.POST, api_url(self.URL), status=201, json=bitstream())
        mgr.upload_bitstream("b1", CONTENT, name="a.pdf")
        assert b"Content-Type: application/pdf" in last(http).body
        mgr.upload_bitstream("b1", CONTENT, name="noext")
        assert b"Content-Type: application/octet-stream" in last(http).body

    def test_upload_from_path(self, mgr, http, api_url, temp_test_file):
        data = open(temp_test_file, "rb").read()
        http.add(responses.POST, api_url(self.URL), status=201, json=bitstream(content=data))
        mgr.upload_bitstream("b1", temp_test_file)
        assert b'filename="test_upload.txt"' in last(http).body

    def test_upload_error(self, mgr, http, api_url):
        http.add(responses.POST, api_url(self.URL), status=400, body="no file")
        with pytest.raises(BadRequestError):
            mgr.upload_bitstream("b1", CONTENT, name="a.txt")

    def test_verify_skipped_when_disabled(self, mgr, http, api_url):
        http.add(responses.POST, api_url(self.URL), status=201, json=bitstream(md5="wrong"))
        assert mgr.upload_bitstream("b1", CONTENT, name="a.txt", verify_checksum=False)["uuid"] == "bs-1"

    def test_checksum_mismatch_deletes_and_raises(self, mgr, http, api_url):
        http.add(responses.POST, api_url(self.URL), status=201, json=bitstream(md5="wrong"))
        http.add(responses.DELETE, api_url("/core/bitstreams/bs-1"), status=204)
        with pytest.raises(DSpaceError, match="MD5 wrong"):
            mgr.upload_bitstream("b1", CONTENT, name="a.txt")
        assert last(http).method == "DELETE"

    def test_size_mismatch_and_failed_cleanup_still_raises(self, mgr, http, api_url):
        bad = bitstream()
        bad["sizeBytes"] = 1
        http.add(responses.POST, api_url(self.URL), status=201, json=bad)
        http.add(responses.DELETE, api_url("/core/bitstreams/bs-1"), status=500)
        with pytest.raises(DSpaceError, match="size 1"):
            mgr.upload_bitstream("b1", CONTENT, name="a.txt")

    def test_mismatch_without_uuid_skips_delete(self, mgr):
        with pytest.raises(DSpaceError):
            mgr._verify_upload({"sizeBytes": 1}, CONTENT)

    def test_verify_accepts_missing_or_non_md5_checksum(self, mgr):
        mgr._verify_upload({"uuid": "x"}, CONTENT)                                    # nothing reported
        mgr._verify_upload(bitstream(md5="sha-value", algorithm="SHA-256"), CONTENT)  # MD5 not comparable
        mgr._verify_upload(bitstream(md5=MD5.upper()), CONTENT)                       # case-insensitive

    def test_enforce_mime_format(self, mgr, http, api_url, hal_page):
        http.add(responses.POST, api_url(self.URL), status=201, json=bitstream())
        http.add(responses.GET, api_url("/core/bitstreamformats"),
                 json=hal_page("bitstreamformats", [{"id": 6, "mimetype": "text/plain"}]))
        http.add(responses.GET, api_url("/core/bitstreams/bs-1/format"), json={"id": 1})
        http.add(responses.PUT, api_url("/core/bitstreams/bs-1/format"), json={"id": 6})
        mgr.upload_bitstream("b1", CONTENT, name="a.txt", mime_type="text/plain", enforce_mime_format=True)
        assert last(http).method == "PUT"

    def test_enforce_mime_format_skipped_for_octet_stream(self, mgr, http, api_url):
        http.add(responses.POST, api_url(self.URL), status=201, json=bitstream())
        mgr.upload_bitstream("b1", CONTENT, name="blob", enforce_mime_format=True)
        assert len(http.calls) == 1

    def test_custom_timeout_passed(self, http_auth):
        manager = BundleManager(http_auth)
        http_auth.session = Mock()
        http_auth.session.request.return_value = Mock(status_code=201, headers={}, json=Mock(return_value=bitstream()))
        manager.upload_bitstream("b1", CONTENT, name="a.txt", timeout=7)
        assert http_auth.session.request.call_args.kwargs["timeout"] == 7
        manager.upload_bitstream("b1", CONTENT, name="a.txt")
        # default: (connect, read), read never below UPLOAD_TIMEOUT
        assert http_auth.session.request.call_args.kwargs["timeout"] == (30.0, float(BundleManager.UPLOAD_TIMEOUT))


class TestReplace:
    def _setup(self, http, api_url, hal_page, existing, primary):
        http.add(responses.GET, api_url("/core/bundles/b1/bitstreams"), json=hal_page("bitstreams", existing))
        if primary is None:
            http.add(responses.GET, api_url("/core/bundles/b1/primaryBitstream"), status=204)
        else:
            http.add(responses.GET, api_url("/core/bundles/b1/primaryBitstream"), json={"uuid": primary})
        http.add(responses.POST, api_url("/core/bundles/b1/bitstreams"), status=201,
                 json=bitstream(uuid="new", name="a.txt"))
        http.add(responses.PATCH, api_url("/core/bitstreams"), status=204)

    def test_replace_without_existing_just_uploads(self, mgr, http, api_url, hal_page):
        self._setup(http, api_url, hal_page, [], None)
        assert mgr.replace_bitstream("b1", CONTENT, name="a.txt")["uuid"] == "new"
        methods = [(c.request.method, c.request.url.split("/api")[1]) for c in http.calls]
        assert ("PATCH", "/core/bitstreams") not in methods
        assert not any("primaryBitstream" in u for _, u in methods)

    def test_replace_deletes_old_copies_after_upload(self, mgr, http, api_url, hal_page):
        self._setup(http, api_url, hal_page,
                    [{"uuid": "o1", "name": "a.txt"}, {"uuid": "o2", "name": "a.txt"}, {"uuid": "x", "name": "b"}],
                    primary="x")
        mgr.replace_bitstream("b1", CONTENT, name="a.txt")
        methods = [c.request.method for c in http.calls]
        assert methods.index("POST") < methods.index("PATCH")   # upload first, delete after
        assert json.loads(last(http).body) == [{"op": "remove", "path": "/bitstreams/o1"},
                                               {"op": "remove", "path": "/bitstreams/o2"}]

    def test_replace_moves_primary_flag(self, mgr, http, api_url, hal_page):
        self._setup(http, api_url, hal_page, [{"uuid": "o1", "name": "a.txt"}], primary="o1")
        http.add(responses.PUT, api_url("/core/bundles/b1/primaryBitstream"), json={"uuid": "b1"})
        mgr.replace_bitstream("b1", CONTENT, name="a.txt")
        put = next(c.request for c in http.calls if c.request.method == "PUT")
        assert put.body == api_url("/core/bitstreams/new")


# ---------------------------------------------------------------------------
# Single bitstreams
# ---------------------------------------------------------------------------

class TestBitstreams:
    def test_get_bitstream(self, mgr, http, api_url, sample_bitstream_data):
        http.add(responses.GET, api_url("/core/bitstreams/bs"), json=sample_bitstream_data)
        assert mgr.get_bitstream("bs") == sample_bitstream_data

    def test_patch_bitstream_200_and_204(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url("/core/bitstreams/bs"), json={"uuid": "bs", "v": 1})
        assert mgr.patch_bitstream("bs", [])["v"] == 1
        http.replace(responses.PATCH, api_url("/core/bitstreams/bs"), status=204)
        http.add(responses.GET, api_url("/core/bitstreams/bs"), json={"uuid": "bs", "v": 2})
        assert mgr.patch_bitstream("bs", [])["v"] == 2

    def test_update_metadata_replace_and_remove(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url("/core/bitstreams/bs"), json={"uuid": "bs"})
        mgr.update_bitstream_metadata("bs", "replace", {"dc.description": "d"})
        assert json.loads(last(http).body) == [
            {"op": "replace", "path": "/metadata/dc.description", "value": [{"value": "d"}]}]
        mgr.update_bitstream_metadata("bs", "remove", {"dc.description": None})
        assert json.loads(last(http).body) == [{"op": "remove", "path": "/metadata/dc.description"}]

    def test_update_metadata_invalid_operation(self, mgr):
        with pytest.raises(ValueError):
            mgr.update_bitstream_metadata("bs", "move", {})

    def test_rename(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url("/core/bitstreams/bs"), json={"uuid": "bs"})
        mgr.rename_bitstream("bs", "new.pdf")
        assert json.loads(last(http).body)[0]["path"] == "/metadata/dc.title"

    def test_delete_bitstream(self, mgr, http, api_url):
        http.add(responses.DELETE, api_url("/core/bitstreams/bs"), status=204)
        assert mgr.delete_bitstream("bs") is True

    def test_delete_bitstreams_bulk_and_empty(self, mgr, http, api_url):
        assert mgr.delete_bitstreams([]) is True
        assert len(http.calls) == 0
        http.add(responses.PATCH, api_url("/core/bitstreams"), status=204)
        assert mgr.delete_bitstreams(["a"]) is True
        assert last(http).headers["Content-Type"] == "application/json"

    def test_download_bytes(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/core/bitstreams/bs/content"), body=CONTENT)
        assert mgr.download_bitstream("bs") == CONTENT
        assert last(http).headers["Accept"] == "*/*"

    def test_download_to_file(self, mgr, http, api_url, tmp_path):
        http.add(responses.GET, api_url("/core/bitstreams/bs/content"), body=CONTENT)
        dest = tmp_path / "out.bin"
        assert mgr.download_bitstream("bs", str(dest), chunk_size=4) == str(dest)
        assert dest.read_bytes() == CONTENT

    def test_get_bitstream_bundle(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/core/bitstreams/bs/bundle"), json={"uuid": "b1"})
        assert mgr.get_bitstream_bundle("bs") == {"uuid": "b1"}

    def test_move_to_bundle(self, mgr, http, api_url):
        http.add(responses.PUT, api_url("/core/bitstreams/bs/bundle"), json={"uuid": "b2"})
        assert mgr.move_bitstream_to_bundle("bs", "b2") == {"uuid": "b2"}
        assert last(http).body == api_url("/core/bundles/b2")

    def test_move_to_bundle_empty_response_refetches(self, mgr, http, api_url):
        http.add(responses.PUT, api_url("/core/bitstreams/bs/bundle"), status=204)
        http.add(responses.GET, api_url("/core/bitstreams/bs/bundle"), json={"uuid": "b2"})
        assert mgr.move_bitstream_to_bundle("bs", "b2") == {"uuid": "b2"}


# ---------------------------------------------------------------------------
# Bitstream formats
# ---------------------------------------------------------------------------

class TestFormats:
    FORMATS = [{"id": 5, "mimetype": "text/xml"}, {"id": 6, "mimetype": "Text/Plain"}, {"id": 7}]

    @pytest.fixture
    def registry(self, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/bitstreamformats"), json=hal_page("bitstreamformats", self.FORMATS))

    def test_get_bitstream_format(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/core/bitstreams/bs/format"), json={"id": 6})
        assert mgr.get_bitstream_format("bs") == {"id": 6}

    def test_set_bitstream_format(self, mgr, http, api_url):
        http.add(responses.PUT, api_url("/core/bitstreams/bs/format"), json={"uuid": "bs"})
        mgr.set_bitstream_format("bs", 6)
        assert last(http).body == api_url("/core/bitstreamformats/6")

    def test_set_bitstream_format_empty_response_refetches(self, mgr, http, api_url):
        http.add(responses.PUT, api_url("/core/bitstreams/bs/format"), status=204)
        http.add(responses.GET, api_url("/core/bitstreams/bs"), json={"uuid": "bs"})
        assert mgr.set_bitstream_format("bs", 6) == {"uuid": "bs"}

    def test_registry_is_cached_until_refresh(self, mgr, http, registry):
        mgr.get_bitstream_formats()
        mgr.get_bitstream_formats()
        assert len(http.calls) == 1
        mgr.get_bitstream_formats(refresh=True)
        assert len(http.calls) == 2

    def test_find_by_mimetype_case_insensitive(self, mgr, registry):
        assert mgr.find_bitstream_format_by_mimetype("text/plain")["id"] == 6
        assert mgr.find_bitstream_format_by_mimetype("image/png") is None

    def test_set_by_mimetype_unknown(self, mgr, registry):
        assert mgr.set_bitstream_format_by_mimetype("bs", "image/png") is None

    def test_set_by_mimetype_already_correct(self, mgr, http, api_url, registry):
        http.add(responses.GET, api_url("/core/bitstreams/bs/format"), json={"id": 6})
        assert mgr.set_bitstream_format_by_mimetype("bs", "text/plain") is None

    def test_set_by_mimetype_forced(self, mgr, http, api_url, registry):
        http.add(responses.PUT, api_url("/core/bitstreams/bs/format"), json={"uuid": "bs"})
        assert mgr.set_bitstream_format_by_mimetype("bs", "text/plain", only_if_different=False) == {"uuid": "bs"}

    def test_get_format_by_id(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/core/bitstreamformats/6"), json={"id": 6})
        assert mgr.get_bitstream_format_by_id(6) == {"id": 6}

    def test_create_update_delete_invalidate_cache(self, mgr, http, api_url):
        mgr._format_cache = [{"id": 1}]
        http.add(responses.POST, api_url("/core/bitstreamformats"), status=201, json={"id": 9})
        assert mgr.create_bitstream_format("YAML", "application/yaml", extensions=["yml"])["id"] == 9
        body = json.loads(last(http).body)
        assert body["type"] == "bitstreamformat" and body["supportLevel"] == "KNOWN" and body["extensions"] == ["yml"]
        assert mgr._format_cache is None

        mgr._format_cache = [{"id": 1}]
        http.add(responses.PUT, api_url("/core/bitstreamformats/9"), json={"id": 9})
        mgr.update_bitstream_format(9, {"shortDescription": "YAML", "id": 123})
        body = json.loads(last(http).body)
        assert body["id"] == 9 and body["type"] == "bitstreamformat"
        assert mgr._format_cache is None

        mgr._format_cache = [{"id": 1}]
        http.add(responses.DELETE, api_url("/core/bitstreamformats/9"), status=204)
        assert mgr.delete_bitstream_format(9) is True
        assert mgr._format_cache is None
