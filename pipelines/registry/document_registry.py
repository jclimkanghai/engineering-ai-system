"""Deprecated compatibility import for the ingestion-only document manifest.

Controlled document identity, revisions, supersession and governing status are
owned by Engineering Registry. New callers should use ``document_manifest`` for
ingestion metadata or Registry APIs for document control.
"""

from .document_manifest import *  # noqa: F403
