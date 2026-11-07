"""
Tests for the Site object / Terms of Use (site.py)
"""

import json

import pytest
import responses

from dspace_api.site import SiteManager

SITE = "5175de65-0000-0000-0000-000000000001"


@pytest.fixture
def mgr(http_auth):
    return SiteManager(http_auth)


@pytest.fixture
def site(http, api_url):
    def register(rights):
        body = {"_embedded": {"sites": [{"uuid": SITE, "metadata": {"dc.rights": rights} if rights is not None else {}}]}}
        http.add(responses.GET, api_url("/core/sites"), json=body)
    return register


def last(http):
    return http.calls[-1].request


class TestSite:
    def test_get_site(self, mgr, http, api_url, site):
        site([])
        assert mgr.get_site()["uuid"] == SITE
        assert last(http).params == {"projection": "allLanguages"}
        mgr.get_site(all_languages=False)
        assert last(http).params == {}

    def test_no_site(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/core/sites"), json={"_embedded": {"sites": []}})
        assert mgr.get_site() is None
        assert mgr.get_terms_of_use() is None
        with pytest.raises(ValueError, match="No Site"):
            mgr.set_terms_of_use("x")


class TestTermsOfUse:
    RIGHTS = [{"value": "  ", "language": "de"}, {"value": "Nutzungsbedingungen ", "language": "de"},
              {"value": "Terms", "language": "en"}, {"value": "Conditions", "language": "fr"}]

    def test_language_then_fallback_then_first(self, mgr, site):
        site(self.RIGHTS)
        assert mgr.get_terms_of_use("de") == "Nutzungsbedingungen"
        assert mgr.get_terms_of_use("it") == "Terms"
        assert mgr.get_terms_of_use("it", fallback_language=None) == "Nutzungsbedingungen"
        assert mgr.get_terms_of_use("it", fallback_language="es") == "Nutzungsbedingungen"

    def test_none_configured(self, mgr, site):
        site(None)
        assert mgr.get_terms_of_use() is None
        assert mgr.get_terms_of_use_values() == []

    def test_set_replaces_existing_language(self, mgr, http, api_url, site):
        site(self.RIGHTS)
        http.add(responses.PATCH, api_url(f"/core/sites/{SITE}"), json={"uuid": SITE})
        mgr.set_terms_of_use("New terms", "en")
        assert json.loads(last(http).body) == [{"op": "replace", "path": "/metadata/dc.rights/2",
                                                "value": {"value": "New terms", "language": "en"}}]

    def test_set_appends_new_language(self, mgr, http, api_url, site):
        site(self.RIGHTS)
        http.add(responses.PATCH, api_url(f"/core/sites/{SITE}"), json={"uuid": SITE})
        mgr.set_terms_of_use("Termini", "it")
        assert json.loads(last(http).body)[0]["path"] == "/metadata/dc.rights/-"

    def test_set_first_value(self, mgr, http, api_url, site):
        site(None)
        http.add(responses.PATCH, api_url(f"/core/sites/{SITE}"), json={"uuid": SITE})
        mgr.set_terms_of_use("Terms", None)
        assert json.loads(last(http).body) == [{"op": "add", "path": "/metadata/dc.rights",
                                                "value": [{"value": "Terms", "language": None}]}]
