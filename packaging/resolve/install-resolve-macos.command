#!/bin/bash
# Copy the public plugin and the native bridge from this Mac's own Resolve SDK.
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd -P)"
script_path="$script_dir/$(basename "$0")"
plugin_id="com.ijduncan.videoanalyzer"
plugin_path="$script_dir/VideoAnalyzer"
developer_path="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer"
destination="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/$plugin_id"
custom_destination=0
elevation_attempted=0
public_files=(manifest.xml package.json main.js preload.js host.js service.js panel.js controller.js index.html styles.css)

fail() { printf '%s\n' "Resolve installation failed: $*" >&2; exit 1; }
# The desktop companion passes its bundled plugin directory as the first argument.
if [ "$#" -gt 0 ] && [ "${1#/}" != "$1" ]; then plugin_path="$1"; shift; fi
while [ "$#" -gt 0 ]; do
    case "$1" in
        --plugin-path|--developer-path|--destination)
            [ "$#" -ge 2 ] || fail "Missing value for $1."
            case "$1" in
                --plugin-path) plugin_path="$2" ;;
                --developer-path) developer_path="$2" ;;
                --destination) destination="$2"; custom_destination=1 ;;
            esac
            shift 2 ;;
        --no-ui) shift ;;
        --elevation-attempted) elevation_attempted=1; shift ;;
        *) fail "Unknown installer argument: $1" ;;
    esac
done

[ "$(/usr/bin/uname -s)" = Darwin ] || fail "Use the Windows installer on Windows. This installer requires an Apple Silicon Mac."
[ "$(/usr/bin/uname -m)" = arm64 ] || fail "This preview requires native Apple Silicon; Intel and Rosetta are not supported."
[ "$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null || true)" != 1 ] || fail "Run Terminal natively instead of under Rosetta."

assert_no_resolve() {
    if /usr/bin/pgrep -x Resolve >/dev/null 2>&1; then
        fail "Close DaVinci Resolve before installing or updating Video Analyzer, then retry."
    fi
}
assert_no_links() {
    local cursor="$1"
    [ "${cursor#/}" != "$cursor" ] || fail "Installation paths must be absolute."
    case "$cursor" in *$'\n'*|*$'\r'*) fail "Invalid installation path." ;; esac
    while [ "$cursor" != / ] && [ -n "$cursor" ]; do
        [ ! -L "$cursor" ] || fail "Symbolic links are not accepted for installation paths."
        cursor="$(dirname "$cursor")"
    done
}
normalize_path() {
    local value="$1" parent leaf
    assert_no_links "$value"
    # Existing parents are resolved without following links (checked above).
    while [ "${value%/}" != "$value" ]; do value="${value%/}"; done
    [ -n "$value" ] || fail "The filesystem root cannot be a plugin destination."
    case "$value" in */../*|*/./*|*/..|*/.) fail "Use a normalized absolute path without dot segments." ;; esac
    printf '%s' "$value"
}
read_plugin_id() {
    local manifest="$1"
    assert_no_links "$manifest"
    [ -f "$manifest" ] || fail "Plugin manifest is missing."
    if /usr/bin/grep -q '<!DOCTYPE\|<!ENTITY' "$manifest"; then
        fail "Plugin manifests cannot contain DTDs or entities."
    fi
    /usr/bin/xmllint --nonet --xpath 'string(/BlackmagicDesign/Plugin/Id)' "$manifest" 2>/dev/null || fail "Plugin manifest is invalid XML."
}
assert_own_destination() {
    assert_no_links "$destination"
    if [ -e "$destination" ]; then
        [ -d "$destination" ] || fail "The plugin destination is not a directory."
        [ "$(read_plugin_id "$destination/manifest.xml")" = "$plugin_id" ] || fail "The existing destination does not identify itself as Video Analyzer. It was not changed."
    fi
}

assert_no_resolve
plugin_path="$(normalize_path "$plugin_path")"
developer_path="$(normalize_path "$developer_path")"
destination="$(normalize_path "$destination")"
[ -d "$plugin_path" ] || fail "Extract the entire integration ZIP; the VideoAnalyzer folder is missing."
for name in "${public_files[@]}"; do
    assert_no_links "$plugin_path/$name"
    [ -f "$plugin_path/$name" ] || fail "The public plugin file $name is missing."
done
[ "$(read_plugin_id "$plugin_path/manifest.xml")" = "$plugin_id" ] || fail "The source manifest is not the Video Analyzer Resolve plugin."
case "$plugin_path/" in "$destination/"*) fail "Source files cannot be overwritten by installation." ;; esac
case "$destination/" in "$plugin_path/"*) fail "Use a separate plugin destination outside the source directory." ;; esac
case "$developer_path/" in "$destination/"*) fail "The SDK directory cannot be overwritten by installation." ;; esac
case "$destination/" in "$developer_path/"*) fail "Install into a separate plugin directory, outside the SDK." ;; esac

bridge=''
for sample in SamplePromisePlugin SamplePlugin; do
    candidate="$developer_path/Workflow Integrations/Examples/$sample/WorkflowIntegration.node"
    if [ -f "$candidate" ]; then bridge="$candidate"; break; fi
done
[ -n "$bridge" ] || fail "The Resolve SDK bridge was not found. Install Resolve Studio 20.1 or later, or use --developer-path with its Developer folder. The bridge is not included in this download."
assert_no_links "$bridge"
architectures="$(/usr/bin/lipo -archs "$bridge" 2>/dev/null)" || fail "The local SDK bridge is not a Mach-O module."
case " $architectures " in *' arm64 '*) ;; *) fail "This Resolve SDK bridge lacks arm64. Install a Resolve Studio SDK with a native Apple Silicon bridge; this installer will not use Rosetta." ;; esac
assert_own_destination

writable_parent="$destination"
while [ ! -d "$writable_parent" ]; do writable_parent="$(dirname "$writable_parent")"; done
if [ ! -w "$writable_parent" ]; then
    [ "$custom_destination" -eq 0 ] && [ "$elevation_attempted" -eq 0 ] || fail "The plugin destination is not writable."
    printf '%s\n' 'Resolve stores plugins in /Library. Administrator permission is needed to copy this plugin.'
    exec /usr/bin/sudo /bin/bash "$script_path" --plugin-path "$plugin_path" --developer-path "$developer_path" --elevation-attempted
fi

created_destination=0
[ -d "$destination" ] || created_destination=1
completed=0
/bin/mkdir -p "$destination"
temporary_files=()
cleanup() {
    local temporary
    for temporary in "${temporary_files[@]}"; do
        [ ! -f "$temporary" ] || /bin/rm -f -- "$temporary"
    done
    if [ "$created_destination" -eq 1 ] && [ "$completed" -eq 0 ]; then
        assert_no_links "$destination"
        # rmdir can remove only the empty directory this invocation created.
        /bin/rmdir "$destination" 2>/dev/null || true
    fi
}
trap cleanup EXIT
for name in "${public_files[@]}" WorkflowIntegration.node; do
    source_file="$plugin_path/$name"
    [ "$name" != WorkflowIntegration.node ] || source_file="$bridge"
    assert_no_links "$source_file"
    assert_no_links "$destination/$name"
    [ ! -d "$destination/$name" ] || fail "A directory exists where plugin file $name should be. It was not overwritten."
    temporary="$(/usr/bin/mktemp "$destination/.video-analyzer-XXXXXXXX")"
    temporary_files+=("$temporary")
    /bin/cp "$source_file" "$temporary"
    /bin/chmod 644 "$temporary"
done
assert_no_resolve
index=0
for name in "${public_files[@]}" WorkflowIntegration.node; do
    assert_no_links "$destination/$name"
    /bin/mv -f "${temporary_files[$index]}" "$destination/$name"
    index=$((index + 1))
done
completed=1
printf '\n%s\n' 'Video Analyzer was installed for Resolve.' 'Open DaVinci Resolve Studio, then choose Workspace > Workflow Integrations > Video Analyzer.' 'Start the Video Analyzer companion and paste its editor pairing token into the plugin.' 'Original media, your analysis library, and certificate trust were not changed.'
