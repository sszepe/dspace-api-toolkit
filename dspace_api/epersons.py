# -*- coding: UTF-8 -*-
"""
EPerson (user account) management for DSpace API (DSpace 7+ / DSpace-CRIS 7+)

EPersons
    GET    /eperson/epersons                            list (admin)
    POST   /eperson/epersons                            create (admin)
    POST   /eperson/epersons?token=                     create from a registration token
    POST   /eperson/epersons/{uuid}?token=&override=    merge registration data
    GET    /eperson/epersons/{uuid}
    PATCH  /eperson/epersons/{uuid}                     metadata, /canLogin, /certificate,
                                                        /email, /netid, /password
    DELETE /eperson/epersons/{uuid}
    GET    /eperson/epersons/{uuid}/groups              direct group memberships
    GET    /eperson/epersons/search/byEmail?email=
    GET    /eperson/epersons/search/byMetadata?query=
    GET    /eperson/epersons/search/isNotMemberOf?group=&query=

Current user
    GET    /authn/status?embed=eperson

Registrations (self-registration / forgot password)
    POST   /eperson/registrations?accountRequestType=register|forgot
    GET    /eperson/registrations/search/findByToken?token=
    PATCH  /eperson/registrations/{id}?token=

End-user agreement (terms of use acceptance)
    stored as eperson metadata "dspace.agreements.end-user" = "true"
    (the terms text itself lives on the Site object, see site.py)
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Union

from ._base import BaseManager
from .projection import EmbedArg, ProjectionArg
from .config import Endpoints
from .exceptions import NotFoundError

# Logger for the module
logger = logging.getLogger(__name__)

# Constants for epersons, registrations, and authentication endpoints
EPERSONS = getattr(Endpoints, "EPERSONS", "/eperson/epersons")
REGISTRATIONS = getattr(Endpoints, "REGISTRATIONS", "/eperson/registrations")
AUTHN_STATUS = getattr(Endpoints, "AUTHN_STATUS", "/authn/status")

# Constants for end-user agreement metadata keys and request types
END_USER_AGREEMENT = "dspace.agreements.end-user"
# DSpace-CRIS: an eperson with this flag never has to accept the agreement (service accounts)
IGNORE_END_USER_AGREEMENT = "dspace.agreements.ignore"
REQUEST_TYPES = ("register", "forgot")

# Type alias for references to epersons (either UUID string or full dictionary)
Ref = Union[str, Dict[str, Any]]


def _bool_str(value: bool) -> str:
    """Convert a boolean value to a string suitable for DSpace PATCH operations."""
    # DSpace's PATCH operations for these flags take "true"/"false" strings
    return "true" if value else "false"


class EPersonManager(BaseManager):
    """Manages DSpace epersons, the current user, registrations and agreements"""

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_eperson(self, eperson: Ref, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Dict[str, Any]:
        """Get an eperson by UUID or reference.

        Parameters:
            eperson (Ref): UUID string or full eperson dictionary.
            all_languages (Optional[bool], optional): Whether to include all languages. Defaults to None.
            projection (ProjectionArg, optional): Projection for the response. Defaults to None.
            embed (EmbedArg, optional): Embedded resources for the response. Defaults to None.

        Returns:
            Dict[str, Any]: The eperson dictionary.
        """
        uuid = self.resolve_eperson(eperson)
        return self._get_json(self._url(EPERSONS, f"/{uuid}"), f"Get eperson {uuid}", all_languages=all_languages, projection=projection, embed=embed)

    def iter_epersons(self, page_size: Optional[int] = None, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None):
        """Every account in the repository (admin only). There may be a lot.

        Parameters:
            page_size (Optional[int], optional): Number of results per page. Defaults to None.
            all_languages (Optional[bool], optional): Whether to include all languages. Defaults to None.
            projection (ProjectionArg, optional): Projection for the response. Defaults to None.
            embed (EmbedArg, optional): Embedded resources for the response. Defaults to None.

        Returns:
            Iterator[Dict[str, Any]]: An iterator over eperson dictionaries.
        """
        return self._iter_embedded(self._url(EPERSONS), "epersons", "List epersons", page_size=page_size,
                                   all_languages=all_languages, projection=projection, embed=embed)

    def find_by_email(self, email: str, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Optional[Dict[str, Any]]:
        """The eperson with this email, or None (204).
        
        Parameters:
            email (str): Email address of the eperson.
            all_languages (Optional[bool], optional): Whether to include all languages. Defaults to None.
            projection (ProjectionArg, optional): Projection for the response. Defaults to None.
            embed (EmbedArg, optional): Embedded resources for the response. Defaults to None.

        Returns:
            Optional[Dict[str, Any]]: The eperson dictionary if found, otherwise None.
        """
        return self._get_optional(self._url(EPERSONS, "/search/byEmail"), "Find eperson by email",
                                  params={"email": email}, all_languages=all_languages, projection=projection, embed=embed)

    def search_epersons(self, query: str, max_results: Optional[int] = None,
                        all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Search first name, last name, email and exact UUID (byMetadata).
        
        Parameters:
            query (str): Search query string.
            max_results (Optional[int], optional): Maximum number of results to return. Defaults to None.
            all_languages (Optional[bool], optional): Whether to include all languages. Defaults to None.
            projection (ProjectionArg, optional): Projection for the response. Defaults to None.
            embed (EmbedArg, optional): Embedded resources for the response. Defaults to None.

        Returns:
            List[Dict[str, Any]]: A list of eperson dictionaries matching the search query.
        """
        url = self._url(EPERSONS, "/search/byMetadata")
        return list(self._iter_embedded(url, "epersons", f"Search epersons '{query}'", [("query", query)],
                                        max_results=max_results, all_languages=all_languages, projection=projection, embed=embed))

    def search_epersons_not_member_of(self, group_uuid: str, query: str = "",
                                      max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        """Epersons that could be added to the group (isNotMemberOf).
        
        Parameters:
            group_uuid (str): UUID of the group.
            query (str, optional): Search query string. Defaults to "".
            max_results (Optional[int], optional): Maximum number of results to return. Defaults to None.

        Returns:
            List[Dict[str, Any]]: A list of eperson dictionaries that are not members of the group.
        """
        url = self._url(EPERSONS, "/search/isNotMemberOf")
        return list(self._iter_embedded(url, "epersons", "Search addable epersons",
                                        [("group", group_uuid), ("query", query)], max_results=max_results))

    def resolve_eperson(self, eperson: Ref) -> str:
        """UUID for an eperson given as UUID, href, dict or email address.
        
        Parameters:
            eperson (Ref): Reference to the eperson (UUID, href, dict or email address).

        Returns:
            str: UUID of the eperson.

        Raises:
            NotFoundError: If the eperson cannot be found by email.
            ValueError: If the eperson reference cannot be resolved.
        """
        uuid = self._uuid_of(eperson)
        if uuid:
            return uuid
        if isinstance(eperson, str) and "@" in eperson:
            found = self.find_by_email(eperson)
            if not found:
                raise NotFoundError(f"No eperson with email {eperson!r}")
            return found["uuid"]
        raise ValueError(f"Cannot resolve eperson reference {eperson!r} (use a UUID, href or email)")

    def get_eperson_groups(self, eperson: Ref, embed_object: bool = True) -> List[Dict[str, Any]]:
        """Groups the eperson is a *direct* member of (linked DSO embedded by default).
        
        Parameters:
            eperson (Ref): Reference to the eperson (UUID, href, dict or email address).
            embed_object (bool, optional): Whether to embed the linked DSO objects. Defaults to True.

        Returns:
            List[Dict[str, Any]]: A list of groups the eperson is a direct member of.

        """
        uuid = self.resolve_eperson(eperson)
        params = [("embed", "object")] if embed_object else None
        return list(self._iter_embedded(self._url(EPERSONS, f"/{uuid}/groups"), "groups",
                                        f"Get groups of eperson {uuid}", params))

    # ------------------------------------------------------------------
    # Current user
    # ------------------------------------------------------------------

    def get_auth_status(self, embed_eperson: bool = True, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Dict[str, Any]:
        """Get the authentication status of the current user.

        /authn/status, e.g. {"authenticated": true, "authenticationMethod": "password", ...}

        Parameters:
            embed_eperson (bool, optional): Whether to embed the eperson object. Defaults to True.
            all_languages (Optional[bool], optional): Whether to include all languages. Defaults to None.
            projection (ProjectionArg, optional): Projection parameter. Defaults to None.
            embed (EmbedArg, optional): Embed parameter. Defaults to None.

        Returns:
            Dict[str, Any]: Authentication status of the current user.
        """
        params = [("embed", "eperson")] if embed_eperson else None
        return self._get_json(self._url(AUTHN_STATUS), "Get authentication status", params,
                              all_languages=all_languages, projection=projection, embed=embed)

    def get_current_user(self, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Optional[Dict[str, Any]]:
        """The logged-in eperson, or None when the session is anonymous.

        Parameters:
            all_languages (Optional[bool], optional): Whether to include all languages. Defaults to None.
            projection (ProjectionArg, optional): Projection parameter. Defaults to None.
            embed (EmbedArg, optional): Embed parameter. Defaults to None.

        Returns:
            Optional[Dict[str, Any]]: The logged-in eperson's data, or None if anonymous.

        """
        status = self.get_auth_status(all_languages=all_languages, projection=projection, embed=embed)
        if not status.get("authenticated"):
            return None
        embedded = (status.get("_embedded") or {}).get("eperson")
        if embedded:
            return embedded
        href = ((status.get("_links") or {}).get("eperson") or {}).get("href")
        return self.get_by_href(href, all_languages=all_languages, projection=projection, embed=embed) if href else None

    # ------------------------------------------------------------------
    # Create / update / delete
    # ------------------------------------------------------------------

    def _person_metadata(self, first_name, last_name, language, metadata) -> Dict[str, Any]:
        """
        Prepare the metadata dictionary for an eperson.

        Parameters:
            first_name (str): First name of the eperson.
            last_name (str): Last name of the eperson.
            language (str): Language of the eperson.
            metadata (Optional[Dict[str, Any]]): Existing metadata to include.

        Returns:
            Dict[str, Any]: Formatted metadata dictionary.

        """
        md = dict(metadata or {})
        for field, value in (("eperson.firstname", first_name), ("eperson.lastname", last_name),
                             ("eperson.language", language)):
            if value and field not in md:
                md[field] = value
        return self._format_metadata(md)

    def create_eperson(self, email: str, first_name: Optional[str] = None, last_name: Optional[str] = None,
                       can_log_in: bool = True, require_certificate: bool = False,
                       net_id: Optional[str] = None, language: Optional[str] = None,
                       metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Create an account (admin only). 422 if the email is missing or taken.
        The account has no password; set one with set_password() or let the
        user use "forgot password" (request_registration(email, "forgot")).

        Parameters:
            email (str): Email address of the eperson.
            first_name (Optional[str], optional): First name of the eperson. Defaults to None.
            last_name (Optional[str], optional): Last name of the eperson. Defaults to None.
            can_log_in (bool, optional): Whether the eperson can log in. Defaults to True.
            require_certificate (bool, optional): Whether the eperson requires a certificate. Defaults to False.
            net_id (Optional[str], optional): Net ID of the eperson. Defaults to None.
            language (Optional[str], optional): Language of the eperson. Defaults to None.
            metadata (Optional[Dict[str, Any]], optional): Additional metadata for the eperson. Defaults to None.

        Returns:
            Dict[str, Any]: The created eperson's data.
        """ 
        body = {
            "name": email,
            "email": email,
            "canLogIn": can_log_in,
            "requireCertificate": require_certificate,
            "selfRegistered": False,
            "metadata": self._person_metadata(first_name, last_name, language, metadata),
            "type": "eperson",
        }
        if net_id:
            body["netid"] = net_id
        person = self._request("POST", self._url(EPERSONS), f"Create eperson {email}", expected=(200, 201),
                               headers=self._headers(), json=body).json()
        logger.info("Created eperson %s (%s)", person.get("uuid"), email)
        return person

    def get_or_create_eperson(self, email: str, **kwargs) -> Dict[str, Any]:
        """
        Get an eperson by email, or create one if it doesn't exist.

        Parameters:
            email (str): Email address of the eperson.
            **kwargs: Additional arguments to pass to `create_eperson`.

        Returns:
            Dict[str, Any]: The eperson's data.
        """
        return self.find_by_email(email) or self.create_eperson(email, **kwargs)

    def create_eperson_from_token(self, token: str, first_name: str, last_name: str,
                                  password: Optional[str] = None, email: Optional[str] = None,
                                  language: Optional[str] = None,
                                  metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Finish a self-registration with the token from the registration email.
        First and last name are mandatory; email (if given) must match the registration.

        Parameters:
            token (str): Registration token from the email.
            first_name (str): First name of the eperson.
            last_name (str): Last name of the eperson.
            password (Optional[str], optional): Password for the eperson. Defaults to None.
            email (Optional[str], optional): Email address of the eperson. Defaults to None.
            language (Optional[str], optional): Language of the eperson. Defaults to None.
            metadata (Optional[Dict[str, Any]], optional): Additional metadata for the eperson. Defaults to None.

        Returns:
            Dict[str, Any]: The created eperson's data.
        """
        body: Dict[str, Any] = {
            "canLogIn": True,
            "requireCertificate": False,
            "metadata": self._person_metadata(first_name, last_name, language, metadata),
            "type": "eperson",
        }
        if password:
            body["password"] = password
        if email:
            body["email"] = email
        return self._request("POST", self._url(EPERSONS), "Create eperson from registration token",
                             expected=(200, 201), headers=self._headers(), params={"token": token},
                             json=body).json()

    def patch_eperson(self, eperson: Ref, operations: List[Dict[str, Any]],
                      params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Patch an eperson with the given operations.

        Parameters:
            eperson (Ref): Reference to the eperson to patch.
            operations (List[Dict[str, Any]]): List of patch operations.
            params (Optional[Dict[str, Any]], optional): Additional query parameters. Defaults to None.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        uuid = self.resolve_eperson(eperson)
        response = self._request("PATCH", self._url(EPERSONS, f"/{uuid}"), f"Patch eperson {uuid}",
                                 headers=self._headers(), json=operations, params=params)
        return response.json()

    def update_eperson_metadata(self, eperson: Ref, operation: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Update eperson metadata.
        
        add / replace / remove eperson metadata (admins: any account; users: their own).
        
        Parameters:
            eperson (Ref): Reference to the eperson to update metadata for.
            operation (str): The metadata operation to perform ("add", "replace", "remove").
            metadata (Dict[str, Any]): The metadata to update.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        return self.patch_eperson(eperson, self._metadata_patch_ops(operation, metadata))

    def upsert_eperson_metadata(self, eperson: Ref, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """replace fields the account already has, add the others.

        Parameters:
            eperson (Ref): Reference to the eperson to update metadata for.
            metadata (Dict[str, Any]): The metadata to upsert.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        current = self.get_eperson(eperson, all_languages=True)
        ops = self._upsert_ops(current.get("metadata") or {}, metadata, self._format_metadata_field)
        return self.patch_eperson(current["uuid"], ops)

    def set_can_log_in(self, eperson: Ref, can_log_in: bool) -> Dict[str, Any]:
        """Enable/disable the account (admin only).
        
        Parameters:
            eperson (Ref): Reference to the eperson to update.
            can_log_in (bool): Whether the account can log in.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        return self.patch_eperson(eperson, [{"op": "replace", "path": "/canLogin", "value": _bool_str(can_log_in)}])

    def set_require_certificate(self, eperson: Ref, required: bool) -> Dict[str, Any]:
        """Set whether the eperson requires a certificate (admin only).

        Parameters:
            eperson (Ref): Reference to the eperson to update.
            required (bool): Whether a certificate is required.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        return self.patch_eperson(eperson, [{"op": "replace", "path": "/certificate", "value": _bool_str(required)}])

    def set_email(self, eperson: Ref, email: str) -> Dict[str, Any]:
        """Set the email address of the eperson (admin only).

        Parameters:
            eperson (Ref): Reference to the eperson to update.
            email (str): The email address to set.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        return self.patch_eperson(eperson, [{"op": "replace", "path": "/email", "value": email}])

    def set_netid(self, eperson: Ref, net_id: str) -> Dict[str, Any]:
        """Set or replace the netid of the eperson (admin only).

        Parameters:
            eperson (Ref): Reference to the eperson to update.
            net_id (str): The netid to set.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        return self.patch_eperson(eperson, [{"op": "replace", "path": "/netid", "value": net_id}])

    def set_password(self, eperson: Ref, new_password: str, current_password: Optional[str] = None,
                     token: Optional[str] = None) -> Dict[str, Any]:
        """
        Change a password. Users changing their own need current_password (403
        if wrong); a password reset uses the registration token instead.
        422 if the password doesn't match the configured rules.
        """
        value = {"new_password": new_password}
        if current_password is not None:
            value["current_password"] = current_password
        return self.patch_eperson(eperson, [{"op": "add", "path": "/password", "value": value}],
                                  params={"token": token} if token else None)

    def delete_eperson(self, eperson: Ref) -> bool:
        uuid = self.resolve_eperson(eperson)
        self._request("DELETE", self._url(EPERSONS, f"/{uuid}"), f"Delete eperson {uuid}", expected=(204,))
        logger.info("Deleted eperson %s", uuid)
        return True

    # ------------------------------------------------------------------
    # Registrations
    # ------------------------------------------------------------------

    def request_registration(self, email: str, request_type: str = "register") -> bool:
        """
        Send a registration ("register") or password reset ("forgot") email.
        Works anonymously; DSpace answers the same either way, so it does not
        reveal whether the address has an account.

        Parameters:
            email (str): The email address to send the registration or password reset email to.
            request_type (str, optional): The type of request, either "register" or "forgot". Defaults to "register".

        Returns:
            bool: True if the request was successfully sent.
        """
        if request_type not in REQUEST_TYPES:
            raise ValueError(f"request_type must be one of {REQUEST_TYPES}")
        self._request("POST", self._url(REGISTRATIONS), f"Request {request_type} for {email}",
                      expected=(200, 201, 204), headers=self._headers(),
                      params={"accountRequestType": request_type}, json={"email": email})
        logger.info("Requested %s email for %s", request_type, email)
        return True

    def find_registration_by_token(self, token: str) -> Dict[str, Any]:
        """Email, linked user and (for ORCID etc.) registration metadata for a token.

        Parameters:
            token (str): The registration token to search for.

        Returns:
            Dict[str, Any]: The registration data associated with the token.
        """
        return self._get_json(self._url(REGISTRATIONS, "/search/findByToken"), "Find registration by token",
                              params={"token": token})

    def update_registration_email(self, registration_id: int, token: str, email: str,
                                  operation: str = "replace") -> bool:
        """Change/add the email of a registration; DSpace renews the token and sends a new mail.
        
        Parameters:
            registration_id (int): The ID of the registration to update.
            token (str): The registration token.
            email (str): The new email address to set.
            operation (str, optional): The operation to perform, either "add" or "replace". Defaults to "replace".

        Returns:
            bool: True if the email was successfully updated.
        """
        if operation not in ("add", "replace"):
            raise ValueError("operation must be 'add' or 'replace'")
        self._request("PATCH", self._url(REGISTRATIONS, f"/{registration_id}"), "Update registration email",
                      expected=(200, 204), headers=self._headers(), params={"token": token},
                      json=[{"op": operation, "path": "/email", "value": [email]}])
        return True

    def merge_registration(self, eperson: Ref, token: str, override: Iterable[str] = ()) -> Dict[str, Any]:
        """Merge registration data (e.g. from ORCID login) into an existing account.
        
        Parameters:
            eperson (Ref): Reference to the eperson to merge registration data into.
            token (str): The registration token.
            override (Iterable[str], optional): Fields to override. Defaults to an empty tuple.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        uuid = self.resolve_eperson(eperson)
        params = {"token": token}
        override = list(override)
        if override:
            params["override"] = ",".join(override)
        response = self._request("POST", self._url(EPERSONS, f"/{uuid}"), f"Merge registration into {uuid}",
                                 expected=(200, 201), headers=self._headers(), params=params)
        return self._json_or_none(response) or {}

    # ------------------------------------------------------------------
    # End-user agreement
    # ------------------------------------------------------------------

    def has_accepted_end_user_agreement(self, eperson: Ref) -> bool:
        """Check if the end-user agreement has been accepted.

        Parameters:
            eperson (Ref): Reference to the eperson to check.

        Returns:
            bool: True if the end-user agreement has been accepted.
        """
        person = eperson if isinstance(eperson, dict) and "metadata" in eperson else self.get_eperson(eperson)
        values = (person.get("metadata") or {}).get(END_USER_AGREEMENT) or []
        return bool(values) and str(values[0].get("value")).lower() == "true"

    def accept_end_user_agreement(self, eperson: Ref) -> Dict[str, Any]:
        """
        Record acceptance. Uses 'replace' when the field already exists
        (a blind 'add' would append a second value), and is a no-op if it's
        already "true". For the logged-in user the login token is refreshed
        too: DSpace-CRIS reads the acceptance from a token claim, so without
        a new token it keeps answering 403 "No user agreement accepted".

        Parameters:
            eperson (Ref): Reference to the eperson to update.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        return self._set_agreement_flag(eperson, END_USER_AGREEMENT, True)

    def revoke_end_user_agreement(self, eperson: Ref) -> Dict[str, Any]:
        """Remove the acceptance, so the user is asked again at next login.

        Parameters:
            eperson (Ref): Reference to the eperson to update.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        return self._set_agreement_flag(eperson, END_USER_AGREEMENT, False)

    def ignore_end_user_agreement(self, eperson: Ref, ignore: bool = True) -> Dict[str, Any]:
        """
        DSpace-CRIS: exempt an account (e.g. an import or harvesting service
        account) from the end-user agreement ("dspace.agreements.ignore").

        Parameters:
            eperson (Ref): Reference to the eperson to update.
            ignore (bool, optional): Whether to ignore the end-user agreement. Defaults to True.
                ignore=False removes the exemption.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        return self._set_agreement_flag(eperson, IGNORE_END_USER_AGREEMENT, ignore)

    def is_ignoring_end_user_agreement(self, eperson: Ref) -> bool:
        """Check if the end-user agreement is being ignored for the given eperson.

        Parameters:
            eperson (Ref): Reference to the eperson to check.

        Returns:
            bool: True if the end-user agreement is being ignored.
        """
        person = eperson if isinstance(eperson, dict) and "metadata" in eperson else self.get_eperson(eperson)
        values = (person.get("metadata") or {}).get(IGNORE_END_USER_AGREEMENT) or []
        return bool(values) and str(values[0].get("value")).lower() == "true"

    def _set_agreement_flag(self, eperson: Ref, field: str, enabled: bool) -> Dict[str, Any]:
        """Set or unset an agreement flag for the given eperson.

        Parameters:
            eperson (Ref): Reference to the eperson to update.
            field (str): The metadata field representing the agreement flag.
            enabled (bool): Whether to enable (True) or disable (False) the flag.

        Returns:
            Dict[str, Any]: The updated eperson's data.
        """
        person = self.get_eperson(eperson, all_languages=True)
        values = (person.get("metadata") or {}).get(field) or []
        if enabled and values and str(values[0].get("value")).lower() == "true":
            return person
        if not enabled and not values:
            return person
        if enabled:
            operation = {"op": "replace" if values else "add", "path": f"/metadata/{field}",
                         "value": [{"value": "true"}]}
        else:
            operation = {"op": "remove", "path": f"/metadata/{field}"}
        updated = self.patch_eperson(person["uuid"], [operation])
        self._refresh_token_if_current(person["uuid"])
        return updated

    def _refresh_token_if_current(self, uuid: str) -> bool:
        """New login token when `uuid` is the logged-in user (agreement flags are token claims).

        Parameters:
            uuid (str): The UUID of the eperson to check against the current logged-in user.

        Returns:
            bool: True if the token was refreshed, False otherwise.
        """
        refresh = getattr(self.auth, "refresh_token", None)
        if not callable(refresh) or getattr(self.auth, "is_authenticated", False) is not True:
            return False
        status = self.get_auth_status(embed_eperson=False)
        href = ((status.get("_links") or {}).get("eperson") or {}).get("href") or ""
        if not href.rstrip("/").endswith(uuid):
            return False
        refresh()
        return True
