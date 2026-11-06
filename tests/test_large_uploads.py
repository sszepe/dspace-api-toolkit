"""
Tests for streamed (large file) uploads in bundles.py: _MultipartStream,
_OpenedSource / _source_size, progress, retries, timeouts and constant memory.
"""

import hashlib
import io
import os
import tracemalloc
from unittest.mock import Mock

import pytest
import responses

from dspace_api.auth import DSpaceAuth
from dspace_api.bundles import BundleManager, _MultipartStream, _OpenedSource, _source_size
from dspace_api.config import DSpaceConfig
from dspace_api.exceptions import DSpaceError

pytestmark = pytest.mark.bundles

API = "http://localhost:8080/server/api"
URL = API + "/core/bundles/b1/bitstreams"


def bitstream_for(content, uuid="bs-1"):
    return {"uuid": uuid, "sizeBytes": len(content),
            "checkSum": {"checkSumAlgorithm": "MD5", "value": hashlib.md5(content).hexdigest()}}


class ZeroFile(io.RawIOBase):
    """Seekable binary file of `size` zero bytes that never exists in memory."""

    def __init__(self, size):
        self.size, self.pos = size, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=os.SEEK_SET):
        self.pos = {os.SEEK_SET: offset, os.SEEK_CUR: self.pos + offset, os.SEEK_END: self.size + offset}[whence]
        return self.pos

    def read(self, n=-1):
        n = self.size - self.pos if n is None or n < 0 else min(n, self.size - self.pos)
        self.pos += n
        return bytes(n)

    def fileno(self):
        raise io.UnsupportedOperation("no fileno")


class Pipe:
    """Non-seekable binary stream (like a pipe or socket)."""

    def __init__(self, data, text=False):
        self._buf = io.BytesIO(data)
        self.text = text

    def read(self, n=-1):
        chunk = self._buf.read(n)
        return chunk.decode() if self.text else chunk

    def tell(self):
        raise OSError("Illegal seek")


@pytest.fixture
def mgr(http_auth):
    return BundleManager(http_auth)


# ---------------------------------------------------------------------------
# The multipart body
# ---------------------------------------------------------------------------

class TestMultipartStream:
    def test_body_layout_length_and_digest(self):
        content = b"x" * 1000
        stream = _MultipartStream(io.BytesIO(content), len(content), 'we"ird\\name\r\n.txt', "text/plain",
                                  '{"name": "n"}', chunk_size=1)
        body = b"".join(stream)
        assert len(body) == len(stream)
        assert body.startswith(f"--{stream.boundary}\r\n".encode())
        assert body.endswith(f"\r\n--{stream.boundary}--\r\n".encode())
        assert b'filename="we\\"ird\\\\name  .txt"' in body
        assert b'name="properties"\r\nContent-Type: application/json\r\n\r\n{"name": "n"}\r\n' in body
        assert stream.content_type == f"multipart/form-data; boundary={stream.boundary}"
        # chunk size is at least 64 KiB, so 1000 bytes arrive in one chunk
        assert stream.file_digest() == (hashlib.md5(content).hexdigest(), 1000)

    def test_restarts_from_original_position(self):
        fh = io.BytesIO(b"HEADERpayload")
        fh.seek(6)
        stream = _MultipartStream(fh, 7, "a", "text/plain", "{}")
        first, second = b"".join(stream), b"".join(stream)
        assert first == second and b"payload" in first and b"HEADER" not in first
        assert stream.bytes_read == 7

    def test_progress(self):
        calls = []
        stream = _MultipartStream(io.BytesIO(bytes(200 * 1024)), 200 * 1024, "a", "x/y", "{}",
                                  chunk_size=64 * 1024, progress=lambda done, total: calls.append((done, total)))
        b"".join(stream)
        assert calls == [(65536, 204800), (131072, 204800), (196608, 204800), (204800, 204800)]

    def test_file_shorter_than_announced(self):
        stream = _MultipartStream(io.BytesIO(b"abc"), 10, "a", "x/y", "{}")
        with pytest.raises(DSpaceError, match="File ended after 3 of 10 bytes"):
            b"".join(stream)

    def test_digest_fallback_when_body_not_consumed(self):
        stream = _MultipartStream(io.BytesIO(b"abcdef"), 6, "a", "x/y", "{}")
        assert stream.file_digest() == (hashlib.md5(b"abcdef").hexdigest(), 6)
        short = _MultipartStream(io.BytesIO(b"abc"), 6, "a", "x/y", "{}")
        assert short.file_digest() == (hashlib.md5(b"abc").hexdigest(), 3)


# ---------------------------------------------------------------------------
# Opening sources
# ---------------------------------------------------------------------------

class TestSources:
    def test_source_size_variants(self, tmp_path):
        path = tmp_path / "f.bin"
        path.write_bytes(b"0123456789")
        with open(path, "rb") as fh:
            fh.seek(4)
            assert _source_size(fh) == 6                     # fstat - position
        bio = io.BytesIO(b"abcdef")
        bio.seek(2)
        assert _source_size(bio) == 4 and bio.tell() == 2   # seek to end and back
        assert _source_size(Pipe(b"x")) is None
        assert _source_size(object()) is None

    def test_path_bytes_and_close(self, tmp_path):
        path = tmp_path / "doc.pdf"
        path.write_bytes(b"%PDF")
        src = _OpenedSource(str(path), None)
        assert (src.name, src.size) == ("doc.pdf", 4)
        src.close()
        assert src.file.closed
        src.close()  # idempotent
        with _OpenedSource(bytearray(b"abc"), "a.bin") as raw:
            assert raw.size == 3
        assert raw.file.closed

    def test_caller_stream_is_not_closed(self):
        fh = io.BytesIO(b"abc")
        fh.name = "/tmp/named.txt"
        with _OpenedSource(fh, None) as src:
            assert src.name == "named.txt"
        assert not fh.closed

    def test_non_seekable_stream_is_spooled(self):
        with _OpenedSource(Pipe(b"streamed data"), "s.txt") as src:
            assert src.size == 13 and src.file.read() == b"streamed data"
            spool = src.file
        assert spool.closed

    def test_errors(self):
        with pytest.raises(TypeError, match="Unsupported"):
            _OpenedSource(123, "x")
        with pytest.raises(ValueError, match="name is required"):
            _OpenedSource(b"abc", None)
        with pytest.raises(TypeError, match="binary mode"):
            _OpenedSource(io.StringIO("text"), "t.txt")
        with pytest.raises(TypeError, match="binary mode"):
            _OpenedSource(Pipe(b"text", text=True), "t.txt")

    def test_open_source_and_read_source(self, mgr):
        with mgr._open_source(b"abc", "a") as src:
            assert src.size == 3
        assert mgr._read_source(b"abc", "a") == ("a", b"abc")


# ---------------------------------------------------------------------------
# upload_bitstream over the wire
# ---------------------------------------------------------------------------

class TestStreamedUpload:
    def test_request_is_streamed_with_content_length(self, mgr, http):
        content = os.urandom(300 * 1024)
        http.add(responses.POST, URL, status=201, json=bitstream_for(content))
        progress = Mock()
        mgr.upload_bitstream("b1", io.BytesIO(content), name="big.bin", progress=progress, chunk_size=100 * 1024)
        request = http.calls[-1].request
        assert int(request.headers["Content-Length"]) == len(request.body)
        assert "Transfer-Encoding" not in request.headers
        assert content in request.body
        assert progress.call_count == 3 and progress.call_args.args == (len(content), len(content))

    def test_upload_from_middle_of_file(self, mgr, http):
        fh = io.BytesIO(b"skip-this|payload")
        fh.seek(10)
        http.add(responses.POST, URL, status=201, json=bitstream_for(b"payload"))
        mgr.upload_bitstream("b1", fh, name="p.txt")
        assert b"skip-this" not in http.calls[-1].request.body

    def test_upload_from_pipe(self, mgr, http):
        http.add(responses.POST, URL, status=201, json=bitstream_for(b"piped"))
        mgr.upload_bitstream("b1", Pipe(b"piped"), name="p.txt")
        assert b"piped" in http.calls[-1].request.body

    def test_retry_resends_complete_body(self, http):
        cfg = DSpaceConfig(API, all_languages=False, max_retries=1, retry_backoff=0)
        auth = DSpaceAuth(cfg)
        auth._authenticated = True
        auth.session.sleep = Mock()
        content = b"retry me" * 1000
        http.add(responses.POST, URL, status=429, headers={"Retry-After": "0"})
        http.add(responses.POST, URL, status=201, json=bitstream_for(content))
        BundleManager(auth).upload_bitstream("b1", content, name="r.bin")
        bodies = [c.request.body for c in http.calls if c.request.method == "POST"]
        assert len(bodies) == 2 and bodies[0] == bodies[1] and content in bodies[1]

    def test_timeout_scales_with_size(self, mgr):
        mgr.config.timeout = 12
        assert mgr._upload_timeout(1024) == (12.0, 300.0)
        assert mgr._upload_timeout(10 * 1024 ** 3) == (12.0, 1024.0)   # 10 GiB at 10 MiB/s
        mgr.config.timeout = None
        assert mgr._upload_timeout(0)[0] == 30.0

    def test_replace_streams_too(self, mgr, http, hal_page):
        http.add(responses.GET, API + "/core/bundles/b1/bitstreams", json=hal_page("bitstreams", []))
        http.add(responses.POST, URL, status=201, json=bitstream_for(b"new"))
        assert mgr.replace_bitstream("b1", Pipe(b"new"), name="a.txt")["uuid"] == "bs-1"


class TestConstantMemory:
    def test_64_mib_upload_never_holds_the_file(self, http_auth):
        """The body is consumed chunk by chunk (as urllib3 does); peak memory stays near one chunk."""
        size = 64 * 1024 * 1024
        chunk = 1024 * 1024

        def fake_send(method, url, data=None, **kwargs):
            sent = sum(len(part) for part in data)   # stream it like a socket would
            assert sent == len(data) and int(kwargs["headers"]["Content-Length"]) == sent
            digest = hashlib.md5()
            zero = bytes(chunk)
            for _ in range(size // chunk):
                digest.update(zero)
            body = {"uuid": "bs-big", "sizeBytes": size,
                    "checkSum": {"checkSumAlgorithm": "MD5", "value": digest.hexdigest()}}
            return Mock(status_code=201, headers={}, content=b"x", json=Mock(return_value=body))

        http_auth.session = Mock()
        http_auth.session.request.side_effect = fake_send
        manager = BundleManager(http_auth)
        tracemalloc.start()
        try:
            result = manager.upload_bitstream("b1", ZeroFile(size), name="big.iso", chunk_size=chunk)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert result["uuid"] == "bs-big"
        assert peak < 16 * chunk, f"peak {peak / 1e6:.1f} MB - the file was buffered"
