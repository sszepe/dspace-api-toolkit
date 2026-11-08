# -*- coding: UTF-8 -*-
"""
Catalog of toolkit capabilities tracked in the compatibility matrix.

Integration tests declare what they exercise with
``@pytest.mark.feature("<id>")``; unknown ids fail at collection time, so
the matrix rows stay stable and every row has a description. The prefix
before the first dot is the area used to group rows.
"""

FEATURES = {
    # Authentication / session
    "auth.login": "Password login, CSRF handling, current user",
    "auth.relogin": "Automatic re-login after a 401 (expired/invalid token)",
    "auth.refresh": "Proactive token refresh before expiry",
    # Items
    "items.crud": "Create, read, update (replace) and delete archived items",
    "items.upsert": "upsert_metadata: add new fields, replace existing ones",
    "items.upsert_by_position": "upsert_metadata_by_position: replace/append by place",
    # Bundles / bitstreams
    "bundles.crud": "Create, find and delete bundles",
    "bitstreams.upload": "Multipart upload with checksum verification, download round-trip",
    "bitstreams.large_upload": "Streamed upload of a large file (DSPACE_IT_LARGE_UPLOAD_MB, default 64), verified round-trip",
    "bitstreams.metadata": "Bitstream metadata PATCH and rename",
    "bitstreams.replace": "Replace a bitstream (upload new, delete old)",
    "bitstreams.primary": "Set / read / clear the primary bitstream",
    "bitstreams.move": "Move a bitstream to another bundle",
    "bitstreams.formats": "Bitstream format registry lookup and assignment",
    # Access control
    "policies.read": "Read resource policies with embedded group/eperson",
    "policies.remove_restore": "Remove Anonymous READ from an item and restore it from the report",
    "policies.create_update": "Create a policy with dates, update and delete it",
    "bulk_access.options": "Read bulk access condition options",
    "bulk_access.run": "Run the bulk-access-control script and wait for the process",
    # Groups / epersons
    "groups.crud": "Create, rename and delete groups",
    "groups.members": "Add/remove epersons and subgroups, sync members",
    "epersons.crud": "Create, find, disable and delete epersons",
    "epersons.agreement": "Accept / revoke the end-user agreement",
    # Review process (workflow)
    "workflow.config": "Workflow definitions, steps and actions; definition of a collection",
    "workflow.items": "Workflow item of a deposit, linked entities, find by submitter, return to workspace",
    "workflow.review": "Pool / claimed tasks, claim, reject with reason, resubmit, approve all steps",
    "workflow.supervision": "Supervision orders on an in-progress submission",
    # Communities / collections
    "communities.crud": "Create (sub)communities, admin group, delete",
    "collections.items": "List, page and count the items of a collection (discovery scope)",
    "collections.roles": "Collection admin and submitter groups",
    "collections.read_restriction": "Restrict default item/bitstream read via role groups",
    "collections.submission_policies": "TYPE_SUBMISSION policies on collections",
    # Relationships (configurable entities; DSpace-CRIS normally has none)
    "relationships.types": "Relationship types, by entity type, label resolution",
    "relationships.crud": "Relate items with name variant, find, related items, virtual metadata, unrelate",
    "relationships.order": "Reorder related items, change name variant, replace an end",
    # Site / entity types
    "site.terms_of_use": "Read the Terms of Use from the Site object",
    "entity_types.list": "List entity types and submit-authorized types",
    # Request shaping
    "projection.all_languages": "projection=allLanguages returns every language variant",
    "projection.embed": "embed= / nested embeds and get_item_with_files",
    "projection.full": "projection=full",
    # Metadata
    "metadata.patch": "MetadataPatch on archived objects: insert, move, set_property, remove by index",
    "metadata.registry": "Metadata schema/field registry: create, find, update, delete, unknown_fields",
    # Workspace items / submission
    "workspace.lifecycle": "Start, get, find (by item / submitter), linked resources, delete",
    "workspace.config": "Submission definitions, sections, forms; routing fields to forms",
    "workspace.metadata": "fill_metadata / section PATCH in submission forms",
    "workspace.upload": "Upload a file, file metadata, primary file, file access conditions, remove",
    "workspace.license": "Grant the deposit license",
    "workspace.access": "Item-level access conditions (access section)",
    "workspace.cclicense": "Creative Commons license URL lookup and section",
    "workspace.import_file": "Create a submission from a BibTeX file",
    "workspace.deposit": "submit(): full submission and deposit, validation errors reported",
    # Authorization checks
    "authz.check": "can / features_on / check / require for the current user and another eperson",
    "authz.batch": "features_on_many: batch check via search/objects (or per-object fallback)",
    "authz.features": "List authorization features and features per resource type (admin)",
    # Versioning
    "versions.create": "Create a new item version, deposit it, list the history",
    "versions.draft": "Draft version detection and discard_draft()",
    # Community / collection extras
    "containers.logo": "Upload, replace and delete community and collection logos",
    "containers.hierarchy": "Parent community, ancestors, community tree, *Authorized searches",
    "collections.item_template": "Create, update and delete a collection item template",
    "collections.license": "Collection deposit license: read, set custom, reset to default",
    "collections.mapping": "Map / unmap items into another collection, list mapped items",
    "collections.harvester": "Harvest settings, metadata formats, disable harvesting",
    "collections.default_access": "Default access conditions (DEFAULT_BITSTREAM_READ) of a collection",
    # Discovery
    "discovery.search": "Discovery search finds a newly created item",
    "discovery.config": "Search configuration: filters, operators, sort options",
    "discovery.filters": "Filtered, paged search (f.<filter>=value,operator) and count",
    "discovery.facets": "Facets of a query and all values of one facet (paged)",
    # Browse
    "browse.indexes": "Browse indexes, single index, index by metadata field",
    "browse.entries": "Metadata browse entries and their items, flat browse",
    # Vocabularies
    "vocabulary.lookup": "List vocabularies, entries, exact lookup and value validation",
    "vocabulary.form_field": "Vocabulary configured for a submission form field",
    "vocabulary.hierarchy": "Hierarchical vocabulary: top entries, children, parent path, tree walk",
}


def area(feature_id: str) -> str:
    return feature_id.split(".", 1)[0]
