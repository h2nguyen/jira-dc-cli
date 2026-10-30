"""The route handlers of the mock Jira, one per feature area. `MockJira` asks them in this order before its own routes."""
from __future__ import annotations

from .mock_agile import AgileRoutes
from .mock_attachments import AttachmentsRoutes
from .mock_collab import CollabRoutes
from .mock_comments import CommentsRoutes
from .mock_reading import ReadingRoutes
from .mock_reference import ReferenceRoutes
from .mock_users import UsersRoutes
from .mock_validation import ValidationRoutes

EXTENSIONS = (
    AttachmentsRoutes,
    AgileRoutes,
    CommentsRoutes,
    UsersRoutes,
    ValidationRoutes,
    ReadingRoutes,
    ReferenceRoutes,
    CollabRoutes,
)
