#!/usr/bin/env bash
# Install the released Whitaker binary used by OrthoConfig's Linux CI lane.
#
# This helper owns only CI bootstrap from leynos/whitaker release assets.  The
# caller supplies the resolved release version and the workflow token through
# INSTALL_TOKEN; the helper does not build from source or install a user tool.

set -euo pipefail

readonly WHITAKER_REPOSITORY="leynos/whitaker"
readonly CACHE_DIRECTORY="${XDG_CACHE_HOME:-${HOME}/.cache}/ortho-config/whitaker-installer"
temporary_directory=""


die() {
    printf '%s\n' "$*" >&2
    exit 1
}


report_status() {
    case "$1" in
        cache=download | cache=repair | cache=reused | verification=passed | verification=failed)
            printf 'whitaker-installer: %s\n' "$1" >&2
            ;;
        *) die "invalid Whitaker installer status" ;;
    esac
}


cleanup_temporary_directory() {
    rm -rf -- "$temporary_directory"
}


require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}


target_from_architecture() {
    case "$1" in
        X64 | x86_64) printf '%s\n' "x86_64-unknown-linux-gnu" ;;
        ARM64 | aarch64) printf '%s\n' "aarch64-unknown-linux-gnu" ;;
        *) die "unsupported Whitaker installer architecture: $1" ;;
    esac
}


asset_metadata() {
    local release_path="$1"
    local asset_name="$2"

    jq -er --arg name "$asset_name" '
        [.assets[] | select(.name == $name and .state == "uploaded")] as $assets
        | if ($assets | length) == 1 then $assets[0] else error("expected one asset") end
        | "\(.id)\t\(.digest)"
    ' "$release_path"
}


is_sha256_digest() {
    [[ "$1" =~ ^sha256:[0-9a-f]{64}$ ]]
}


cached_assets_verify() {
    local archive_path="$1"
    local checksum_path="$2"
    local archive_name="$3"
    local archive_digest="$4"
    local checksum_digest="$5"
    local actual_checksum_digest
    local sidecar_digest
    local sidecar_name

    [[ -s "$archive_path" && -s "$checksum_path" ]] || return 1
    actual_checksum_digest="$(sha256sum "$checksum_path" | awk '{print $1}')"
    [[ "$actual_checksum_digest" == "${checksum_digest#sha256:}" ]] || return 1
    read -r sidecar_digest sidecar_name < "$checksum_path"
    [[ "$sidecar_digest" == "${archive_digest#sha256:}" ]] || return 1
    [[ "$sidecar_name" == "$archive_name" ]] || return 1
    (
        cd "$(dirname "$archive_path")"
        sha256sum --check --status "$(basename "$checksum_path")"
    )
}


download_asset() {
    local asset_id="$1"
    local destination="$2"

    GH_TOKEN="${INSTALL_TOKEN}" gh api -H "Accept: application/octet-stream" \
        "repos/${WHITAKER_REPOSITORY}/releases/assets/${asset_id}" > "$destination"
}


main() {
    local version="${WHITAKER_INSTALLER_VERSION:?WHITAKER_INSTALLER_VERSION is required}"
    local architecture="${RUNNER_ARCH:-$(uname -m)}"
    local target
    local archive_name
    local checksum_name
    local member_name
    local release_path
    local archive_id
    local archive_digest
    local checksum_id
    local checksum_digest
    local archive_path
    local checksum_path

    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid Whitaker version: $version"
    [[ -n "${INSTALL_TOKEN:-}" ]] || die "INSTALL_TOKEN is required"
    for tool in gh jq sha256sum tar install mktemp; do
        require_command "$tool"
    done

    target="$(target_from_architecture "$architecture")"
    archive_name="whitaker-installer-${target}-v${version}.tgz"
    checksum_name="${archive_name}.sha256"
    member_name="whitaker-installer-${target}-v${version}/whitaker-installer"
    temporary_directory="$(mktemp -d)"
    trap cleanup_temporary_directory EXIT
    release_path="${temporary_directory}/release.json"

    GH_TOKEN="${INSTALL_TOKEN}" gh api \
        "repos/${WHITAKER_REPOSITORY}/releases/tags/v${version}" > "$release_path"
    IFS=$'\t' read -r archive_id archive_digest < <(asset_metadata "$release_path" "$archive_name")
    IFS=$'\t' read -r checksum_id checksum_digest < <(
        asset_metadata "$release_path" "$checksum_name"
    )
    if ! is_sha256_digest "$archive_digest"; then
        report_status "verification=failed"
        die "archive has no SHA-256 API digest"
    fi
    if ! is_sha256_digest "$checksum_digest"; then
        report_status "verification=failed"
        die "checksum has no SHA-256 API digest"
    fi

    mkdir -p "$CACHE_DIRECTORY"
    archive_path="${CACHE_DIRECTORY}/${archive_name}"
    checksum_path="${CACHE_DIRECTORY}/${checksum_name}"
    if cached_assets_verify "$archive_path" "$checksum_path" "$archive_name" \
        "$archive_digest" "$checksum_digest"; then
        report_status "cache=reused"
    else
        if [[ -e "$archive_path" || -e "$checksum_path" ]]; then
            report_status "cache=repair"
        else
            report_status "cache=download"
        fi
        rm -f -- "$archive_path" "$checksum_path"
        download_asset "$archive_id" "${temporary_directory}/${archive_name}"
        download_asset "$checksum_id" "${temporary_directory}/${checksum_name}"
        if ! cached_assets_verify "${temporary_directory}/${archive_name}" \
            "${temporary_directory}/${checksum_name}" "$archive_name" "$archive_digest" \
            "$checksum_digest"; then
            report_status "verification=failed"
            die "downloaded Whitaker release assets failed verification"
        fi
        install -m 0644 "${temporary_directory}/${archive_name}" "$archive_path"
        install -m 0644 "${temporary_directory}/${checksum_name}" "$checksum_path"
    fi
    if ! cached_assets_verify "$archive_path" "$checksum_path" "$archive_name" \
        "$archive_digest" "$checksum_digest"; then
        report_status "verification=failed"
        die "cached Whitaker release assets failed verification"
    fi
    report_status "verification=passed"

    tar --extract --gzip --no-same-owner --file "$archive_path" \
        --directory "$temporary_directory" "$member_name"
    install -D -m 0755 "${temporary_directory}/${member_name}" \
        "${CARGO_HOME:-${HOME}/.cargo}/bin/whitaker-installer"
}


main "$@"
