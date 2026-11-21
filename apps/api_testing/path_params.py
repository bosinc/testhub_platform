"""Helpers for OpenAPI-style single-brace path parameters."""

import re
from urllib.parse import quote


PATH_PARAMETER_RE = re.compile(r'(?<!\{)\{([^{}]+)\}(?!\})')


def resolve_path_parameters(url, params):
    """Substitute `{name}` path values and return remaining query parameters."""
    query_params = list(params or [])
    values = {}
    for key, value in query_params:
        values.setdefault(key, value)

    def substitute(match):
        key = match.group(1).strip()
        if key not in values:
            return match.group(0)
        value = values[key]
        query_params[:] = [(item_key, item_value) for item_key, item_value in query_params
                            if item_key != key]
        if value is None or value == '':
            return match.group(0)
        return quote(str(value), safe='')

    return PATH_PARAMETER_RE.sub(substitute, url or ''), query_params
