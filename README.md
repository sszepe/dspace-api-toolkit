# dspace-api-toolkit

[![CI](https://github.com/sszepe/dspace-api-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/sszepe/dspace-api-toolkit/actions/workflows/ci.yml)
[![Compatibility matrix](https://github.com/sszepe/dspace-api-toolkit/actions/workflows/integration-matrix.yml/badge.svg)](https://github.com/sszepe/dspace-api-toolkit/actions/workflows/integration-matrix.yml)
[![PyPI](https://img.shields.io/pypi/v/dspace-api-toolkit.svg)](https://pypi.org/project/dspace-api-toolkit/)
[![Python](https://img.shields.io/pypi/pyversions/dspace-api-toolkit.svg)](https://pypi.org/project/dspace-api-toolkit/)
[![License](https://img.shields.io/pypi/l/dspace-api-toolkit.svg)](LICENSE)

A Python toolkit for the REST API of **DSpace 7+** and **DSpace-CRIS**, built
for repository administrators and integration scripts: batch imports,
submissions, files of any size, access control, collection setup, versioning -
and long unattended runs that survive token expiry, rate limits and restarts.

Every feature is tested against mocked HTTP with pytest with a target 
100% coverage **and** against real DSpace / DSpace-CRIS installations started 
with Docker Compose; the results form a [compatibility matrix](#compatibility).

```bash
pip install dspace-api-toolkit
```

Requires Python 3.10+. The import name of the dspace-api-toolkit in python code 
using it is `dspace_api`.

## Quick start

```python
from dspace_api import DSpaceClient, MetadataPatch

client = DSpaceClient("https://repo.example.org/server/api", "admin@example.org", "secret")

# Submit an item through the submission process, with an embargoed file
result = client.workspace.submit(
    collection_uuid,
    {"dc.title": "My thesis", "dc.contributor.author": ["Doe, Jane"], "dc.date.issued": "2026"},
    files=["thesis.pdf"],
    file_access_conditions=[{"name": "embargo", "startDate": "2027-01-01"}],
)
item_uuid = result["item_uuid"]

# Upload a large file - streamed, constant memory, MD5 verified
bundle = client.bundles.get_or_create_bundle(item_uuid, "ORIGINAL")
client.bundles.upload_bitstream(bundle["uuid"], "dataset.zip",
                                progress=lambda done, total: print(f"{done / total:.0%}"))

# Precise metadata edits
client.metadata.patch_metadata("item", item_uuid,
                               MetadataPatch().append("dc.subject", "music").move("dc.contributor.author", 1, 0))

# Check permissions before acting
if client.authorizations.can(("item", item_uuid), "withdrawItem"):
    ...

client.close()
```

More scripts will be added in the [`examples/`](examples/) folder.

## What's covered

| Area | Manager | Highlights |
|---|---|---|
| Items | `client.items` | create, read, update, delete; `upsert_metadata`, position-aware upserts, move, mapped collections |
| Metadata | `client.metadata` | `MetadataPatch` builder (archived items, submission sections, files), PATCH any object, schema/field registry, `ensure_fields()` |
| Submissions | `client.workspace` | create (empty / external source / BibTeX-RIS file), fill forms by field, upload, file & item access conditions, license, CC license, deposit, one-call `submit()` with validation errors |
| Bundles & files | `client.bundles` | bundles, streamed uploads of any size, replace, download, primary bitstream, move, format registry |
| Access control | `client.permissions` | resource policies, remove / grant / restore group access with CSV reports, bulk access control script |
| Permission checks | `client.authorizations` | `can`, `features_on`, `require`, batch checks, admin scope |
| Versioning | `client.versions` | create versions, `new_version()` flow, histories, drafts |
| Communities | `client.communities` | create, hierarchy and tree walk, admin group, logo, *Authorized searches |
| Collections | `client.collections` | role groups (admin, submitters, item/bitstream read, workflow), read restrictions, submission policies, license, item template, mapped items, harvesting, logo |
| People & groups | `client.epersons`, `client.groups` | users, registration, end-user agreement, groups and members |
| Site & config | `client.site`, `client.entity_types` | Terms of Use per language, entity types |
| Search & browse | `client.discovery` | search configuration, `f.<filter>` filters, paged `iter_search`, `count`, facets with all values (`facet_counts`), browse indexes, entries and items |
| Review workflow | `client.workflow` | workflow items, `pending_tasks`, claim / approve / reject / unclaim, `review()` in one call, `approve_all_steps`, workflow definitions, steps and actions, supervision orders |
| Relationships | `client.relationships` | configurable entities (plain DSpace): relationship types, `relate` / `unrelate` by label, `related_items` in order, `reorder`, name variants, `ensure_related` |
| Vocabularies | `client.vocabulary` | vocabularies and authorities, exact `lookup`, `invalid_values` for import validation, form-field vocabularies, hierarchical trees, `to_metadata_value` |

Every read accepts `all_languages=`, `projection=` and `embed=` (including
nested embeds with sizes) to shape the response.

## Configuration

Arguments win over environment variables, which win over a `.env` file in the
working directory (see [`.env.example`](.env.example)):

| Variable | Default | |
|---|---|---|
| `DSPACE_URL` | `http://localhost:8080/server/api` | REST API root |
| `DSPACE_TIMEOUT` | `30` | seconds per request |
| `DSPACE_MAX_RETRIES` | `3` | retries for 429 / 502 / 503 / 504 and connection errors (idempotent requests; 429 for all) |
| `DSPACE_RETRY_BACKOFF`, `DSPACE_RETRY_MAX_WAIT` | `1.0`, `30` | exponential backoff with jitter, honours `Retry-After` |
| `DSPACE_TOKEN_REFRESH_MARGIN` | `60` | refresh the login token this many seconds before it expires |
| `DSPACE_REAUTHENTICATE` | `true` | log in again after a 401 |
| `DSPACE_ACCEPT_USER_AGREEMENT` | `false` | DSpace-CRIS: accept the end-user agreement for the logged-in user when the server demands it |
| `DSPACE_ALL_LANGUAGES` | `false` | default for `projection=allLanguages` (DSpace-CRIS, DSpace 10; left out automatically on DSpace 8/9) |

`DSpaceConfig(env_file=False)` ignores `.env`; `env_file="prod.env"` reads another file.

## Large files

Uploads (`upload_bitstream`, `replace_bitstream`, `workspace.upload_file`)
stream the multipart body from disk with an exact `Content-Length`: memory
use stays at one chunk (8 MiB) regardless of file size, the MD5 is computed
while sending and compared with DSpace's checksum afterwards, and the read
timeout grows with the file size. The server must allow large requests too:

- DSpace `config/application.properties`: `spring.servlet.multipart.max-file-size`
  and `max-request-size` (default 512MB)
- reverse proxy: nginx `client_max_body_size`, `proxy_request_buffering off`, `proxy_read_timeout`
- Tomcat connector timeout

## Compatibility

The integration suite runs against these Docker Compose stacks (upstream
compose files of each branch):

| Target | |
|---|---|
| DSpace 8.x, 9.x, 10.x | `dspace/dspace` images |
| DSpace-CRIS 2024.02, 2025.02 | `4science/dspace-cris` images |

You can run the same suite against your own **test** installation, from a
clone of the repository:

```bash
git clone https://github.com/sszepe/dspace-api-toolkit && cd dspace-api-toolkit
pip install -e ".[integration]"
DSPACE_IT_PASSWORD=... python -m integration.run_matrix --existing \
    --url https://dspace-test.example.org/server/api --user admin@example.org --name my-test-server
```

The integration tests require an administrator account. They create and delete test 
data. 

WARNING: never point the integration matrix to a production instancce!

## Development

```bash
git clone https://github.com/sszepe/dspace-api-toolkit && cd dspace-api-toolkit
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                                        # unit tests, 100% coverage gate
python -m integration.run_matrix -t dspace-9  # regression tests against a Docker stack
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the regression test workflow and
[integration/README.md](integration/README.md) for the matrix details.

## Related projects

- [dspace-rest-client](https://pypi.org/project/dspace-rest-client/) - the
  DSpace community's general-purpose Python client.

We are very grateful to the team behind dspace-rest-client. Their work inspired this 
project, and we learned a great deal from their approach and implementation. As our 
requirements and goals evolved, we eventually reached a point where we needed to take 
a different direction and develop our own solution. We sincerely appreciate the 
foundation they provided and the work they continue to contribute to the DSpace 
community.

## License

BSD 3-Clause, see [LICENSE](LICENSE).
