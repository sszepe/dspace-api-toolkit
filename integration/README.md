# Integration tests and compatibility matrix

The unit tests (`tests/test_*.py`) check the toolkit against mocked HTTP.
The integration tests (`tests/integration/`) run the same toolkit against
real DSpace / DSpace-CRIS servers and produce a **compatibility matrix**:
one row per toolkit feature, one column per server version.

```
python -m integration.run_matrix                     # all targets, one after another
python -m integration.run_matrix -t dspace-9 -t cris-2025.02
python -m integration.run_matrix --list
```

Results land in `reports/`:

| File | Content |
|---|---|
| `<target>.json` | every test: features, outcome, message, duration; server version |
| `<target>.xml` | JUnit XML (for CI test views) |
| `<target>-docker.log` | last 3000 container log lines |
| `matrix.md` / `matrix.html` / `matrix.json` | the matrix (HTML: hover a cell for details) |

## Requirements

- Docker with **Compose 2.24 or newer** (the override uses `!reset` / `!override`)
- ~4 GB RAM per running target; the first start of a target takes several
  minutes (image pull + all database migrations)
- Python packages: `requests`, `python-dotenv`, `pyyaml`, `pytest`

## How a target runs

For each entry in `integration/targets.yml` the runner:

1. downloads the **upstream** `docker-compose.yml` of that branch (DSpace or
   4Science) into `.integration/<target>/` - so start commands, Postgres image
   and Solr cores always match the version;
2. adds a generated `override.yml`: no build sections / fixed container names /
   source mounts, REST API on `127.0.0.1:<port>` with a matching
   `dspace.server.url`, Postgres health check before the backend starts, and a
   private subnet per target (so targets can also run in parallel);
3. `docker compose up -d`, waits for `GET /server/api` (aborts early if the
   backend container exits), creates the admin with `dspace create-administrator`;
4. runs `pytest tests/integration` with the report plugin;
5. saves the container logs and runs `docker compose down -v`
   (`--keep` leaves the stack running for debugging).

A target whose stack cannot start gets a report with `setup_error`; the matrix
shows its whole column as failed with the reason.

NOTE: As we didn't find a suitable DSpace 7 docker image, DSpace 7 is not included in 
the test matrix even though the dspace-api-toolkit states that the DSpace 7+ is
supported.

### DSpace settings (local.cfg)

The images ship no `local.cfg`. The runner writes
`.integration/<target>/local.cfg` from the `local_cfg` entries in
`targets.yml` (defaults plus the target's own) and mounts it read-only as
`/dspace/config/local.cfg`. Currently it holds:

```
webui.user.assumelogin = true     # "login as" via X-On-Behalf-Of (tests/integration/test_it_on_behalf.py)
```

A list value repeats the key. Environment variables from the compose files
(`dspace__P__...`) still take precedence over `local.cfg`. DSpace reads the file
at startup only: after changing it, restart the target (`--down`, then `--up`
or a test run).

### Adding or pinning a target

Add an entry to `targets.yml` (unique `name` and `port`). Without `env`, the
upstream defaults are used, i.e. the latest build of the branch. To pin a
release: `env: {DSPACE_VER: <tag>}` - upstream uses that variable for the
backend, Solr and (where used) pgcrypto Postgres images, so the tag must exist
for all of them.

### Testing an existing installation

```
DSPACE_IT_PASSWORD=... python -m integration.run_matrix --existing \
    --url https://dspace-test.example.org/server/api --user admin@example.org --name test-server
```

This needs an **administrator** account. The tests create a sandbox community
("Toolkit IT <run id>") plus test groups and epersons and delete them at the
end - use a test system, not production.

## Reading the matrix

| Cell | Meaning |
|---|---|
| ✅ pass | every test for the feature passed |
| ❌ fail | a test failed or errored - see details / hover text |
| 🟡 partial | some tests passed, others were skipped or unsupported |
| ⛔ not supported | the server does not offer the feature (e.g. endpoint 404) |
| ⏭ skipped | not run (opt-in, missing precondition) |
| · no result | no test for the feature on that target |
| 🔻 | regression: passed in the baseline run, fails now |

Regressions: `python -m integration.build_matrix --reports reports --baseline old/matrix.json`
(exit code 1 if there are any). The CI workflow does this automatically.

## Writing integration tests

```python
import pytest
from .helpers import unsupported_on, values

@pytest.mark.feature("bitstreams.primary")          # id from integration/features.py
def test_primary_bitstream(bundles, new_item):
    item = new_item()                                # created in the sandbox, deleted afterwards
    bundle = bundles.get_or_create_bundle(item["uuid"])
    with unsupported_on(404, 405, what="primaryBitstream"):   # -> "not supported", not "failed"
        ...
```

- Every test needs at least one `@pytest.mark.feature(...)`; unknown ids are
  rejected at collection. New capability -> add it to `integration/features.py`.
- Fixtures (`tests/integration/conftest.py`): managers (`items`, `bundles`,
  `permissions`, `groups`, `epersons`, `communities`, `collections`, `site`,
  `entity_types`, `discovery`), `me`, `sandbox`, `collection`, `entity_type`
  (`"Publication"` or `None` on servers without configurable entities), and
  factories `new_item`, `new_eperson`, `new_group`, `new_collection`,
  `new_auth`, `cleanup`.
- `helpers.eventually(fn, timeout)` polls, e.g. for Solr indexing.
- Without `DSPACE_IT_URL` all integration tests are skipped, so `pytest`
  for the unit tests keeps working as before.

Run one test file against a running stack:

```
DSPACE_IT_URL=http://localhost:18090/server/api DSPACE_IT_USER=admin@example.org \
DSPACE_IT_PASSWORD='Toolkit-IT-2026!' pytest tests/integration/test_it_items.py -o addopts= -v
```

## CI

`.github/workflows/integration-matrix.yml` runs one job per target (weekly and
on demand, optionally for selected targets), uploads each report, builds the
matrix into the job summary, keeps it as the baseline for the next run and
fails the workflow on regressions.
