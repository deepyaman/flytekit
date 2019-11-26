from __future__ import absolute_import

import base64 as _base64
import logging as _logging

import requests as _requests

from flytekit.common.exceptions.base import FlyteException as _FlyteException
from flytekit.configuration.creds import (
    CLIENT_CREDENTIALS_SECRET_LOCATION as _CREDENTIALS_SECRET_FILE,
    CLIENT_CREDENTIALS_SECRET as _CREDENTIALS_SECRET,
)

_utf_8 = 'utf-8'


class FlyteAuthenticationException(_FlyteException):
    _ERROR_CODE = "FlyteAuthenticationFailed"


def get_file_contents(location):
    """
    This reads an input file, and returns the string contents, and should be used for reading credentials.
    This function will also strip newlines.

    :param Text location: The file path holding the client id or secret
    :rtype: Text
    """
    with open(location, 'r') as f:
        return f.read().replace('\n', '')


def get_secret():
    """
    This function will either read in the password from the file path given by the CLIENT_CREDENTIALS_SECRET_LOCATION
    config object, or from the environment variable using the CLIENT_CREDENTIALS_SECRET config object.
    :rtype: Text
    """
    if _CREDENTIALS_SECRET_FILE.get():
        return get_file_contents(_CREDENTIALS_SECRET_FILE.get())
    elif _CREDENTIALS_SECRET.get():
        return _CREDENTIALS_SECRET.get()
    raise FlyteAuthenticationException('No secret could be found in either {} or the {} env variable'.format(
        _CREDENTIALS_SECRET_FILE.get(), _CREDENTIALS_SECRET.env_var))


def get_basic_authorization_header(client_id, client_secret):
    """
    This function transforms the client id and the client secret into a header that conforms with http basic auth.
    It joins the id and the secret with a : then base64 encodes it, then adds the appropriate text.
    :param Text client_id:
    :param Text client_secret:
    :rtype: Text
    """
    concated = "{}:{}".format(client_id, client_secret)
    return "Basic {}".format(str(_base64.b64encode(concated.encode(_utf_8)), _utf_8))


def get_token(token_endpoint, authorization_header, scope):
    """
    :param token_endpoint:
    :param authorization_header:
    :param scope:
    :rtype: (Text,Int) The first element is the access token retrieved from the IDP, the second is the expiration
            in seconds
    """
    headers = {
        'Authorization': authorization_header,
        'Cache-Control': 'no-cache',
        'Accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    body = {
        'grant_type': 'client_credentials',
        'scope': scope,
    }
    response = _requests.post(token_endpoint, data=body, headers=headers)
    if response.status_code != 200:
        _logging.error("Non-200 ({}) received from IDP: {}".format(response.status_code, response.text))
        raise FlyteAuthenticationException('Non-200 received from IDP')

    response = response.json()
    return response['access_token'], response['expires_in']
