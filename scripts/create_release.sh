#!/usr/bin/env bash
set -euo pipefail

BUMP="${1:-}"

if [[ ! "$BUMP" =~ ^(major|minor|patch)$ ]]; then
  echo "Usage: $0 <major|minor|patch>"
  exit 1
fi

LATEST=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
IFS='.' read -r MAJOR MINOR PATCH <<< "${LATEST#v}"

case "$BUMP" in
  major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
  patch) PATCH=$((PATCH + 1)) ;;
esac

TAG="v${MAJOR}.${MINOR}.${PATCH}"

echo "Current: $LATEST → New: $TAG"
git tag "$TAG"
git push origin "$TAG"
echo "Released $TAG"
