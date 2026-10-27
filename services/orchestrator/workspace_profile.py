"""Opaque profile namespace for browser presentation preferences, never scientific data."""
import hashlib


def profile_id(paths):
    # Keep the namespace stable for existing workspace-study tabs and drafts.
    root = str(paths.sessions.resolve())
    return hashlib.sha256(('workspace-study-profile-v1\0' + root).encode()).hexdigest()[:32]
