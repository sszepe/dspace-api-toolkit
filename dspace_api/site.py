# -*- coding: UTF-8 -*-
"""
Site object and Terms of Use for DSpace API

The repository-wide Terms of Use text is the Site object's dc.rights
metadata (one value per language, may contain Markdown). Whether a user
accepted it is stored on the eperson - see EPersonManager.*_end_user_agreement.

    GET    /core/sites?projection=allLanguages
    PATCH  /core/sites/{uuid}                      metadata (admin)
"""

import logging
from typing import Any, Dict, List, Optional

from ._base import BaseManager
from .config import Endpoints

logger = logging.getLogger(__name__)

SITES = getattr(Endpoints, "SITES", "/core/sites")
TERMS_FIELD = "dc.rights"


class SiteManager(BaseManager):
    """Reads and updates the Site object, in particular the Terms of Use"""

    def get_site(self, all_languages: Optional[bool] = True) -> Optional[Dict[str, Any]]:
        """
        The single Site object. Defaults to all_languages=True (unlike the
        other managers), because the Terms of Use are stored per language;
        pass None to follow the client setting, False to switch it off.

        Parameters:
            all_languages: Whether to retrieve all language versions of the site metadata

        Returns:
            dict: The site object, or None if not found
        """
        data = self._get_json(self._url(SITES), "Get site", all_languages=all_languages)
        sites = data.get("_embedded", {}).get("sites", [])
        return sites[0] if sites else None

    def get_terms_of_use_values(self) -> List[Dict[str, Any]]:
        """
        Get the raw Terms of Use metadata values.

        Returns:
            list: List of metadata values for the Terms of Use
        """
        site = self.get_site() or {}
        return (site.get("metadata") or {}).get(TERMS_FIELD) or []

    def get_terms_of_use(self, language: str = "en", fallback_language: Optional[str] = "en") -> Optional[str]:
        """
        Terms text in `language`, else `fallback_language`, else the first
        non-empty value. None if no terms are configured.

        Parameters:
            language: Preferred language for the terms
            fallback_language: Language to fall back to if the preferred language is not available

        Returns:
            str: Terms of Use text in the requested language, or None if not available
        """
        values = [v for v in self.get_terms_of_use_values() if (v.get("value") or "").strip()]
        for lang in (language, fallback_language):
            match = next((v for v in values if v.get("language") == lang), None) if lang else None
            if match:
                return match["value"].strip()
        return values[0]["value"].strip() if values else None

    def set_terms_of_use(self, text: str, language: Optional[str] = "en") -> Dict[str, Any]:
        """
        Create or replace the terms for one language, leaving other languages alone (admin).

        Parameters:
            text: The terms text to set
            language: Language for the terms

        Returns:
            dict: The updated site object
        """
        site = self.get_site()
        if not site:
            raise ValueError("No Site object found")
        values = (site.get("metadata") or {}).get(TERMS_FIELD) or []
        new_value = {"value": text, "language": language}
        index = next((i for i, v in enumerate(values) if v.get("language") == language), None)
        if index is not None:
            op = {"op": "replace", "path": f"/metadata/{TERMS_FIELD}/{index}", "value": new_value}
        elif values:
            op = {"op": "add", "path": f"/metadata/{TERMS_FIELD}/-", "value": new_value}
        else:
            op = {"op": "add", "path": f"/metadata/{TERMS_FIELD}", "value": [new_value]}
        logger.info("Setting terms of use (%s) via %s", language, op["op"])
        return self._request("PATCH", self._url(SITES, f"/{site['uuid']}"), "Set terms of use",
                             headers=self._headers(), json=[op]).json()
