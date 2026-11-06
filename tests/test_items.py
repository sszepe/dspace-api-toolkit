import json
import pytest
from unittest.mock import Mock, call
from dspace_api.items import ItemManager
from dspace_api.exceptions import (
    BadRequestError,
    NotFoundError,
    DSpaceError,
    ServerError,
)

class TestItemManager:
    """Test cases for ItemManager"""

    def test_init(self, mock_auth):
        """Test ItemManager initialization"""
        manager = ItemManager(mock_auth)

        assert manager.auth == mock_auth
        assert manager.session == mock_auth.session
        assert manager.config == mock_auth.config

    def test_get_items_success(self, mock_auth):
        """Test get_items success path"""
        mgr = ItemManager(mock_auth)
        expected = {"_embedded": {"items": []}, "page": {"number": 0}}
        mgr.session.get = Mock(return_value=Mock(status_code=200, json=Mock(return_value=expected)))

        result = mgr.get_items(page=1, size=5, sort="dc.title,asc")

        mgr.session.get.assert_called_once()
        # verify params are passed through
        _, kwargs = mgr.session.get.call_args
        assert kwargs["params"] == {"page": 1, "size": 5, "sort": "dc.title,asc"}
        assert result == expected

    def test_get_items_error_400_raises_bad_request(self, mock_auth):
        """Test get_items error path"""
        mgr = ItemManager(mock_auth)
        mgr.session.get = Mock(return_value=Mock(status_code=400, text="nope"))

        with pytest.raises(BadRequestError):
            mgr.get_items(page=0, size=20)

    def test_get_item_success(self, mock_auth):
        """Test get_item success path"""
        mgr = ItemManager(mock_auth)
        payload = {"uuid": "item-uuid", "name": "An Item"}
        mgr.session.get = Mock(return_value=Mock(status_code=200, json=Mock(return_value=payload)))

        result = mgr.get_item("item-uuid")

        mgr.session.get.assert_called_once()
        assert result == payload

    def test_get_item_404_raises_not_found(self, mock_auth):
        """Test get_item 404 error path"""
        mgr = ItemManager(mock_auth)
        mgr.session.get = Mock(return_value=Mock(status_code=404, text="missing"))

        with pytest.raises(NotFoundError):
            mgr.get_item("missing-uuid")

    def test_create_item_adds_entity_type_metadata_when_missing(self, mock_auth):
        """Test create_item adds entity type metadata when missing"""
        mgr = ItemManager(mock_auth)
        # 201 success path
        created = {"uuid": "new-item", "metadata": {}}
        mgr.session.post = Mock(return_value=Mock(status_code=201, json=Mock(return_value=created)))

        res = mgr.create_item(
            collection_uuid="col-123",
            metadata={"dc.title": "Hello"},
            entity_type="Publication",
            in_archive=True,
            discoverable=True,
            withdrawn=False,
        )

        # Verify correct URL and payload
        (url,), kwargs = mgr.session.post.call_args
        assert url.endswith("/core/items?owningCollection=col-123")

        sent = json.loads(kwargs["data"])
        assert sent["type"] == "item"
        assert sent["entityType"] == "Publication"
        # entity type should be injected into metadata if missing
        assert sent["metadata"]["dspace.entity.type"] == [{"value": "Publication"}]
        assert sent["metadata"]["dc.title"] == [{"value": "Hello"}]

        assert res == created

    def test_create_item_errors_raise(self, mock_auth):
        """Test create_item error handling"""
        mgr = ItemManager(mock_auth)
        # Force non-201 to hit error handler
        mgr.session.post = Mock(return_value=Mock(status_code=500, text="boom"))
        with pytest.raises(DSpaceError):
            mgr.create_item("col-123", {"dc.title": "X"}, "Publication")


    def test_update_item_builds_patch_ops_and_succeeds(self, mock_auth):
        """Test update_item success path"""
        mgr = ItemManager(mock_auth)
        updated = {"uuid": "item-1"}
        mgr.session.patch = Mock(return_value=Mock(status_code=200, json=Mock(return_value=updated)))

        res = mgr.update_item("item-1", "replace", {"dc.title": "New Title", "dc.subject": ["A", "B"]})

        # Verify patch document
        (url,), kwargs = mgr.session.patch.call_args
        assert url.endswith("/core/items/item-1")

        ops = kwargs["json"]

        # Check that both replace ops are present by path
        assert any(o["op"] == "replace" and o["path"] == "/metadata/dc.title" for o in ops)
        assert any(o["op"] == "replace" and o["path"] == "/metadata/dc.subject" for o in ops)

        # Validate normalized values
        title_op = next(o for o in ops if o["path"] == "/metadata/dc.title")
        assert title_op["value"] == [{"value": "New Title"}]

        subj_op = next(o for o in ops if o["path"] == "/metadata/dc.subject")
        assert subj_op["value"] == [{"value": "A"}, {"value": "B"}]

        assert res == updated

    def test_update_item_non_200_raises(self, mock_auth):
        """Test update_item error handling"""
        mgr = ItemManager(mock_auth)
        mgr.session.patch = Mock(return_value=Mock(status_code=400, text="bad"))
        with pytest.raises(BadRequestError):
            mgr.update_item("item-1", "replace", {"dc.title": "X"})

    def test_delete_item_success_returns_true(self, mock_auth):
        """Test delete_item success path"""
        mgr = ItemManager(mock_auth)
        mgr.session.delete = Mock(return_value=Mock(status_code=204))

        assert mgr.delete_item("dead-beef") is True
        mgr.session.delete.assert_called_once()

    def test_delete_item_non_204_raises(self, mock_auth):
        """Test delete_item error handling"""
        mgr = ItemManager(mock_auth)
        mgr.session.delete = Mock(return_value=Mock(status_code=500, text="oops"))
        with pytest.raises(DSpaceError):
            mgr.delete_item("dead-beef")

    def test_change_owning_collection_success_and_url_params(self, mock_auth):
        """Test change_owning_collection success path"""
        mgr = ItemManager(mock_auth)
        mgr.session.put = Mock(return_value=Mock(status_code=204))

        # no inheritPolicies
        assert mgr.change_owning_collection("item-1", "col-2", inherit_policies=False) is True
        # with inheritPolicies=true
        assert mgr.change_owning_collection("item-1", "col-2", inherit_policies=True) is True

        # Verify both calls and URL difference
        first_call, second_call = mgr.session.put.call_args_list
        url1 = first_call[0][0]
        url2 = second_call[0][0]
        assert url1.endswith("/core/items/item-1/owningCollection")
        assert url2.endswith("/core/items/item-1/owningCollection?inheritPolicies=true")

        # header is text/uri-list and data is the target collection URI
        for ca in mgr.session.put.call_args_list:
            _, kwargs = ca
            assert kwargs["headers"] == {"Content-Type": "text/uri-list"}
            assert "/core/collections/col-2" in kwargs["data"]

    def test_change_owning_collection_error(self, mock_auth):
        """Test change_owning_collection error handling"""
        mgr = ItemManager(mock_auth)
        mgr.session.put = Mock(return_value=Mock(status_code=400, text="bad"))
        with pytest.raises(BadRequestError):
            mgr.change_owning_collection("item-1", "col-2")

    def test_add_mapped_collection_success(self, mock_auth):
        """Test add_mapped_collection success path"""
        mgr = ItemManager(mock_auth)
        mgr.session.post = Mock(return_value=Mock(status_code=204))

        assert mgr.add_mapped_collection("item-1", "col-3") is True
        (url,), kwargs = mgr.session.post.call_args
        assert url.endswith("/core/items/item-1/mappedCollections")
        assert kwargs["headers"] == {"Content-Type": "text/uri-list"}
        assert "/core/collections/col-3" in kwargs["data"]

    def test_add_mapped_collection_error(self, mock_auth):
        """Test add_mapped_collection error handling"""
        mgr = ItemManager(mock_auth)
        mgr.session.post = Mock(return_value=Mock(status_code=500, text="boom"))
        with pytest.raises(DSpaceError):
            mgr.add_mapped_collection("item-1", "col-3")

    def test_remove_mapped_collection_success(self, mock_auth):
        """Test remove_mapped_collection success path"""
        mgr = ItemManager(mock_auth)
        mgr.session.delete = Mock(return_value=Mock(status_code=204))

        assert mgr.remove_mapped_collection("item-1", "col-3") is True
        (url,), kwargs = mgr.session.delete.call_args
        assert url.endswith("/core/items/item-1/mappedCollections/col-3")

    def test_remove_mapped_collection_error(self, mock_auth):
        """Test remove_mapped_collection error handling"""
        mgr = ItemManager(mock_auth)
        mgr.session.delete = Mock(return_value=Mock(status_code=400, text="bad"))
        with pytest.raises(BadRequestError):
            mgr.remove_mapped_collection("item-1", "col-3")

    def test_format_metadata_field_handles_string_list_and_dict(self, mock_auth):
        """Test _format_metadata_field handles string, list, and dict inputs"""
        mgr = ItemManager(mock_auth)

        # string -> [{"value": "..."}]
        assert mgr._format_metadata_field("X") == [{"value": "X"}]

        # list of strings -> [{"value": "A"}, {"value": "B"}]
        assert mgr._format_metadata_field(["A", "B"]) == [{"value": "A"}, {"value": "B"}]

        # list of dicts -> unchanged
        dict_list = [{"value": "A", "language": "en"}]
        assert mgr._format_metadata_field(dict_list) == dict_list

        # mixed/other -> stringified values
        assert mgr._format_metadata_field(123) == [{"value": "123"}]
        assert mgr._format_metadata_field([1, {"value": "v"}]) == [{"value": "1"}, {"value": "v"}]

    def test_format_metadata_builds_dict_of_lists(self, mock_auth):
        """Test _format_metadata builds dict of lists"""
        mgr = ItemManager(mock_auth)
        md = mgr._format_metadata({"dc.title": "T", "dc.subject": ["A", "B"]})
        assert md == {
            "dc.title": [{"value": "T"}],
            "dc.subject": [{"value": "A"}, {"value": "B"}],
        }


    def test_update_item_uses_given_operation(self, mock_auth):
        """Test update_item passes the requested op (add/replace/remove) into every patch op"""
        mgr = ItemManager(mock_auth)
        mgr.session.patch = Mock(return_value=Mock(status_code=200, json=Mock(return_value={})))
        mgr.update_item("item-1", "add", {"dc.subject": "New", "dc.rights": "CC"})
        ops = mgr.session.patch.call_args.kwargs["json"]
        assert {o["op"] for o in ops} == {"add"}
        assert [o["path"] for o in ops] == ["/metadata/dc.subject", "/metadata/dc.rights"]

    def test_create_item_keeps_explicit_entity_type_metadata(self, mock_auth):
        """Test create_item does not overwrite a dspace.entity.type given in metadata"""
        mgr = ItemManager(mock_auth)
        mgr.session.post = Mock(return_value=Mock(status_code=201, json=Mock(return_value={"uuid": "x"})))
        mgr.create_item("col-1", {"dc.title": "T", "dspace.entity.type": "Person"}, "Publication")
        sent = json.loads(mgr.session.post.call_args.kwargs["data"])
        assert sent["metadata"]["dspace.entity.type"] == [{"value": "Person"}]
        assert sent["entityType"] == "Publication"


class TestItemUpsert:
    """upsert_metadata / upsert_metadata_by_position and the 200-branch of change_owning_collection"""

    ITEM = {"uuid": "item-1", "metadata": {
        "dc.title": [{"value": "Old title", "place": 0}],
        "dc.subject": [{"value": "A", "place": 0}, {"value": "B", "place": 1}],
    }}

    @staticmethod
    def _mgr(mock_auth, item=None, patch_status=200, patch_json=None):
        mgr = ItemManager(mock_auth)
        mgr.session.get = Mock(return_value=Mock(status_code=200, json=Mock(return_value=item)))
        mgr.session.patch = Mock(return_value=Mock(status_code=patch_status, text="err",
                                                   json=Mock(return_value=patch_json or {"uuid": "item-1"})))
        return mgr

    @staticmethod
    def _ops(mgr):
        return mgr.session.patch.call_args.kwargs["json"]

    # --- upsert_metadata --------------------------------------------------

    def test_upsert_fetches_item_and_picks_op_per_field(self, mock_auth):
        mgr = self._mgr(mock_auth, item=self.ITEM)
        out = mgr.upsert_metadata("item-1", {"dc.title": "New title", "dc.rights": ["CC BY", "CC0"]})

        mgr.session.get.assert_called_once()
        assert out == {"uuid": "item-1"}
        (url,), _ = mgr.session.patch.call_args
        assert url.endswith("/core/items/item-1")
        assert self._ops(mgr) == [
            {"op": "replace", "path": "/metadata/dc.title", "value": [{"value": "New title"}]},
            {"op": "add", "path": "/metadata/dc.rights", "value": [{"value": "CC BY"}, {"value": "CC0"}]},
        ]

    def test_upsert_with_existing_metadata_skips_get(self, mock_auth):
        mgr = self._mgr(mock_auth)
        mgr.upsert_metadata("item-1", {"dc.title": "T"}, existing_metadata={"dc.title": [{"value": "x"}]})
        mgr.session.get.assert_not_called()
        assert self._ops(mgr)[0]["op"] == "replace"

    def test_upsert_treats_empty_field_as_new(self, mock_auth):
        mgr = self._mgr(mock_auth)
        mgr.upsert_metadata("item-1", {"dc.title": "T"}, existing_metadata={"dc.title": []})
        assert self._ops(mgr)[0]["op"] == "add"

    @pytest.mark.parametrize("item", [{"uuid": "item-1"}, {"uuid": "item-1", "metadata": None}])
    def test_upsert_item_without_metadata(self, mock_auth, item):
        mgr = self._mgr(mock_auth, item=item)
        mgr.upsert_metadata("item-1", {"dc.title": "T"})
        assert self._ops(mgr)[0]["op"] == "add"

    def test_upsert_keeps_preformatted_authority_values(self, mock_auth):
        value = {"value": "Project X", "authority": "proj-uuid", "confidence": 600}
        mgr = self._mgr(mock_auth)
        mgr.upsert_metadata("item-1", {"dc.relation.project": [value]}, existing_metadata={})
        assert self._ops(mgr)[0]["value"] == [value]

    def test_upsert_error_raises(self, mock_auth):
        mgr = self._mgr(mock_auth, patch_status=422)
        with pytest.raises(DSpaceError):
            mgr.upsert_metadata("item-1", {"dc.title": "T"}, existing_metadata={})

    def test_upsert_propagates_get_errors(self, mock_auth):
        mgr = ItemManager(mock_auth)
        mgr.session.get = Mock(return_value=Mock(status_code=404, text="missing"))
        mgr.session.patch = Mock()
        with pytest.raises(NotFoundError):
            mgr.upsert_metadata("item-1", {"dc.title": "T"})
        mgr.session.patch.assert_not_called()

    # --- upsert_metadata_by_position ---------------------------------------

    def test_by_position_replace_append_and_new_field(self, mock_auth):
        mgr = self._mgr(mock_auth, item=self.ITEM)
        replace = {"value": "B2", "place": 1}
        append = {"value": "C", "place": 2}
        new = {"value": "Proj", "authority": "p-1", "confidence": 600, "place": 0}

        out = mgr.upsert_metadata_by_position("item-1", {
            "dc.title": {"value": "New", "place": 0},
            "dc.subject": replace,
            "dc.relation.project": new,
        })
        assert out == {"uuid": "item-1"}
        assert self._ops(mgr) == [
            {"op": "replace", "path": "/metadata/dc.title/0", "value": {"value": "New", "place": 0}},
            {"op": "replace", "path": "/metadata/dc.subject/1", "value": replace},
            {"op": "add", "path": "/metadata/dc.relation.project", "value": new},
        ]

        mgr.upsert_metadata_by_position("item-1", {"dc.subject": append})
        assert self._ops(mgr) == [{"op": "add", "path": "/metadata/dc.subject/-", "value": append}]

    @pytest.mark.parametrize("place", [None, "1", 1.0])
    def test_by_position_requires_integer_place(self, mock_auth, place):
        mgr = self._mgr(mock_auth, item=self.ITEM)
        value = {"value": "x"} if place is None else {"value": "x", "place": place}
        with pytest.raises(TypeError, match="place-value has to be a valid integer"):
            mgr.upsert_metadata_by_position("item-1", {"dc.title": value})
        mgr.session.patch.assert_not_called()

    def test_by_position_gap_in_existing_field(self, mock_auth):
        mgr = self._mgr(mock_auth, item=self.ITEM)
        with pytest.raises(IndexError, match="next_valid_place=2"):
            mgr.upsert_metadata_by_position("item-1", {"dc.subject": {"value": "x", "place": 3}})
        mgr.session.patch.assert_not_called()

    def test_by_position_new_field_must_start_at_zero(self, mock_auth):
        mgr = self._mgr(mock_auth, item={"uuid": "item-1", "metadata": None})
        with pytest.raises(IndexError, match="has to be 0"):
            mgr.upsert_metadata_by_position("item-1", {"dc.rights": {"value": "x", "place": 1}})
        mgr.session.patch.assert_not_called()

    def test_by_position_validates_all_fields_before_patching(self, mock_auth):
        mgr = self._mgr(mock_auth, item=self.ITEM)
        with pytest.raises(IndexError):
            mgr.upsert_metadata_by_position("item-1", {
                "dc.title": {"value": "ok", "place": 0},
                "dc.rights": {"value": "bad", "place": 5},
            })
        mgr.session.patch.assert_not_called()

    def test_by_position_error_raises(self, mock_auth):
        mgr = self._mgr(mock_auth, item=self.ITEM, patch_status=500)
        with pytest.raises(ServerError):
            mgr.upsert_metadata_by_position("item-1", {"dc.title": {"value": "x", "place": 0}})

    # --- change_owning_collection ------------------------------------------

    def test_change_owning_collection_accepts_200(self, mock_auth):
        mgr = ItemManager(mock_auth)
        mgr.session.put = Mock(return_value=Mock(status_code=200))
        assert mgr.change_owning_collection("item-1", "col-2") is True

    def test_create_item_without_entity_type(self, mock_auth):
        """entity_type=None creates a plain item: no entityType, no dspace.entity.type"""
        mgr = ItemManager(mock_auth)
        mgr.session.post = Mock(return_value=Mock(status_code=201, json=Mock(return_value={"uuid": "x"})))
        mgr.create_item("col-1", {"dc.title": "T"}, None)
        sent = json.loads(mgr.session.post.call_args.kwargs["data"])
        assert "entityType" not in sent and "dspace.entity.type" not in sent["metadata"]
