#!/usr/bin/env bash
set -eo pipefail

BUMP="${1:-}"

if [[ ! "$BUMP" =~ ^(major|minor|patch)$ ]]; then
    echo "Usage: $0 <major|minor|patch>"
    exit 1
fi

is_head_already_tagged() {
    if [ "$(git tag --points-at=HEAD | wc -l)" -gt 0 ]; then
        printf true
    else
        printf false
    fi
}

has_git_signing_key() {
    if ! git config --get user.signingKey > /dev/null 2>&1; then
        printf false
    else
        printf true
    fi
}

if [ "$(is_head_already_tagged)" = true ]; then
    printf "HEAD is already tagged. Exiting now.\n" >&2
    exit
fi

LATEST=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
IFS='.' read -r MAJOR MINOR PATCH <<< "${LATEST#v}"

case "$BUMP" in
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    patch) PATCH=$((PATCH + 1)) ;;
esac

TAG="v${MAJOR}.${MINOR}.${PATCH}"

if [ "$(has_git_signing_key)" = true ]; then
    git_tag_cmd="git tag -s"
else
    git_tag_cmd="git tag"
fi

printf "Current: %s → New: %s\n" "$LATEST" "$TAG" >&2

if [ "$LATEST" = "v0.0.0" ]; then
    release_notes_ref="HEAD"
else
    release_notes_ref="HEAD...$LATEST"
fi
release_notes=$(git log --format=-\ %s "$release_notes_ref" -- . ':!infrastructure' ':!.github' ':!scripts')
printf "%s\n\n%s" "$TAG" "$release_notes" | eval "$git_tag_cmd" -F - "$TAG"

git push origin HEAD
git push origin "$TAG"
printf "Released %s\n" "$TAG" >&2
