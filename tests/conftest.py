"""
Shared pytest fixtures. Mocks the Groq client so LLM-touching tests run
without network access or an API key.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_groq_client():
    """Patch tools._get_groq_client to return a client with a canned response."""
    fake_response = MagicMock()
    fake_response.choices[0].message.content = (
        "Pair this with your favorite jeans and sneakers for an effortless look. "
        "Great for a casual day out.\n#thrifted #ootd #secondhandstyle"
    )

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    with patch("tools._get_groq_client", return_value=fake_client):
        yield fake_client
