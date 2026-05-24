from pathlib import Path


def test_dockerfile_installs_dependencies_before_copying_app_code():
    dockerfile = Path("Dockerfile").read_text()

    copy_pyproject = dockerfile.index("COPY pyproject.toml .")
    requirements = dockerfile.index("/tmp/requirements.txt")
    install_requirements = dockerfile.index("pip install --no-cache-dir -r /tmp/requirements.txt")
    copy_src = dockerfile.index("COPY src/ src/")
    editable_install = dockerfile.index('pip install --no-cache-dir --no-deps -e ".[serve]"')

    assert copy_pyproject < requirements < install_requirements < copy_src < editable_install


def test_dockerfile_editable_install_does_not_reinstall_dependencies():
    dockerfile = Path("Dockerfile").read_text()

    assert 'pip install --no-cache-dir --no-deps -e ".[serve]"' in dockerfile
