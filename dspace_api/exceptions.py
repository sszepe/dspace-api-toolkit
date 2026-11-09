# -*- coding: UTF-8 -*-
"""
Custom exceptions for DSpace API client
"""

class DSpaceError(Exception):
    """Base exception class for DSpace API errors"""

    def __init__(self, message, status_code=None, response_text=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

class AuthenticationError(DSpaceError):
    """Exception raised for authentication-related errors"""
    pass

class AuthorizationError(DSpaceError):
    """Exception raised for authorization/permission errors"""
    pass

class UserAgreementRequired(AuthorizationError):
    """
    DSpace-CRIS refused the request because the logged-in user has not
    accepted the end-user agreement (403 "No user agreement accepted").

    Accept it once with client.epersons.accept_end_user_agreement(...),
    mark service accounts with ignore_end_user_agreement(...), or set
    DSpaceConfig(accept_user_agreement=True) / DSPACE_ACCEPT_USER_AGREEMENT=true
    to let the toolkit accept it for the logged-in user automatically.
    """
    pass

class OnBehalfOfError(DSpaceError):
    """
    A request sent with X-On-Behalf-Of ("login as") was refused. DSpace
    allows it only when webui.user.assumelogin = true, for an administrator's
    login, and not for another administrator as target (DSpace-CRIS: unless
    webui.user.assumelogin.admin = true).
    """
    pass

class NotFoundError(DSpaceError):
    """Exception raised when a resource is not found"""
    pass

class BadRequestError(DSpaceError):
    """Exception raised for bad request errors (400)"""
    pass

class ValidationError(DSpaceError):
    """Exception raised for validation errors"""
    pass

class ServerError(DSpaceError):
    """Exception raised for server errors (5xx)"""
    pass

class SessionError(DSpaceError):
    """Exception raised for session-related errors"""
    pass

def handle_response_error(response, operation_name="API operation"):
    """
    Handle HTTP response errors and raise appropriate exceptions

    Parameters:
        response: requests.Response object
        operation_name: Description of the operation for error context

    Raises:
        Appropriate DSpaceError subclass based on status code
    """
    status_code = response.status_code
    acting_as = getattr(response, "dspace_on_behalf_of", None)
    acting_as = acting_as if isinstance(acting_as, str) else None     # set by DSpaceSession only
    text = response.text if isinstance(getattr(response, "text", None), str) else ""

    # A 403 inside an on_behalf_of() block is usually the point of the
    # exercise (the other user may not do this) and stays an AuthorizationError.
    if acting_as and (status_code == 401 or (status_code == 400 and "Authentication request is invalid" in text)):
        reasons = {
            400: "the server has 'login as' switched off (webui.user.assumelogin), the UUID is not an "
                 "eperson, or the eperson is an administrator",
            401: "X-On-Behalf-Of needs a logged-in administrator",
        }
        raise OnBehalfOfError(
            f"{operation_name} failed: 'login as' {acting_as} refused - {reasons[status_code]}",
            status_code=status_code,
            response_text=response.text
        )
    if status_code == 400:
        raise BadRequestError(
            f"{operation_name} failed: Bad Request",
            status_code=status_code,
            response_text=response.text
        )
    elif status_code == 401:
        raise AuthenticationError(
            f"{operation_name} failed: Unauthorized",
            status_code=status_code,
            response_text=response.text
        )
    elif status_code == 403 and "No user agreement accepted" in (response.text or ""):
        raise UserAgreementRequired(
            f"{operation_name} failed: Forbidden - the logged-in user has not accepted the end-user "
            "agreement (DSpace-CRIS). Accept it, or set accept_user_agreement=True / "
            "DSPACE_ACCEPT_USER_AGREEMENT=true",
            status_code=status_code,
            response_text=response.text
        )
    elif status_code == 403:
        raise AuthorizationError(
            f"{operation_name} failed: Forbidden",
            status_code=status_code,
            response_text=response.text
        )
    elif status_code == 404:
        raise NotFoundError(
            f"{operation_name} failed: Not Found",
            status_code=status_code,
            response_text=response.text
        )
    elif 500 <= status_code < 600:
        raise ServerError(
            f"{operation_name} failed: Server Error",
            status_code=status_code,
            response_text=response.text
        )
    else:
        raise DSpaceError(
            f"{operation_name} failed: Unexpected status {status_code}",
            status_code=status_code,
            response_text=response.text
        )
