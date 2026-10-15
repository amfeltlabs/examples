#!/bin/sh
# Install the amfelt CLI, or the plugins a project declares, from their GitHub releases.
#
# This is the install path until the release channel exists (roadmap#763). The releases
# are private, so it needs the GitHub CLI, gh, signed in with read access to
# amfeltlabs/agent and amfeltlabs/hub. Once the channel exists, a package manager installs
# the CLI, and `amfelt plugin install` provisions the plugins; steps/lib.sh says where
# that one line goes.
#
# Usage:
#   sh steps/install-from-github.sh cli BIN_DIR
#       Installs amfelt, the version project/IMAGE names as AGENT_VERSION, as
#       BIN_DIR/amfelt. Put BIN_DIR on your PATH.
#   sh steps/install-from-github.sh plugins PROJECT WORKSPACE
#       Installs every plugin the project file PROJECT declares, at the version it
#       declares, where the agent looks for installed plugins in the workspace directory
#       WORKSPACE: WORKSPACE/plugins/<category>/amfelt/<plugin>/v<version>/. The agent's
#       workspace is .amfelt in the directory it runs in.
#
# Each archive is checked against the checksums file of the release it comes from before
# anything is extracted, and each installed binary must report the version asked for.
# A binary that is already installed at that version is left as it is.
set -eu

jaffle=$(cd "$(dirname "$0")/.." && pwd)

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

# platform: the OS and architecture as the release archives name them.
platform() {
    case $(uname -s) in
        Darwin) os=Darwin ;;
        Linux) os=Linux ;;
        *) fail "no amfelt release for $(uname -s); the steps run on macOS and Linux" ;;
    esac
    case $(uname -m) in
        x86_64 | amd64) arch=x86_64 ;;
        arm64 | aarch64) arch=arm64 ;;
        *) fail "no amfelt release for the architecture $(uname -m)" ;;
    esac
    printf '%s_%s\n' "$os" "$arch"
}

# sha256_check FILE: check the "<hex>  <name>" lines of FILE, in FILE's directory.
sha256_check() {
    if command -v sha256sum >/dev/null 2>&1; then
        (cd "$(dirname "$1")" && sha256sum -c "$(basename "$1")")
    else
        (cd "$(dirname "$1")" && shasum -a 256 -c "$(basename "$1")")
    fi
}

# reports BINARY NAME VERSION: BINARY's `version` output starts with "NAME VERSION".
reports() {
    out=$(NO_COLOR=1 "$1" version 2>&1) || return 1
    case $out in
        "$2 $3" | "$2 $3 "*) return 0 ;;
        *) return 1 ;;
    esac
}

# fetch REPO TAG ASSET CHECKSUMS DIR: download ASSET of the release TAG of REPO into DIR,
# and check it against that release's CHECKSUMS file.
fetch() {
    echo "Downloading $3 from the release $2 of $1"
    gh release download "$2" --repo "$1" --pattern "$3" --pattern "$4" --dir "$5" \
        || fail "cannot download $3 from the release $2 of $1; gh needs read access to $1"
    grep -E "^[0-9a-f]{64}  $3\$" "$5/$4" >"$5/$3.sha256" \
        || fail "the release $2 of $1 lists no checksum for $3 in $4"
    sha256_check "$5/$3.sha256" >/dev/null || fail "$3 does not match its checksum in $4"
}

# install_binary BINARY_NAME ARCHIVE_DIR TARGET NAME VERSION: extract BINARY_NAME from the one
# archive in ARCHIVE_DIR to TARGET, and check it reports NAME VERSION.
install_binary() {
    archive=$(find "$2" -name '*.tar.gz' | head -n 1)
    tar -xzf "$archive" -C "$2" "$1"
    mkdir -p "$(dirname "$3")"
    rm -f "$3"
    cp "$2/$1" "$3"
    chmod 0755 "$3"
    reports "$3" "$4" "$5" || fail "$3 does not report $4 $5"
    echo "Installed $4 $5 as $3"
}

command -v gh >/dev/null || fail "gh, the GitHub CLI, is not installed; until the release channel exists it downloads amfelt"
gh auth status >/dev/null 2>&1 || fail "gh is not signed in; run gh auth login"
plat=$(platform)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

case ${1:-} in
    cli)
        [ $# -eq 2 ] || fail "usage: install-from-github.sh cli BIN_DIR"
        version=$(sed -n 's/^AGENT_VERSION=//p' "$jaffle/project/IMAGE")
        [ -n "$version" ] || fail "project/IMAGE names no AGENT_VERSION"
        target=$2/amfelt
        if [ -x "$target" ] && reports "$target" amfelt "$version"; then
            echo "amfelt $version is already installed as $target"
            exit 0
        fi
        fetch amfeltlabs/agent "v$version" "amfelt_$plat.tar.gz" "amfelt_${version}_checksums.txt" "$tmp"
        install_binary amfelt "$tmp" "$target" amfelt "$version"
        ;;
    plugins)
        [ $# -eq 3 ] || fail "usage: install-from-github.sh plugins PROJECT WORKSPACE"
        project=$2
        workspace=$3
        [ -f "$project" ] || fail "$project is missing"
        # One line per declared plugin: category, name, version and registry, read with
        # check_project.py's reader of the project file.
        python3 -B -c '
import sys
sys.path.insert(0, sys.argv[1])
import check_project
project = check_project.load_project(sys.argv[2]) or {}
for category in ("source", "target"):
    for entry in (project.get("plugins") or {}).get(category) or []:
        print(category, entry.get("plugin") or "-", entry.get("version") or "-", entry.get("registry") or "-")
' "$jaffle" "$project" >"$tmp/plugins" || fail "cannot read the plugins of $project"
        [ -s "$tmp/plugins" ] || fail "$project declares no plugins"
        while read -r category name version registry; do
            [ "$registry" = local ] || fail "$project declares the $category plugin $name with registry $registry; only local plugins are installed"
            [ "$version" != - ] || fail "$project declares the $category plugin $name without a version; the releases are per version"
            binary=$category-$name
            target=$workspace/plugins/$category/amfelt/$name/v$version/$binary
            if [ -x "$target" ] && reports "$target" "$binary" "$version"; then
                echo "$binary $version is already installed in $workspace"
                continue
            fi
            mkdir -p "$tmp/$binary"
            fetch amfeltlabs/hub "$binary-v$version" "${binary}_$plat.tar.gz" "connectors_${version}_checksums.txt" "$tmp/$binary"
            install_binary "$binary" "$tmp/$binary" "$target" "$binary" "$version"
        done <"$tmp/plugins"
        ;;
    *)
        fail "usage: install-from-github.sh cli BIN_DIR | plugins PROJECT WORKSPACE"
        ;;
esac
