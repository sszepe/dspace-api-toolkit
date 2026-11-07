"""
Tests for DSpace vocabulary module
"""

import pytest
from unittest.mock import Mock
from dspace_api.vocabulary import VocabularyManager
from dspace_api.config import Endpoints

class TestVocabularyManager:
    """Test cases for VocabularyManager"""

    def test_init(self, mock_auth):
        """Test VocabularyManager initialization"""
        manager = VocabularyManager(mock_auth)
        assert manager.auth == mock_auth
        assert manager.session == mock_auth.session
        assert manager.config == mock_auth.config