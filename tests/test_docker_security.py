from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def test_dockerfile_non_root_user():
    """Verifies that the Dockerfile drops root privileges and runs as an unprivileged user."""
    dockerfile_path = BASE_DIR / "Dockerfile"
    assert dockerfile_path.exists(), "Dockerfile must exist"

    content = dockerfile_path.read_text(encoding="utf-8")

    # Verify multi-stage build
    assert "FROM python:3.11-slim AS builder" in content
    assert "FROM python:3.11-slim AS runner" in content

    # Verify non-root user creation & usage
    assert "groupadd -g 1001 appgroup" in content
    assert "useradd -u 1001 -g appgroup" in content
    assert "USER 1001:1001" in content

    # Verify no root ownership of write directories
    assert "chown -R appuser:appgroup" in content

    # Verify built-in container healthcheck
    assert "HEALTHCHECK" in content


def test_dockerignore_excludes_secrets():
    """Ensures .dockerignore blocks sensitive environment variables and dev databases."""
    dockerignore_path = BASE_DIR / ".dockerignore"
    assert dockerignore_path.exists(), ".dockerignore must exist"

    content = dockerignore_path.read_text(encoding="utf-8")
    assert ".env" in content
    assert ".git" in content
    assert "data/*.db" in content
