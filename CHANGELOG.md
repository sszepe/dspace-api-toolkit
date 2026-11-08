# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-08

First public release.

### Added
- `DSpaceClient` with managers for items, metadata (`MetadataPatch`, registry),
  workspace items / submissions (`submit()`), bundles and bitstreams, resource
  policies and bulk access control, authorization checks, versioning,
  communities, collections (role groups, license, item template, mapped items,
  harvesting, logos), epersons, groups, site / Terms of Use, entity types,
  discovery and vocabularies.
- Streamed multipart uploads for bitstreams and submission files: constant
  memory for any file size, exact `Content-Length` (no chunked encoding), MD5
  and size verified against DSpace, size-scaled read timeout, progress callback,
  non-seekable streams spooled to a temporary file.
- Search and browse: search configuration, filters (`f.<filter>=value,operator`),
  paged `iter_search()` and `count()`, facets (`search_facets`, `get_facet`,
  `iter_facet_values`, `facet_counts`), browse indexes, entries and items.
- Review workflow (`client.workflow`): workflow items (linked entities, by
  submitter or item, return to workspace, delete, section PATCH), pool and
  claimed tasks, claim / approve / reject with reason / select reviewers /
  score / unclaim, `review()` and `approve_all_steps()`, `pending_tasks()`,
  workflow definitions, steps and actions, and supervision orders.
- Relationships for configurable entities (plain DSpace; DSpace-CRIS normally
  has none): relationship types and label resolution, `relate()` /
  `ensure_related()` / `unrelate()` from the focus item's point of view,
  `related_items()` in order, `reorder()`, name variants, replacing an end,
  `copyVirtualMetadata` on delete, listing and searching relationships.
  `EntityTypeManager.get_entity_type()` and `get_relationship_types()`.
- Controlled vocabularies: entries, exact `lookup()`, `check_values()` /
  `invalid_values()` for import validation, `vocabulary_for_field()`, entry
  details, parents, children and `iter_tree()` for hierarchical vocabularies.
- Robust long runs: retries with backoff and `Retry-After`, proactive token
  refresh, re-login after 401, CSRF token rotation.
- `all_languages`, `projection` and `embed` (incl. nested embeds with sizes) on reads.
- DSpace-CRIS end-user agreement: a 403 "No user agreement accepted" raises
  `UserAgreementRequired` (an `AuthorizationError`) with a hint instead of a
  bare "Forbidden". `accept_end_user_agreement()` refreshes the login token
  when it is the logged-in user (the server reads the flag from a token
  claim). New `ignore_end_user_agreement()` / `is_ignoring_end_user_agreement()`
  for service accounts, and opt-in `DSpaceConfig(accept_user_agreement=True)`
  / `DSPACE_ACCEPT_USER_AGREEMENT=true` to accept it automatically for the
  logged-in user. Default behaviour is unchanged.
- Integration test suite against DSpace 8/9/10 and DSpace-CRIS 2024.02/2025.02
  via Docker Compose, with a compatibility matrix (Markdown / HTML / JSON) and
  regression detection.
