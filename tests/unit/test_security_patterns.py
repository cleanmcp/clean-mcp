"""Tests for sensitive file pattern matching."""

import re
import os
import tempfile
from clean.util.security import is_safe_to_index, SENSITIVE_FILE_PATTERNS


def _matches_sensitive(filename: str) -> bool:
    """Check if a filename matches any sensitive pattern."""
    for pattern in SENSITIVE_FILE_PATTERNS:
        if re.search(pattern, filename, re.IGNORECASE):
            return True
    return False


class TestSensitiveFilePatterns:
    def test_blocks_dot_env(self):
        assert _matches_sensitive(".env") is True

    def test_blocks_dot_env_local(self):
        assert _matches_sensitive(".env.local") is True

    def test_blocks_dot_env_production(self):
        assert _matches_sensitive(".env.production") is True

    def test_blocks_dot_env_development(self):
        assert _matches_sensitive(".env.development") is True

    def test_allows_dot_env_example(self):
        assert _matches_sensitive(".env.example") is False

    def test_allows_dot_env_template(self):
        assert _matches_sensitive(".env.template") is False

    def test_allows_dot_env_sample(self):
        assert _matches_sensitive(".env.sample") is False

    def test_blocks_credentials_json(self):
        assert _matches_sensitive("credentials.json") is True

    def test_blocks_private_key_pem(self):
        assert _matches_sensitive("private_key.pem") is True

    def test_blocks_id_rsa(self):
        assert _matches_sensitive("id_rsa") is True

    def test_allows_regular_python(self):
        assert _matches_sensitive("main.py") is False

    def test_allows_package_json(self):
        assert _matches_sensitive("package.json") is False


class TestIsSafeToIndex:
    def test_env_example_is_safe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, ".env.example")
            with open(path, "w") as f:
                f.write("DATABASE_URL=changeme\n")
            safe, reason = is_safe_to_index(path, tmpdir)
            assert safe is True, f"Expected .env.example to be safe, got: {reason}"

    def test_env_local_is_not_safe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, ".env.local")
            with open(path, "w") as f:
                f.write("SECRET=real_secret\n")
            safe, reason = is_safe_to_index(path, tmpdir)
            assert safe is False
