# -*- coding: UTF-8 -*-
"""
Bundle and bitstream management for DSpace API (DSpace 7+ / DSpace-CRIS 7+)

Bundles
    GET    /core/items/{itemUuid}/bundles             list bundles of an item
    POST   /core/items/{itemUuid}/bundles             create a bundle
    GET    /core/bundles/{uuid}                       get a bundle
    PATCH  /core/bundles/{uuid}                       metadata / re-order bitstreams
    DELETE /core/bundles/{uuid}                       delete bundle (and ALL its bitstreams)
    GET    /core/bundles/{uuid}/bitstreams            list bitstreams (paginated)
    POST   /core/bundles/{uuid}/bitstreams            upload a bitstream (multipart)
    GET|POST|PUT|DELETE /core/bundles/{uuid}/primaryBitstream

Bitstreams
    GET    /core/bitstreams/{uuid}                    get a bitstream
    PATCH  /core/bitstreams/{uuid}                    metadata
    DELETE /core/bitstreams/{uuid}                    delete one bitstream
    PATCH  /core/bitstreams                           delete several bitstreams
    GET    /core/bitstreams/{uuid}/content            download
    GET|PUT /core/bitstreams/{uuid}/format            read / set bitstream format
    GET|PUT /core/bitstreams/{uuid}/bundle            read / move to another bundle

Bitstream formats (registry)
    GET    /core/bitstreamformats                     list formats
    POST   /core/bitstreamformats                     create (admin)
    GET|PUT|DELETE /core/bitstreamformats/{id}        read / update / delete (admin)

Notes
-----
- Every request goes through ``_request()``, which feeds the response back to
  ``DSpaceAuth._update_token()``. DSpace rotates the CSRF token on some
  responses; without this, long batch runs can start failing with 403.
- Uploads are streamed: the multipart/form-data body is generated on the fly
  from the file (``_MultipartStream``) with an exact Content-Length, so
  requests never switches to chunked transfer encoding (which DSpace does not
  accept reliably) and nothing is read into memory - multi-GB files upload
  with constant memory. MD5 and size are computed while sending, so the file
  is read exactly once, and checked against DSpace's checksum afterwards.
  Non-seekable streams are first spooled to a temporary file (their size must
  be known before sending).
- Large uploads also need the server side to allow them: DSpace's
  ``spring.servlet.multipart.max-file-size`` / ``max-request-size``
  (config/application.properties, default 512MB), the reverse proxy
  (nginx ``client_max_body_size``, ``proxy_request_buffering off``,
  ``proxy_read_timeout``) and the Tomcat connector timeout.
- DSpace guesses the bitstream format from the file extension, not from the
  multipart part's Content-Type. Use ``set_bitstream_format_by_mimetype()``
  (or ``upload_bitstream(..., enforce_mime_format=True)``) if the guess is wrong.
"""

import hashlib
import json
import logging
import mimetypes
import os
import shutil
import tempfile
import uuid as uuid_lib
from typing import Any, BinaryIO, Callable, Dict, Iterator, List, Optional, Tuple, Union

from ._base import BaseManager
from .projection import EmbedArg, ProjectionArg, get_embedded_page, is_embedded_complete
from .config import Endpoints
from .exceptions import DSpaceError

# DSpace bundle and bitstream management utilities
logger = logging.getLogger(__name__)

# Standard DSpace bundle names
ORIGINAL = "ORIGINAL"
THUMBNAIL = "THUMBNAIL"
TEXT = "TEXT"
LICENSE = "LICENSE"

BITSTREAM_FORMATS = getattr(Endpoints, "BITSTREAM_FORMATS", "/core/bitstreamformats")

# Anything upload_bitstream() accepts as file content
FileSource = Union[str, "os.PathLike[str]", bytes, bytearray, BinaryIO]

# progress(bytes_sent, total_bytes) - called once per chunk while uploading
ProgressCallback = Callable[[int, int], None]


def _format_metadata_field(values) -> List[Dict[str, Any]]:
    """Normalise a str / list[str] / list[dict] into DSpace MetadataValue list.

    Parameters:
        values (Union[str, List[str], List[dict], dict]): The metadata values to normalise.

    Returns:
        List[Dict[str, Any]]: A list of DSpace MetadataValue dictionaries.
    """
    if isinstance(values, str):
        return [{"value": values}]
    if isinstance(values, dict):
        return [values]
    if isinstance(values, list):
        out = []
        for v in values:
            out.append(v if isinstance(v, dict) else {"value": str(v)})
        return out
    return [{"value": str(values)}]


def _format_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Normalise a metadata dict into DSpace MetadataValue lists.

    Parameters:
        metadata (Optional[Dict[str, Any]]): The metadata dictionary to normalise.

    Returns:
        Dict[str, List[Dict[str, Any]]]: A dictionary with fields as keys and lists of DSpace MetadataValue dictionaries as values.
    """
    return {field: _format_metadata_field(v) for field, v in (metadata or {}).items()}


class _MultipartStream:
    """
    multipart/form-data body for one file + one JSON "properties" field,
    produced chunk by chunk from a seekable binary file.

    requests treats it as a streamed body (it is iterable) of known length
    (``__len__``), so it sends a Content-Length header instead of chunked
    encoding, and urllib3 writes the chunks straight to the socket. The MD5 of
    the file part is computed on the way, so the file is read only once.
    """

    def __init__(self, fileobj: BinaryIO, size: int, filename: str, mime_type: str,
                 properties: Optional[str], chunk_size: int = 8 * 1024 * 1024,
                 progress: Optional[ProgressCallback] = None):
        """Initialise the multipart stream with the given file, size, filename, MIME type,
        optional JSON properties, chunk size, and progress callback.
        
        Parameters:
            fileobj (BinaryIO): The binary file object to read from.
            size (int): The size of the file in bytes.
            filename (str): The name of the file.
            mime_type (str): The MIME type of the file.
            properties (Optional[str]): Optional JSON properties for the multipart request.
            chunk_size (int, optional): The size of each chunk to read. Defaults to 8 * 1024 * 1024.
            progress (Optional[ProgressCallback], optional): A callback for progress updates. Defaults to None.

        Returns:
            None
        """
        self.boundary = uuid_lib.uuid4().hex
        self.content_type = f"multipart/form-data; boundary={self.boundary}"
        self._file = fileobj
        self._start = fileobj.tell()
        self._size = size
        self._chunk = max(64 * 1024, int(chunk_size))
        self._progress = progress
        safe_name = filename.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
        b = self.boundary
        # properties=None: only the file part (e.g. workspace item uploads)
        properties_part = (
            f"--{b}\r\n"
            'Content-Disposition: form-data; name="properties"\r\n'
            "Content-Type: application/json\r\n\r\n"
            f"{properties}\r\n"
        ) if properties is not None else ""
        self._head = (
            properties_part +
            f"--{b}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        self._tail = f"\r\n--{b}--\r\n".encode("ascii")
        self.md5 = hashlib.md5()  # noqa: S324 - DSpace's checksum algorithm
        self.bytes_read = 0

    def __len__(self) -> int:
        """Return the total length of the multipart stream.

        Returns:
            int: The total length of the multipart stream in bytes.
        """
        return len(self._head) + self._size + len(self._tail)

    def __iter__(self) -> Iterator[bytes]:
        """Yield the multipart stream in chunks, updating the MD5 digest and progress callback.

        Yields:
            bytes: Chunks of the multipart stream.
        """
        self._file.seek(self._start)
        self.md5 = hashlib.md5()  # noqa: S324
        self.bytes_read = 0
        yield self._head
        remaining = self._size
        while remaining > 0:
            chunk = self._file.read(min(self._chunk, remaining))
            if not chunk:
                raise DSpaceError(f"File ended after {self.bytes_read} of {self._size} bytes while uploading")
            remaining -= len(chunk)
            self.bytes_read += len(chunk)
            self.md5.update(chunk)
            if self._progress:
                self._progress(self.bytes_read, self._size)
            yield chunk
        yield self._tail

    def file_digest(self) -> Tuple[str, int]:
        """
        Compute the MD5 digest and size of the file part.

        Uses the digest computed while sending when the whole file went out;
        otherwise (body not consumed by the transport, upload aborted midway)
        re-reads the file once.

        Returns:
            Tuple[str, int]: A tuple containing the MD5 hex digest and the size of the file part in bytes.
        """
        if self.bytes_read == self._size:
            return self.md5.hexdigest(), self.bytes_read
        digest = hashlib.md5()  # noqa: S324
        self._file.seek(self._start)
        remaining = self._size
        while remaining > 0:
            chunk = self._file.read(min(self._chunk, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
        return digest.hexdigest(), self._size - remaining


def _source_size(fh: BinaryIO) -> Optional[int]:
    """Bytes from the current position to the end, or None if the stream can't tell.

    Parameters:
        fh (BinaryIO): The binary file object to check the size of.

    Returns:
        Optional[int]: The number of bytes from the current position to the end, or None if unknown.
    """
    try:
        pos = fh.tell()
        try:
            return os.fstat(fh.fileno()).st_size - pos
        except (AttributeError, OSError, ValueError):
            end = fh.seek(0, os.SEEK_END)
            fh.seek(pos)
            return end - pos
    except (AttributeError, OSError, ValueError):
        return None


class _OpenedSource:
    """A FileSource as (name, seekable binary file, size); closes what it opened."""

    def __init__(self, source: FileSource, name: Optional[str]):
        """
        Initialize an opened source from a file source.

        Parameters:
            source (FileSource): The file source to open.
            name (Optional[str]): The name of the file, if available.

        Raises:
            TypeError: If the source type is unsupported or not opened in binary mode.
            ValueError: If the name is not provided for raw bytes or unnamed streams.
        """
        self._owned = None
        if isinstance(source, (bytes, bytearray)):
            import io
            self.file = self._owned = io.BytesIO(bytes(source))
        elif isinstance(source, (str, os.PathLike)):
            path = os.fspath(source)
            self.file = self._owned = open(path, "rb")
            name = name or os.path.basename(path)
        elif hasattr(source, "read"):
            self.file = source
            name = name or os.path.basename(getattr(source, "name", "") or "")
        else:
            raise TypeError(f"Unsupported file source type: {type(source).__name__}")
        if not name:
            self.close()
            raise ValueError("A bitstream name is required when uploading raw bytes or an unnamed stream")
        self.name = name

        size = _source_size(self.file)
        if size is None:
            # Pipe / socket / other non-seekable stream: spool it so its size is known
            spool = tempfile.TemporaryFile()
            first = self.file.read(0)
            if isinstance(first, str):
                spool.close()
                raise TypeError("File-like source must be opened in binary mode ('rb')")
            shutil.copyfileobj(self.file, spool, 8 * 1024 * 1024)
            spool.seek(0)
            self.close()
            self.file = self._owned = spool
            size = _source_size(spool)
        else:
            probe = self.file.read(0)
            if isinstance(probe, str):
                self.close()
                raise TypeError("File-like source must be opened in binary mode ('rb')")
        self.size = size

    def close(self) -> None:
        """Close the owned file, if any.

        Returns:
            None
        """
        if self._owned is not None:
            self._owned.close()
            self._owned = None

    def __enter__(self):
        """Enter the runtime context related to this object.

        Returns:
            _OpenedSource: The opened source object itself.
        """
        return self

    def __exit__(self, *exc):
        """Exit the runtime context related to this object, closing the owned file if any."""
        self.close()


class BundleManager(BaseManager):
    """Manages DSpace bundles, bitstreams and the bitstream format registry"""

    UPLOAD_TIMEOUT = 300  # seconds; the 30 s config default is too short for large files
    # Lowest throughput assumed when DSpace finishes a large upload (checksum,
    # asset store / S3 copy) before answering: the read timeout grows with size.
    UPLOAD_MIN_BYTES_PER_SECOND = 10 * 1024 * 1024
    UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024

    def __init__(self, auth):
        """Initialise the BundleManager with the given authentication.

        Parameters:
            auth: The authentication object to use for API requests.
        """
        super().__init__(auth)
        self._format_cache: Optional[List[Dict[str, Any]]] = None

    @staticmethod
    def _open_source(source: FileSource, name: Optional[str]) -> _OpenedSource:
        """Open a FileSource for streaming (nothing is read into memory).

        Parameters:
            source (FileSource): The file source to open.
            name (Optional[str]): The name of the file, if available.

        Returns:
            _OpenedSource: The opened source object.

        Raises:
            TypeError: If the source type is unsupported or not opened in binary mode.
            ValueError: If the name is not provided for raw bytes or unnamed streams.
        """
        return _OpenedSource(source, name)

    @staticmethod
    def _read_source(source: FileSource, name: Optional[str]) -> Tuple[str, bytes]:
        """Whole content in memory - kept for callers that want bytes; uploads stream instead.

        Parameters:
            source (FileSource): The file source to read.
            name (Optional[str]): The name of the file, if available.

        Returns:
            Tuple[str, bytes]: The name of the file and its content as bytes.

        Raises:
            TypeError: If the source type is unsupported or not opened in binary mode.
            ValueError: If the name is not provided for raw bytes or unnamed streams.
        """
        if isinstance(source, (bytes, bytearray)):
            content = bytes(source)
        elif isinstance(source, (str, os.PathLike)):
            path = os.fspath(source)
            with open(path, "rb") as fh:
                content = fh.read()
            name = name or os.path.basename(path)
        elif hasattr(source, "read"):
            content = source.read()
            if isinstance(content, str):
                raise TypeError("File-like source must be opened in binary mode ('rb')")
            name = name or os.path.basename(getattr(source, "name", "") or "")
        else:
            raise TypeError(f"Unsupported file source type: {type(source).__name__}")

        if not name:
            raise ValueError("A bitstream name is required when uploading raw bytes or an unnamed stream")
        return name, content

    # ------------------------------------------------------------------
    # Bundles
    # ------------------------------------------------------------------

    def get_item_bundles(self, item_uuid: str, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Return all bundles of an item (all pages).

        Parameters:
            item_uuid (str): The UUID of the item.
            all_languages (Optional[bool]): Whether to include all languages.
            projection (ProjectionArg): The projection to use.
            embed (EmbedArg): The embed parameter to use.

        Returns:
            List[Dict[str, Any]]: A list of bundle dictionaries.
        """
        logger.info("Getting bundles of item %s", item_uuid)
        url = self._url(Endpoints.ITEMS, f"/{item_uuid}/bundles")
        return list(self._iter_embedded(url, "bundles", f"Get bundles of item {item_uuid}",
                                        all_languages=all_languages, projection=projection, embed=embed))

    # Page size asked for embedded bundles / bitstreams; larger lists are paged in
    EMBED_PAGE_SIZE = 100

    def get_item_with_files(self, item_uuid: str, all_languages: Optional[bool] = None
                            ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """
        (item, bundles) in one request where possible; each bundle dict gets a
        complete "bitstreams" list.

        Uses embed=bundles/bitstreams. Embedded lists are pages: if one is
        truncated (more bundles/bitstreams than the embed size), the rest is
        fetched with the paging endpoints, so the result is always complete.
        """
        item = self._get_json(
            self._url(Endpoints.ITEMS, f"/{item_uuid}"), f"Get item {item_uuid} with files",
            all_languages=all_languages,
            embed={"bundles": self.EMBED_PAGE_SIZE, "bundles/bitstreams": self.EMBED_PAGE_SIZE},
        )
        bundles, _ = get_embedded_page(item, "bundles")
        if bundles is None or not is_embedded_complete(item, "bundles"):
            logger.debug("Embedded bundles of %s missing or truncated - paging", item_uuid)
            bundles = self.get_item_bundles(item_uuid, all_languages=all_languages)
        result = []
        for bundle in bundles:
            bitstreams, _ = get_embedded_page(bundle, "bitstreams")
            if bitstreams is None or not is_embedded_complete(bundle, "bitstreams"):
                bitstreams = self.get_bundle_bitstreams(bundle["uuid"], all_languages=all_languages)
            result.append(dict(bundle, bitstreams=bitstreams))
        return item, result

    def find_bundle(self, item_uuid: str, name: str = ORIGINAL) -> Optional[Dict[str, Any]]:
        """Return the first bundle of an item with the given name, or None.

        Parameters:
            item_uuid (str): The UUID of the item.
            name (str): The name of the bundle to find.

        Returns:
            Optional[Dict[str, Any]]: The first matching bundle dictionary, or None if not found.
        """
        for bundle in self.get_item_bundles(item_uuid):
            if bundle.get("name") == name:
                return bundle
        return None

    def get_bundle(self, bundle_uuid: str, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Dict[str, Any]:
        """Return the bundle with the given UUID.

        Parameters:
            bundle_uuid (str): The UUID of the bundle.
            all_languages (Optional[bool]): Whether to include all languages.
            projection (ProjectionArg): The projection to use.
            embed (EmbedArg): The embed parameter to use.

        Returns:
            Dict[str, Any]: The bundle dictionary.
        """
        logger.info("Getting bundle %s", bundle_uuid)
        url = self._url(Endpoints.BUNDLES, f"/{bundle_uuid}")
        return self._get_json(url, f"Get bundle {bundle_uuid}", all_languages=all_languages, projection=projection, embed=embed)

    def create_bundle(self, item_uuid: str, name: str = ORIGINAL,
                      metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Create a bundle on an item (POST /core/items/{uuid}/bundles).

        DSpace does not enforce unique bundle names, so calling this twice
        creates two bundles with the same name. Use get_or_create_bundle()
        unless you really want that.

        Parameters:
            item_uuid (str): The UUID of the item.
            name (str): The name of the bundle to create.
            metadata (Optional[Dict[str, Any]]): The metadata for the bundle.

        Returns:
            Dict[str, Any]: The created bundle dictionary.
        """
        logger.info("Creating bundle '%s' on item %s", name, item_uuid)
        url = self._url(Endpoints.ITEMS, f"/{item_uuid}/bundles")
        body = {"name": name, "metadata": _format_metadata(metadata)}
        bundle = self._request(
            "POST", url, f"Create bundle '{name}' on item {item_uuid}",
            expected=(200, 201), headers=self._headers(), json=body,
        ).json()
        logger.info("Created bundle %s ('%s')", bundle.get("uuid"), name)
        return bundle

    def get_or_create_bundle(self, item_uuid: str, name: str = ORIGINAL,
                             metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Return the item's bundle with this name, creating it if missing.

        Parameters:
            item_uuid (str): The UUID of the item.
            name (str): The name of the bundle to find or create.
            metadata (Optional[Dict[str, Any]]): The metadata for the bundle if it needs to be created.

        Returns:
            Dict[str, Any]: The bundle dictionary.
        """
        bundle = self.find_bundle(item_uuid, name)
        if bundle:
            logger.info("Using existing bundle %s ('%s')", bundle.get("uuid"), name)
            return bundle
        return self.create_bundle(item_uuid, name, metadata)

    def patch_bundle(self, bundle_uuid: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply raw JSON Patch operations to a bundle.
        
        Parameters:
            bundle_uuid (str): The UUID of the bundle.
            operations (List[Dict[str, Any]]): The list of JSON Patch operations to apply.

        Returns:
            Dict[str, Any]: The updated bundle dictionary.
        """
        url = self._url(Endpoints.BUNDLES, f"/{bundle_uuid}")
        response = self._request(
            "PATCH", url, f"Patch bundle {bundle_uuid}", expected=(200, 204),
            headers=self._headers("application/json-patch+json"), json=operations,
        )
        return response.json() if response.status_code == 200 and response.content else self.get_bundle(bundle_uuid)

    def move_bitstream_in_bundle(self, bundle_uuid: str, from_index: int, to_index: int) -> Dict[str, Any]:
        """Re-order bitstreams inside a bundle (0-based positions).

        Parameters:
            bundle_uuid (str): The UUID of the bundle.
            from_index (int): The current index of the bitstream.
            to_index (int): The target index of the bitstream.

        Returns:
            Dict[str, Any]: The updated bundle dictionary.
        """
        logger.info("Moving bitstream in bundle %s from position %d to %d", bundle_uuid, from_index, to_index)
        return self.patch_bundle(bundle_uuid, [{
            "op": "move",
            "from": f"/_links/bitstreams/{from_index}/href",
            "path": f"/_links/bitstreams/{to_index}/href",
        }])

    def delete_bundle(self, bundle_uuid: str) -> bool:
        """Delete a bundle and all its bitstreams.

        Parameters:
            bundle_uuid (str): The UUID of the bundle.

        Returns:
            bool: True if the bundle was deleted successfully.
        """
        logger.info("Deleting bundle %s (including its bitstreams)", bundle_uuid)
        url = self._url(Endpoints.BUNDLES, f"/{bundle_uuid}")
        self._request("DELETE", url, f"Delete bundle {bundle_uuid}", expected=(204,))
        return True

    # ------------------------------------------------------------------
    # Primary bitstream
    # ------------------------------------------------------------------

    def get_primary_bitstream(self, bundle_uuid: str) -> Optional[Dict[str, Any]]:
        """Return the primary bitstream of a bundle, or None if none is set.

        Parameters:
            bundle_uuid (str): The UUID of the bundle.

        Returns:
            Optional[Dict[str, Any]]: The primary bitstream dictionary, or None if not set.
        """
        url = self._url(Endpoints.BUNDLES, f"/{bundle_uuid}/primaryBitstream")
        response = self._request("GET", url, f"Get primary bitstream of bundle {bundle_uuid}", expected=(200, 204))
        if response.status_code == 204 or not response.content:
            return None
        return response.json() or None

    def set_primary_bitstream(self, bundle_uuid: str, bitstream_uuid: str) -> Dict[str, Any]:
        """Mark a bitstream as primary (POST if none yet, PUT to change it).

        Parameters:
            bundle_uuid (str): The UUID of the bundle.
            bitstream_uuid (str): The UUID of the bitstream to set as primary.

        Returns:
            Dict[str, Any]: The updated bundle dictionary.
        """
        logger.info("Setting primary bitstream of bundle %s to %s", bundle_uuid, bitstream_uuid)
        url = self._url(Endpoints.BUNDLES, f"/{bundle_uuid}/primaryBitstream")
        has_primary = self.get_primary_bitstream(bundle_uuid) is not None
        method, expected = ("PUT", (200,)) if has_primary else ("POST", (200, 201))
        response = self._request(
            method, url, f"Set primary bitstream of bundle {bundle_uuid}", expected=expected,
            headers=self._headers("text/uri-list"),
            data=self._uri_list(Endpoints.BITSTREAMS, f"/{bitstream_uuid}"),
        )
        return response.json() if response.content else self.get_bundle(bundle_uuid)

    def clear_primary_bitstream(self, bundle_uuid: str) -> bool:
        """Unset the primary bitstream (the bitstream itself is kept). Returns False if none was set.
        
        Parameters:
            bundle_uuid (str): The UUID of the bundle.

        Returns:
            bool: True if the primary bitstream was cleared, False if none was set.
        """
        if self.get_primary_bitstream(bundle_uuid) is None:
            return False
        url = self._url(Endpoints.BUNDLES, f"/{bundle_uuid}/primaryBitstream")
        self._request("DELETE", url, f"Clear primary bitstream of bundle {bundle_uuid}", expected=(204,))
        return True

    # ------------------------------------------------------------------
    # Bitstreams inside a bundle
    # ------------------------------------------------------------------

    def get_bundle_bitstreams(self, bundle_uuid: str, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Return all bitstreams of a bundle (all pages).
        
        Parameters:
            bundle_uuid (str): The UUID of the bundle.
            all_languages (Optional[bool]): Whether to include all languages.
            projection (ProjectionArg): The projection to use.
            embed (EmbedArg): The embed parameter to use.

        Returns:
            List[Dict[str, Any]]: A list of bitstream dictionaries.
        """
        url = self._url(Endpoints.BUNDLES, f"/{bundle_uuid}/bitstreams")
        return list(self._iter_embedded(url, "bitstreams", f"Get bitstreams of bundle {bundle_uuid}",
                                        all_languages=all_languages, projection=projection, embed=embed))

    def find_bitstreams_by_name(self, bundle_uuid: str, name: str) -> List[Dict[str, Any]]:
        """All bitstreams in the bundle with exactly this name (DSpace allows duplicates).
        
        Parameters:
            bundle_uuid (str): The UUID of the bundle.
            name (str): The name of the bitstreams to find.

        Returns:
            List[Dict[str, Any]]: A list of bitstream dictionaries with the specified name.
        """
        return [b for b in self.get_bundle_bitstreams(bundle_uuid) if b.get("name") == name]

    def upload_bitstream(
        self,
        bundle_uuid: str,
        source: FileSource,
        name: Optional[str] = None,
        mime_type: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        verify_checksum: bool = True,
        enforce_mime_format: bool = False,
        timeout: Optional[Union[float, Tuple[float, float]]] = None,
        progress: Optional[ProgressCallback] = None,
        chunk_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Upload a file as a new bitstream into a bundle - streamed, any size.

        Parameters:
            bundle_uuid (str): The UUID of the target bundle.
            source (FileSource): File path, bytes, or binary file object (read from its
                current position; non-seekable streams are spooled to a temp file).
            name (Optional[str]): Bitstream name (defaults to the file's basename).
            mime_type (Optional[str]): Content-Type of the file part (guessed from name if omitted).
            description (Optional[str]): Convenience for metadata dc.description.
            metadata (Optional[Dict[str, Any]]): Extra bitstream metadata, e.g. {"dc.rights": "open access"}.
            verify_checksum (bool): Compare DSpace's MD5 and size with the ones computed
                while sending; on mismatch the new bitstream is deleted and
                DSpaceError is raised.
            enforce_mime_format (bool): After upload, set the registry format matching
                mime_type if DSpace guessed a different one.
            timeout (Optional[Union[float, Tuple[float, float]]]): Request timeout; default (connect, read) with the read
                timeout scaled to the file size (UPLOAD_MIN_BYTES_PER_SECOND).
            progress (Optional[ProgressCallback]): progress(bytes_sent, total_bytes), called per chunk.
            chunk_size (Optional[int]): Bytes read and sent per chunk (default 8 MiB).

        Returns:
            dict: the created bitstream

        A failed upload (connection lost, timeout) may leave a bitstream in
        DSpace if the server had already received the file; check with
        find_bitstreams_by_name() before retrying if that matters.
        """
        with self._open_source(source, name) as src:
            name = src.name
            mime_type = mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream"

            md = dict(metadata or {})
            if description and "dc.description" not in md:
                md["dc.description"] = description
            properties = {"name": name, "metadata": _format_metadata(md)}

            body = _MultipartStream(src.file, src.size, name, mime_type, json.dumps(properties),
                                    chunk_size=chunk_size or self.UPLOAD_CHUNK_SIZE, progress=progress)
            headers = self._headers(content_type=None)
            headers["Content-Type"] = body.content_type
            headers["Content-Length"] = str(len(body))

            logger.info("Uploading '%s' (%d bytes, %s) to bundle %s", name, src.size, mime_type, bundle_uuid)
            url = self._url(Endpoints.BUNDLES, f"/{bundle_uuid}/bitstreams")
            bitstream = self._request(
                "POST", url, f"Upload bitstream '{name}' to bundle {bundle_uuid}",
                expected=(200, 201),
                headers=headers,
                data=body,
                timeout=timeout or self._upload_timeout(src.size),
            ).json()
            bitstream_uuid = bitstream.get("uuid")
            logger.info("Uploaded '%s' as bitstream %s", name, bitstream_uuid)

            if verify_checksum:
                self._verify_digest(bitstream, *body.file_digest())

        if enforce_mime_format and mime_type != "application/octet-stream":
            self.set_bitstream_format_by_mimetype(bitstream_uuid, mime_type, only_if_different=True)

        return bitstream

    def _upload_timeout(self, size: int) -> Tuple[float, float]:
        """Calculate the upload timeout based on the file size and configuration.
        Parameters:
            size (int): The size of the file being uploaded in bytes.

        Returns:
            Tuple[float, float]: A tuple containing the connect and read timeouts.
        """
        connect = float(getattr(self.config, "timeout", 30) or 30)
        read = max(float(self.UPLOAD_TIMEOUT), size / float(self.UPLOAD_MIN_BYTES_PER_SECOND))
        return connect, read

    def _verify_upload(self, bitstream: Dict[str, Any], content: bytes) -> None:
        """Verify against in-memory content (kept for compatibility).

        Parameters:
            bitstream (Dict[str, Any]): The bitstream metadata returned by DSpace.
            content (bytes): The in-memory content to verify against.

        Raises:
            DSpaceError: If the verification fails.
        """
        self._verify_digest(bitstream, hashlib.md5(content).hexdigest(), len(content))

    def _verify_digest(self, bitstream: Dict[str, Any], local_md5: str, local_size: int) -> None:
        """Verify the uploaded bitstream against the local MD5 digest and size.

        Parameters:
            bitstream (Dict[str, Any]): The bitstream metadata returned by DSpace.
            local_md5 (str): The MD5 digest of the local file.
            local_size (int): The size of the local file in bytes.

        Raises:
            DSpaceError: If the verification fails.
        """
        checksum = bitstream.get("checkSum") or {}
        remote_md5 = (checksum.get("value") or "").lower()
        remote_size = bitstream.get("sizeBytes")
        algorithm = (checksum.get("checkSumAlgorithm") or "MD5").upper()

        problems = []
        if remote_size is not None and remote_size != local_size:
            problems.append(f"size {remote_size} != local {local_size}")
        if algorithm == "MD5" and remote_md5 and remote_md5 != local_md5:
            problems.append(f"MD5 {remote_md5} != local {local_md5}")

        if problems:
            uuid = bitstream.get("uuid")
            logger.error("Upload verification failed for bitstream %s: %s", uuid, "; ".join(problems))
            try:
                if uuid:
                    self.delete_bitstream(uuid)
            except Exception as exc:  # noqa: BLE001
                logger.error("Could not delete corrupt bitstream %s: %s", uuid, exc)
            raise DSpaceError(f"Upload verification failed for bitstream {uuid}: {'; '.join(problems)}")
        logger.debug("Upload verified (MD5 %s, %d bytes)", local_md5, local_size)

    def replace_bitstream(self, bundle_uuid: str, source: FileSource, name: Optional[str] = None,
                          **upload_kwargs) -> Dict[str, Any]:
        """
        Replace all bitstreams named `name` in the bundle with a new upload.

        Upload happens first and old copies are deleted only afterwards, so a
        failed upload never leaves the bundle without the file. If one of the
        old copies was the primary bitstream, the new one becomes primary.

        Parameters:
            bundle_uuid (str): The UUID of the bundle containing the bitstreams to replace.
            source (FileSource): The source of the new file to upload.
            name (Optional[str]): The name of the bitstreams to replace. If not provided, the name is inferred from the source.
            **upload_kwargs: Additional keyword arguments passed to the upload method.

        Returns:
            Dict[str, Any]: The metadata of the newly uploaded bitstream.

        Raises:
            DSpaceError: If the upload or replacement fails.
        """
        with self._open_source(source, name) as src:
            return self._replace_opened(bundle_uuid, src, **upload_kwargs)

    def _replace_opened(self, bundle_uuid: str, src: _OpenedSource, **upload_kwargs) -> Dict[str, Any]:
        """Replace all bitstreams named `src.name` in the bundle with a new upload.
        
        Parameters:
            bundle_uuid (str): The UUID of the bundle containing the bitstreams to replace.
            src (_OpenedSource): The opened source of the new file to upload.
            **upload_kwargs: Additional keyword arguments passed to the upload method.

        Returns:
            Dict[str, Any]: The metadata of the newly uploaded bitstream.

        Raises:
            DSpaceError: If the upload or replacement fails.
        """
        name = src.name
        old = self.find_bitstreams_by_name(bundle_uuid, name)
        primary = self.get_primary_bitstream(bundle_uuid) if old else None
        old_uuids = [b["uuid"] for b in old]
        was_primary = bool(primary and primary.get("uuid") in old_uuids)

        new = self.upload_bitstream(bundle_uuid, src.file, name=name, **upload_kwargs)

        if was_primary:
            # Move the primary flag off the old bitstream before deleting it
            self.set_primary_bitstream(bundle_uuid, new["uuid"])
        if old_uuids:
            self.delete_bitstreams(old_uuids)
            logger.info("Replaced %d old '%s' bitstream(s) in bundle %s", len(old_uuids), name, bundle_uuid)
        return new

    # ------------------------------------------------------------------
    # Single bitstreams
    # ------------------------------------------------------------------

    def get_bitstream(self, bitstream_uuid: str, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Dict[str, Any]:
        """Return the bitstream with the given UUID.

        Parameters:
            bitstream_uuid (str): The UUID of the bitstream to retrieve.
            all_languages (Optional[bool]): Whether to include all language variants of the metadata.
            projection (ProjectionArg): The projection to apply to the response.
            embed (EmbedArg): The embed options for the response.

        Returns:
            Dict[str, Any]: The metadata of the requested bitstream.
        """
        url = self._url(Endpoints.BITSTREAMS, f"/{bitstream_uuid}")
        return self._get_json(url, f"Get bitstream {bitstream_uuid}", all_languages=all_languages, projection=projection, embed=embed)

    def patch_bitstream(self, bitstream_uuid: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply raw JSON Patch operations to a bitstream.

        Parameters:
            bitstream_uuid (str): The UUID of the bitstream to patch.
            operations (List[Dict[str, Any]]): The list of JSON Patch operations to apply.

        Returns:
            Dict[str, Any]: The updated bitstream metadata.

        Raises:
            DSpaceError: If the patch operation fails.
        """
        url = self._url(Endpoints.BITSTREAMS, f"/{bitstream_uuid}")
        response = self._request(
            "PATCH", url, f"Patch bitstream {bitstream_uuid}", expected=(200, 204),
            headers=self._headers("application/json-patch+json"), json=operations,
        )
        return response.json() if response.status_code == 200 and response.content else self.get_bitstream(bitstream_uuid)

    def update_bitstream_metadata(self, bitstream_uuid: str, operation: str,
                                  metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add / replace / remove bitstream metadata fields.

        Parameters:
            bitstream_uuid (str): The UUID of the bitstream to update.
            operation (str): The operation to perform ("add", "replace", or "remove").
            metadata (Dict[str, Any]): The metadata fields to update.

        Returns:
            Dict[str, Any]: The updated bitstream metadata.

        Raises:
            ValueError: If the operation is not one of "add", "replace", or "remove".

        Examples:
            update_bitstream_metadata(uuid, "replace", {"dc.description": "Full text"})
            update_bitstream_metadata(uuid, "remove", {"dc.description": None})
        """
        if operation not in ("add", "replace", "remove"):
            raise ValueError("operation must be 'add', 'replace' or 'remove'")
        ops = []
        for field, values in metadata.items():
            op = {"op": operation, "path": f"/metadata/{field}"}
            if operation != "remove":
                op["value"] = _format_metadata_field(values)
            ops.append(op)
        logger.info("%s metadata %s on bitstream %s", operation, list(metadata), bitstream_uuid)
        return self.patch_bitstream(bitstream_uuid, ops)

    def rename_bitstream(self, bitstream_uuid: str, new_name: str) -> Dict[str, Any]:
        """The bitstream name is its dc.title.

        Parameters:
            bitstream_uuid (str): The UUID of the bitstream to rename.
            new_name (str): The new name for the bitstream.

        Returns:
            Dict[str, Any]: The updated bitstream metadata.

        Raises:
            ValueError: If the operation fails.
        """
        return self.update_bitstream_metadata(bitstream_uuid, "replace", {"dc.title": new_name})

    def delete_bitstream(self, bitstream_uuid: str) -> bool:
        """Delete the bitstream with the given UUID.

        Parameters:
            bitstream_uuid (str): The UUID of the bitstream to delete.

        Returns:
            bool: True if the deletion was successful.

        Raises:
            DSpaceError: If the deletion fails.
        """
        logger.info("Deleting bitstream %s", bitstream_uuid)
        url = self._url(Endpoints.BITSTREAMS, f"/{bitstream_uuid}")
        self._request("DELETE", url, f"Delete bitstream {bitstream_uuid}", expected=(204,))
        return True

    def delete_bitstreams(self, bitstream_uuids: List[str]) -> bool:
        """Delete several bitstreams in one request (PATCH /core/bitstreams).
        
        Parameters:
            bitstream_uuids (List[str]): The list of UUIDs of the bitstreams to delete.

        Returns:
            bool: True if the deletion was successful.

        Raises:
            DSpaceError: If the deletion fails.
        """
        if not bitstream_uuids:
            return True
        logger.info("Deleting %d bitstream(s)", len(bitstream_uuids))
        ops = [{"op": "remove", "path": f"/bitstreams/{u}"} for u in bitstream_uuids]
        self._request(
            "PATCH", self._url(Endpoints.BITSTREAMS), "Delete bitstreams", expected=(204,),
            headers=self._headers("application/json"), json=ops,
        )
        return True

    def download_bitstream(self, bitstream_uuid: str, dest_path: Optional[str] = None,
                           chunk_size: int = 1024 * 1024) -> Union[bytes, str]:
        """Download bitstream content. Returns bytes, or the path if dest_path is given
        (streamed to disk in chunks).

        Parameters:
            bitstream_uuid (str): The UUID of the bitstream to download.
            dest_path (Optional[str]): The path to save the downloaded content. If None, the content is returned as bytes.
            chunk_size (int): The size of chunks to read when streaming to disk.

        Returns:
            Union[bytes, str]: The downloaded content as bytes, or the path if dest_path is provided.

        Raises:
            DSpaceError: If the download fails.
        """
        url = self._url(Endpoints.BITSTREAMS, f"/{bitstream_uuid}/content")
        response = self._request(
            "GET", url, f"Download bitstream {bitstream_uuid}",
            headers=self._headers(content_type=None, accept="*/*"),
            stream=dest_path is not None, timeout=self.UPLOAD_TIMEOUT, all_languages=False,
        )
        if dest_path is None:
            return response.content
        with open(dest_path, "wb") as fh:
            for chunk in response.iter_content(chunk_size=chunk_size):
                fh.write(chunk)
        logger.info("Downloaded bitstream %s to %s", bitstream_uuid, dest_path)
        return dest_path

    def get_bitstream_bundle(self, bitstream_uuid: str) -> Dict[str, Any]:
        """Return the bundle containing the given bitstream.

        Parameters:
            bitstream_uuid (str): The UUID of the bitstream.

        Returns:
            Dict[str, Any]: The bundle containing the bitstream.

        Raises:
            DSpaceError: If the request fails.
        """
        url = self._url(Endpoints.BITSTREAMS, f"/{bitstream_uuid}/bundle")
        return self._request("GET", url, f"Get bundle of bitstream {bitstream_uuid}").json()

    def move_bitstream_to_bundle(self, bitstream_uuid: str, target_bundle_uuid: str) -> Dict[str, Any]:
        """Move a bitstream into another bundle (of the same item). Returns the updated bundle.

        Parameters:
            bitstream_uuid (str): The UUID of the bitstream to move.
            target_bundle_uuid (str): The UUID of the target bundle.

        Returns:
            Dict[str, Any]: The updated bundle containing the bitstream.

        Raises:
            DSpaceError: If the move operation fails.
        """
        logger.info("Moving bitstream %s to bundle %s", bitstream_uuid, target_bundle_uuid)
        url = self._url(Endpoints.BITSTREAMS, f"/{bitstream_uuid}/bundle")
        response = self._request(
            "PUT", url, f"Move bitstream {bitstream_uuid}", expected=(200, 204),
            headers=self._headers("text/uri-list"),
            data=self._uri_list(Endpoints.BUNDLES, f"/{target_bundle_uuid}"),
        )
        return response.json() if response.content else self.get_bitstream_bundle(bitstream_uuid)

    # ------------------------------------------------------------------
    # Bitstream formats
    # ------------------------------------------------------------------

    def get_bitstream_format(self, bitstream_uuid: str) -> Dict[str, Any]:
        """The registry format currently assigned to a bitstream.

        Parameters:
            bitstream_uuid (str): The UUID of the bitstream.

        Returns:
            Dict[str, Any]: The registry format of the bitstream.

        Raises:
            DSpaceError: If the request fails.
        """
        url = self._url(Endpoints.BITSTREAMS, f"/{bitstream_uuid}/format")
        return self._request("GET", url, f"Get format of bitstream {bitstream_uuid}").json()

    def set_bitstream_format(self, bitstream_uuid: str, format_id: int) -> Dict[str, Any]:
        """Assign a registry format (by numeric id) to a bitstream.

        Parameters:
            bitstream_uuid (str): The UUID of the bitstream.
            format_id (int): The numeric ID of the registry format to assign.

        Returns:
            Dict[str, Any]: The updated bitstream metadata.

        Raises:
            DSpaceError: If the operation fails.
        """
        logger.info("Setting format of bitstream %s to %s", bitstream_uuid, format_id)
        url = self._url(Endpoints.BITSTREAMS, f"/{bitstream_uuid}/format")
        response = self._request(
            "PUT", url, f"Set format of bitstream {bitstream_uuid}", expected=(200, 204),
            headers=self._headers("text/uri-list"),
            data=self._uri_list(BITSTREAM_FORMATS, f"/{format_id}"),
        )
        return response.json() if response.content else self.get_bitstream(bitstream_uuid)

    def set_bitstream_format_by_mimetype(self, bitstream_uuid: str, mime_type: str,
                                         only_if_different: bool = True) -> Optional[Dict[str, Any]]:
        """Look up the registry format for mime_type and assign it. Returns None if nothing changed.
        Parameters:
            bitstream_uuid (str): The UUID of the bitstream.
            mime_type (str): The MIME type to look up in the registry.
            only_if_different (bool): If True, only set the format if it differs from the current one.

        Returns:
            Optional[Dict[str, Any]]: The updated bitstream metadata, or None if nothing changed.

        Raises:
            DSpaceError: If the operation fails.
        """
        fmt = self.find_bitstream_format_by_mimetype(mime_type)
        if not fmt:
            logger.warning("No bitstream format with mimetype %s in the registry - format left unchanged", mime_type)
            return None
        if only_if_different:
            current = self.get_bitstream_format(bitstream_uuid)
            if current.get("id") == fmt.get("id"):
                return None
        return self.set_bitstream_format(bitstream_uuid, fmt["id"])

    def get_bitstream_formats(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """Whole format registry (cached per manager instance)."""
        if self._format_cache is None or refresh:
            url = self._url(BITSTREAM_FORMATS)
            self._format_cache = list(self._iter_embedded(url, "bitstreamformats", "Get bitstream formats"))
        return self._format_cache

    def get_bitstream_format_by_id(self, format_id: int) -> Dict[str, Any]:
        """Return the bitstream format with the given ID."""
        url = self._url(BITSTREAM_FORMATS, f"/{format_id}")
        return self._request("GET", url, f"Get bitstream format {format_id}").json()

    def find_bitstream_format_by_mimetype(self, mime_type: str) -> Optional[Dict[str, Any]]:
        """Find a bitstream format by its MIME type. Returns None if not found.

        Parameters:
            mime_type (str): The MIME type to look up in the registry.

        Returns:
            Optional[Dict[str, Any]]: The bitstream format if found, otherwise None.
        """
        mime_type = mime_type.lower()
        for fmt in self.get_bitstream_formats():
            if (fmt.get("mimetype") or "").lower() == mime_type:
                return fmt
        return None

    def create_bitstream_format(self, short_description: str, mimetype: str, description: str = "",
                                support_level: str = "KNOWN", internal: bool = False,
                                extensions: Optional[List[str]] = None) -> Dict[str, Any]:
        """Register a new format (admin only). support_level: UNKNOWN, KNOWN or SUPPORTED.

        Parameters:
            short_description (str): The short description of the format.
            mimetype (str): The MIME type of the format.
            description (str): The detailed description of the format.
            support_level (str): The support level of the format (UNKNOWN, KNOWN, SUPPORTED).
            internal (bool): Whether the format is internal.
            extensions (Optional[List[str]]): The list of file extensions associated with the format.

        Returns:
            Dict[str, Any]: The newly created bitstream format.

        Raises:
            DSpaceError: If the creation fails.
        """
        body = {
            "shortDescription": short_description,
            "description": description,
            "mimetype": mimetype,
            "supportLevel": support_level,
            "internal": internal,
            "extensions": extensions or [],
            "type": "bitstreamformat",
        }
        fmt = self._request("POST", self._url(BITSTREAM_FORMATS), "Create bitstream format",
                            expected=(200, 201), headers=self._headers(), json=body).json()
        self._format_cache = None
        return fmt

    def update_bitstream_format(self, format_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Full update (PUT) of a registry format (admin only).

        Parameters:
            format_id (int): The ID of the bitstream format to update.
            data (Dict[str, Any]): The data to update the format with.

        Returns:
            Dict[str, Any]: The updated bitstream format.
        """
        body = dict(data, id=format_id, type="bitstreamformat")
        fmt = self._request("PUT", self._url(BITSTREAM_FORMATS, f"/{format_id}"),
                            f"Update bitstream format {format_id}", expected=(200, 201),
                            headers=self._headers(), json=body).json()
        self._format_cache = None
        return fmt

    def delete_bitstream_format(self, format_id: int) -> bool:
        """
        Delete a bitstream format by its ID.

        Parameters:
            format_id (int): The ID of the bitstream format to delete.

        Returns:
            bool: True if the deletion was successful.
        """
        self._request("DELETE", self._url(BITSTREAM_FORMATS, f"/{format_id}"),
                      f"Delete bitstream format {format_id}", expected=(204,))
        self._format_cache = None
        return True
