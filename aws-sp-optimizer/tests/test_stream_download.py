"""Tests for stream_download_json using the responses mock library."""

import responses

from scripts.pricing_cache import stream_download_json


@responses.activate
def test_stream_download_returns_parsed_json():
    responses.add(
        responses.GET,
        "https://example.test/rates.json",
        json={"key": "value", "nested": [1, 2, 3]},
        status=200,
    )
    result = stream_download_json("https://example.test/rates.json")
    assert result == {"key": "value", "nested": [1, 2, 3]}


@responses.activate
def test_stream_download_raises_on_404():
    import requests

    responses.add(
        responses.GET,
        "https://example.test/missing.json",
        status=404,
    )
    import pytest

    with pytest.raises(requests.exceptions.HTTPError):
        stream_download_json("https://example.test/missing.json")
