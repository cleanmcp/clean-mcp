"""Sample Python file for parser tests."""


def greet(name):
    """Greet someone."""
    message = format_greeting(name)
    return message


def format_greeting(name):
    """Format a greeting message."""
    return f"Hello, {name}!"


class UserService:
    """Service for user operations."""

    def get_user(self, user_id):
        """Get a user by ID."""
        data = self.fetch_data(user_id)
        return self.validate(data)

    def fetch_data(self, user_id):
        """Fetch user data."""
        return {"id": user_id, "name": "Test"}

    def validate(self, data):
        """Validate user data."""
        return data
