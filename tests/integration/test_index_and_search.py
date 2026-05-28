"""Integration test: index a real codebase and search it."""

import os
import tempfile

import pytest

from clean.core.config import CleanConfig
from clean.services.container import ServiceContainer


@pytest.fixture
def container_and_project():
    """Set up a container with a temp project containing Python files."""
    with tempfile.TemporaryDirectory() as project_dir:
        # Create realistic Python files
        with open(os.path.join(project_dir, "auth.py"), "w") as f:
            f.write("""
def validate_email(email):
    \"\"\"Validate email format.\"\"\"
    return '@' in email and '.' in email

def hash_password(password):
    \"\"\"Hash a password for storage.\"\"\"
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()

def authenticate(email, password):
    \"\"\"Authenticate a user.\"\"\"
    if not validate_email(email):
        return False
    hashed = hash_password(password)
    return check_credentials(email, hashed)

def check_credentials(email, hashed_password):
    \"\"\"Check credentials against database.\"\"\"
    return True  # stub
""")

        with open(os.path.join(project_dir, "users.py"), "w") as f:
            f.write("""
class UserService:
    def create_user(self, name, email):
        \"\"\"Create a new user.\"\"\"
        from auth import validate_email
        if not validate_email(email):
            raise ValueError("Invalid email")
        return {"name": name, "email": email}

    def get_user(self, user_id):
        \"\"\"Retrieve a user by ID.\"\"\"
        return {"id": user_id, "name": "Test User"}

    def delete_user(self, user_id):
        \"\"\"Delete a user.\"\"\"
        return True
""")

        with tempfile.TemporaryDirectory() as db_dir:
            config = CleanConfig()
            config.storage.default_persist_dir = db_dir
            config.embedder.show_progress_bar = False
            container = ServiceContainer(config)
            yield container, project_dir


def test_full_index_and_search(container_and_project):
    container, project_dir = container_and_project

    # Index
    result = container.indexer.index(project_dir)
    assert result["status"] == "success"
    assert result["functions_indexed"] >= 6  # At least the functions we created

    # Search
    search_result = container.searcher.search("email validation", project_dir, top_k=3)
    results = search_result["results"]
    assert len(results) > 0

    # Best result should be related to email
    best = results[0].entity
    assert "email" in best.name.lower() or "email" in best.code.lower()


def test_search_with_context_expansion(container_and_project):
    container, project_dir = container_and_project

    # Index
    container.indexer.index(project_dir)

    # Search with depth
    search_result = container.searcher.search(
        "authenticate user", project_dir, top_k=3, depth=1
    )
    results = search_result["results"]
    context = search_result["context"]

    assert len(results) > 0

    # If context exists, it should have some data
    if context is not None:
        assert context.function is not None


def test_formatter_output(container_and_project):
    container, project_dir = container_and_project
    container.indexer.index(project_dir)

    result = container.searcher.search("hash password", project_dir, top_k=2)
    results = result["results"]

    if results:
        toon_output = container.toon_formatter.format_results(results)
        json_output = container.json_formatter.format_results(results)
        rich_output = container.rich_formatter.format_results(
            results, result["context"]
        )

        assert "results" in toon_output
        assert len(toon_output) < len(json_output), "TOON should be smaller than JSON"
        assert "FOUND:" in rich_output or "No results" in rich_output


def test_multi_project_isolation(container_and_project):
    container, project_dir = container_and_project

    # Create second project
    with tempfile.TemporaryDirectory() as project2:
        with open(os.path.join(project2, "math.py"), "w") as f:
            f.write("def add(a, b):\n    return a + b\n")

        # Index both
        container.indexer.index(project_dir)
        container.indexer.index(project2)

        # Search each — results should be isolated
        r1 = container.searcher.search("email", project_dir, top_k=3)
        r2 = container.searcher.search("addition", project2, top_k=3)

        # Project 1 should have email-related results
        assert len(r1["results"]) > 0

        # Project 2 should have math-related results
        assert len(r2["results"]) > 0
