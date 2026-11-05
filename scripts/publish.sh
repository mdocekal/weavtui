#!/usr/bin/env bash
# Build weavtui and publish it to PyPI (or TestPyPI with --test).
#
#   scripts/publish.sh [--test] [--skip-tests] [--no-tag]
#
# Authentication: set UV_PUBLISH_TOKEN to a PyPI API token (pypi-...), or run from a CI
# job configured for PyPI trusted publishing. Bump the version first with
# `uv version --bump patch|minor|major` and commit it.
set -euo pipefail

cd "$(dirname "$0")/.."

repo="pypi"
run_tests=1
tag=1
for arg in "$@"; do
    case "$arg" in
        --test) repo="testpypi" ;;
        --skip-tests) run_tests=0 ;;
        --no-tag) tag=0 ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

step() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

version="$(uv version --short)"
step "Releasing weavtui $version to $repo"

# 1. Sanity checks -----------------------------------------------------------------
[[ -z "$(git status --porcelain)" ]] || die "working tree is not clean; commit or stash first"
if git rev-parse -q --verify "refs/tags/v$version" >/dev/null; then
    die "tag v$version already exists; bump the version (uv version --bump patch)"
fi
branch="$(git branch --show-current)"
[[ "$branch" == "main" ]] || echo "warning: releasing from branch '$branch', not main"

# 2. Lint and tests ----------------------------------------------------------------
if (( run_tests )); then
    step "Lint"
    uv run ruff check .
    uv run ruff format --check .
    step "Tests (integration tests are skipped if no Weaviate is running)"
    uv run pytest -q
fi

# 3. Build -------------------------------------------------------------------------
step "Build"
rm -rf dist
uv build
wheel="$(ls dist/weavtui-"$version"-*.whl)"

# 4. Check the artifacts -----------------------------------------------------------
step "Smoke test the wheel in a clean environment"
uv run --isolated --no-project --python 3.13 --with "$wheel" weavtui --version
uv run --isolated --no-project --python 3.13 --with "$wheel" \
    python -c "import importlib.resources as r; assert r.files('weavtui').joinpath('weavtui.tcss').is_file()"

step "Check package metadata and README rendering"
uvx twine@latest check --strict dist/*

# 5. Upload ------------------------------------------------------------------------
step "Upload to $repo"
if [[ "$repo" == "testpypi" ]]; then
    uv publish --publish-url https://test.pypi.org/legacy/ --check-url https://test.pypi.org/simple/
    echo "Try it: uvx --index https://test.pypi.org/simple/ --index-strategy unsafe-best-match weavtui==$version"
else
    uv publish
fi

# 6. Tag ---------------------------------------------------------------------------
if (( tag )) && [[ "$repo" == "pypi" ]]; then
    git tag -a "v$version" -m "weavtui $version"
    step "Tagged v$version. Push it with: git push origin v$version"
fi
