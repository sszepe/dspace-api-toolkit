# Contributing

# Contributing to dspace-api-toolkit

Thank you for helping improve the dspace-api-toolkit! Contributions are not limited to code. If you use DSpace or DSpace-CRIS, your experience can help make the toolkit more useful, reliable, and approachable for everyone.

**You do not need to be a Python developer or set up a development environment to contribute.** Reporting an API gap, describing a workflow, improving an example, or telling us how you use the toolkit is valuable.

## Ways to contribute

| Contribution | What helps | Do I need to write code? |
|---|---|---|
| **API gaps and missing capabilities** | Identify REST endpoints, operations, or workflows the toolkit does not yet support | No |
| **Feature requests** | Describe a task you want to accomplish and why it matters | No |
| **Bug reports** | Share what happened, what you expected, and how to reproduce it | No |
| **Documentation** | Clarify instructions, correct inaccuracies, or explain confusing concepts | No |
| **Examples and recipes** | Share a small script, notebook, or step-by-step workflow | Sometimes |
| **Real-world applications** | Describe how you use the toolkit in a repository, integration, migration, or automation | No |
| **Compatibility feedback** | Report behavior across DSpace and DSpace-CRIS versions | No |
| **Code and tests** | Implement fixes, add capabilities, and improve reliability | Yes |

If you are unsure where an idea fits, open a GitHub issue and describe it in your own words. You do not need to propose an implementation.

## Before you contribute

- Check existing issues and pull requests to see whether the topic is already being discussed.
- For questions, ideas, and early feedback, use GitHub Discussions **if enabled**; otherwise open an issue.
- Keep reports focused on one problem or use case where practical.
- Do not share access tokens, passwords, personal data, private repository records, or sensitive server logs. Redact examples before posting.
- If you are reporting a security vulnerability, avoid publishing exploit details in a public issue. Use the repository's private security reporting channel if available, or contact the maintainers privately.

## Report an API gap or missing capability

This is especially helpful when the underlying DSpace REST API supports an operation but the toolkit does not expose it conveniently—or when support differs between DSpace versions or DSpace-CRIS.

Open an issue with a title such as **API gap: Support [operation/resource]** and include whatever you know:

- **Goal:** What are you trying to do? Describe the user workflow, not only the endpoint.
- **Current limitation:** What is missing, cumbersome, or not working in the toolkit?
- **Relevant API details:** REST method, endpoint, request/response shape, or a link to upstream documentation, if known.
- **Environment:** DSpace or DSpace-CRIS, version, and toolkit version, if relevant.
- **Desired experience:** What would a useful toolkit interface look like? A short pseudocode example is welcome but optional.
- **Workaround or impact:** How are you handling this today, and what would improving it enable?

You do **not** need to know the correct REST endpoint to report a gap. Describing the workflow is enough to start a discussion.

## Suggest a feature or improvement

A good feature request explains the problem before prescribing a solution. Tell us:

1. **Who needs it?** For example, repository administrators, metadata curators, migration teams, or application developers.
2. **What are they trying to accomplish?** Include the workflow and its scale where useful.
3. **What is difficult today?** Explain missing functionality, repetitive steps, or unexpected behavior.
4. **What would success look like?** Describe an expected outcome or possible API design.
5. **Which environments matter?** Note DSpace/DSpace-CRIS versions and any known differences.

Suggestions for higher-level helpers, clearer errors, better pagination, bulk operations, or more consistent APIs are welcome, even when no single REST endpoint is missing.

## Share a real-world application

We especially welcome stories from actual deployments and integrations. These help prioritize features and reveal workflows that tests alone cannot capture.

Useful topics include repository automation, ingest pipelines, metadata cleanup, migrations, synchronization, reporting, research information systems, and integrations with other services.

You might share:

- **Context:** What kind of repository or integration are you working with?
- **Workflow:** What does the toolkit do, and where does it fit in the process?
- **Environment:** Which DSpace or DSpace-CRIS versions are involved?
- **Scale and constraints:** Approximate record counts, performance needs, or operational limitations, if shareable.
- **What worked / what was difficult:** Useful capabilities, gaps, workarounds, and surprises.
- **Reusable material:** A sanitized example, architecture sketch, or documentation link, if available.

You can keep your organization anonymous and omit proprietary details. A short description is enough; publishing your application or source code is not required. If you are comfortable having your use case referenced publicly in project documentation, say so explicitly.

## Improve documentation and examples

Documentation contributions are first-class contributions. They may be as small as fixing a misleading sentence or as substantial as a tutorial.

Particularly useful additions include:

- Getting-started instructions for common tasks.
- Examples of authentication, searching, pagination, item or bitstream workflows, and error handling.
- DSpace versus DSpace-CRIS compatibility notes.
- Troubleshooting guides based on actual problems.
- End-to-end recipes showing how several toolkit operations fit together.

For a new example, aim to include the **goal**, **prerequisites and supported versions**, **minimal runnable code or clear pseudocode**, **expected result**, and **important limitations**. Use placeholders for credentials and URLs; never commit real secrets or sensitive repository content. Prefer small, reproducible examples that readers can adapt.

For substantial examples or documentation reorganizations, consider opening an issue first to agree on the best location and scope. Small corrections can go straight into a pull request.

## Report a bug or compatibility problem

Please include:

- What you expected and what actually happened.
- A minimal reproduction (code or steps), if possible.
- Toolkit version, Python version, and DSpace/DSpace-CRIS version.
- Relevant sanitized errors, logs, HTTP status codes, or response excerpts.
- Whether the problem occurs consistently or only in certain environments.

If the behavior is specific to a DSpace version, that is useful information even if you cannot provide a fix. Avoid testing potentially destructive reproductions against production systems.

## Contributing code

Code contributions are welcome, but the setup and test requirements below apply **only when relevant to the change**. Reporting an issue, sharing a use case, or suggesting a feature does not require running tests. Documentation-only changes generally do not need integration tests.

### Development setup

```bash
git clone https://github.com/OWNER/dspace-api-toolkit
cd dspace-api-toolkit
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Replace `OWNER` with the repository's GitHub owner.

### Test layers

The project uses two complementary test layers:

| Layer | Where | Runs against | When |
|---|---|---|---|
| Unit tests | `tests/test_*.py` | Mocked HTTP (`responses`) | Every push / PR; Python 3.10–3.14 on Linux, Windows, and macOS |
| Regression (integration) tests | `tests/integration/` | Real DSpace / DSpace-CRIS in Docker Compose | `main` (one target); weekly and every release (all targets) |

### Unit tests

```bash
pytest  # All unit tests and coverage report; fails below 100%
pytest tests/test_bundles.py -k upload -o addopts=  # Quick focused run without coverage
```

- Coverage is a hard gate: **100% line and branch coverage**. New code needs tests; genuinely unreachable lines may use `# pragma: no cover` with an explanation.
- Tests use the real `requests` stack with `responses`. Fixtures `http`, `http_auth`, `api_url`, and `hal_page` live in `tests/conftest.py`.
- Headers, query strings, and multipart bodies are exercised as DSpace would receive them. Streamed request bodies are read as a real socket would, so `http.calls[-1].request.body` is bytes.

### Regression tests with Docker Compose

The runner starts each DSpace version from its **upstream** `docker-compose.yml` plus a generated override, runs `tests/integration`, writes a report, and tears the stack down again.

**Requirements:**

- Docker Engine with **Compose v2.24+** (`docker compose version`).
- Approximately 4 GB free RAM per target and 10 GB disk space for images.
- Network access to Docker Hub and `raw.githubusercontent.com`.
- `pip install -e ".[integration]"` (also included in `[dev]`).

**Run:**

```bash
python -m integration.run_matrix --list  # Available targets
python -m integration.run_matrix -t dspace-9  # One target; first start: 5–15 min
python -m integration.run_matrix -t dspace-9 -t cris-2025.02
python -m integration.run_matrix  # All targets, sequentially

# Run selected tests and keep the stack for debugging
python -m integration.run_matrix -t dspace-9 --keep -- -k "workspace or bundles"
```

Open `reports/matrix.html` or `reports/matrix.md` afterwards. With `--keep`, the REST API stays on the target's port (`--list` shows it), allowing direct reruns:

```bash
DSPACE_IT_URL=http://localhost:18090/server/api \
DSPACE_IT_USER=admin@example.org \
DSPACE_IT_PASSWORD='<test-stack-password>' \
pytest tests/integration/test_it_items.py -o addopts= -v
```

Use the credentials configured for your disposable test stack. Stop a kept stack by rerunning the target without `--keep`, or with `docker compose -p dsit-<target> down -v` (for example, `docker compose -p dsit-dspace-9 down -v`). Container logs for each run are in `reports/<target>-docker.log`.

Large-file uploads are tested with a 64 MB file by default. To test a 2 GB upload (the test stacks allow up to 4 GB):

```bash
DSPACE_IT_LARGE_UPLOAD_MB=2048 python -m integration.run_matrix -t dspace-9
```

**Against an existing test server:**

```bash
DSPACE_IT_PASSWORD='<test-server-password>' \
python -m integration.run_matrix --existing \
  --url https://dspace-test.example.org/server/api \
  --user admin@example.org --name test-server
```

This requires an administrator account. The runner creates resources inside a temporary sandbox community and deletes them afterwards. 

WARNING: **Never run these tests against production instances!**

**Compare with a previous run:**

```bash
python -m integration.build_matrix --reports reports --baseline old-reports/matrix.json
```

Cells that passed before but fail now are marked 🔻, and the exit code is `1`.

### Adding or changing a feature

1. Discuss the workflow or API gap in an issue when the design or scope is unclear.
2. Implement it in `dspace_api/`. Managers derive from `BaseManager`; use `self._request`, `self._get_json`, and `self._iter_embedded` so retries, token refresh, CSRF, and `projection`/`embed` handling remain consistent.
3. Add unit tests to maintain 100% line and branch coverage.
4. Add an integration test when the change introduces or changes behavior against a real DSpace API: register a feature ID in `integration/features.py` and mark a test in `tests/integration/test_it_*.py` with `@pytest.mark.feature("<id>")`. Use `unsupported_on(...)` for endpoints absent from older versions, so the matrix reports **not supported** instead of a failure.
5. Add or update user-facing documentation and a practical example when the behavior needs explanation.
6. Add an entry under `## [Unreleased]` in `CHANGELOG.md` for user-visible changes.
7. If REST endpoint coverage changes, update the contract coverage report as described in [REST contract coverage](docs/REST_CONTRACT_COVERAGE.md#how-to-update).

### Pull requests

Keep each PR focused on one topic. In the description, explain **what changed, why it matters, and how you validated it**. Link a relevant issue if one exists.

- **Code changes:** Include appropriate tests; CI must pass (unit tests across supported Python versions and build checks).
- **Request handling changes** (sessions, authentication, uploads): Run integration tests against at least one target and mention the result.
- **Documentation or example changes:** Explain what readers can now do or understand; include verification steps where useful.
- **Compatibility changes:** Mention the DSpace/DSpace-CRIS versions tested and any versions known not to support the feature.

Maintainers may suggest a smaller scope or a different design to keep the public API consistent. Early discussion is encouraged for larger changes.

### Line endings

The repository stores LF line endings (`.gitattributes`: `* text=auto`). Git may convert to CRLF on Windows checkouts when `core.autocrlf` is enabled, so edit using your platform's normal settings.

## Not sure where to start?

Start with something you encountered while using the toolkit: a confusing instruction, a missing operation, a version-specific difference, or a workflow you wish were easier. Open an issue with as much or as little detail as you have. Maintainers and other contributors can help refine it.

**Thank you for sharing what you need, what you have learned, and what you have built.** Those contributions shape the toolkit just as much as code does.
