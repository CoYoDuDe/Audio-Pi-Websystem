import re
from typing import Any, Dict, Mapping, Optional

TOKEN_INPUT_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
TOKEN_META_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)">')


def _extract_csrf_token(html: str) -> str:
    for pattern in (TOKEN_INPUT_RE, TOKEN_META_RE):
        match = pattern.search(html)
        if match:
            return match.group(1)
    raise AssertionError("Kein CSRF-Token im HTML gefunden")


def get_csrf_token(client, source_url: str = "/") -> str:
    response = client.get(source_url, follow_redirects=True)
    html = response.get_data(as_text=True)
    return _extract_csrf_token(html)


def csrf_post(
    client,
    url: str,
    data: Optional[Mapping[str, Any]] = None,
    *,
    follow_redirects: bool = False,
    source_url: str = "/",
    headers: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
):
    token = get_csrf_token(client, source_url=source_url)
    if data is None:
        form_data: Dict[str, Any] = {"csrf_token": token}
    else:
        if hasattr(data, "items"):
            form_data = dict(data.items())
        else:
            form_data = dict(data)
        form_data["csrf_token"] = token

    request_headers: Dict[str, Any] = {}
    if headers:
        request_headers.update(headers)
    request_headers.setdefault("X-CSRFToken", token)

    return client.post(
        url,
        data=form_data,
        headers=request_headers,
        follow_redirects=follow_redirects,
        **kwargs,
    )
