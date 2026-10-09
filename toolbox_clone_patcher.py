#!/usr/bin/env python3
"""Toolbox clone patcher (CLI).

Retargets a decompiled Toolbox project to a custom MCBE clone package
and optionally renames the Toolbox app itself (package + label) so the
clone can be installed side-by-side with the original.

Supports decompilations made with ApkTool M only. Other apktool
frontends / versions may use a different project layout and are NOT
supported by this script.
"""

import json
import os
import re
import sys
from collections import Counter

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

WARNING_TEXT = (
    "WARNING: this script supports decompilations created with ApkTool M "
    "only (apktool.json + AndroidManifest.xml + smali/ + res/ layout). "
    "Projects decoded with other tools are not supported."
)

# smali files known to contain the MCBE package string (dotted form).
# Files are resolved relative to the project root. Any extra *.smali file
# containing the exact package string is patched as well (auto-scan).
KNOWN_SMALI_FILES = [
    "smali/io/mrarm/mctoolbox/MinecraftActivity.smali",
    "smali/io/mrarm/mctoolbox/ErrorActivity.smali",
    "smali/io/mrarm/mctoolbox/DiagnosticActivity.smali",
    "smali/io/mrarm/mctoolbox/RelaunchActivity.smali",
    "smali/mx3.smali",
    "smali/nf3.smali",
    "smali/my3.smali",
    "smali/ky3.smali",
    "smali/fy3.smali",
    "smali/io/mrarm/yurai/xbox/CLLAuthProvider.smali",
    "smali/com/mojang/minecraftpe/HardwareInformation.smali",
    "smali/com/microsoft/xbox/idp/interop/Interop.smali",
]

REQUIRED_PATHS = [
    "AndroidManifest.xml",
    "apktool.json",
    "smali",
    "res",
    os.path.join("res", "values", "strings.xml"),
]

PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
MCBE_FIND_RE = re.compile(r"com\.mojang\.minecraftpe[A-Za-z0-9_]*")
MANIFEST_PACKAGE_RE = re.compile(r'package="([^"]+)"')
APP_NAME_RE = re.compile(
    r'(<string\s+name="app_name">)(.*?)(</string\s*>)', re.DOTALL
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def write_text(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(data)


def ask(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)


def valid_package(name):
    return bool(PACKAGE_RE.match(name))


def check_project(root):
    """Return a list of missing required paths (empty = OK)."""
    missing = []
    for rel in REQUIRED_PATHS:
        if not os.path.exists(os.path.join(root, rel)):
            missing.append(rel)
    return missing


def detect_mcbe_package(root):
    """Detect the currently used MCBE package from known smali files."""
    hits = Counter()
    for rel in KNOWN_SMALI_FILES:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        try:
            hits.update(MCBE_FIND_RE.findall(read_text(path)))
        except (OSError, UnicodeDecodeError):
            continue
    if not hits:
        return None
    return hits.most_common(1)[0][0]


def patch_mcbe_package(root, old, new):
    """Replace the exact dotted MCBE package in smali. Returns (files, count)."""
    targets = []
    for rel in KNOWN_SMALI_FILES:
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            targets.append(path)
    # Auto-scan: catch the same string in any other smali file.
    for dirpath, _dirs, files in os.walk(os.path.join(root, "smali")):
        for name in files:
            if not name.endswith(".smali"):
                continue
            full = os.path.join(dirpath, name)
            if full in targets:
                continue
            try:
                if old in read_text(full):
                    targets.append(full)
            except (OSError, UnicodeDecodeError):
                continue
    changed_files = 0
    total = 0
    for path in targets:
        try:
            text = read_text(path)
        except (OSError, UnicodeDecodeError):
            continue
        count = text.count(old)
        if count:
            write_text(path, text.replace(old, new))
            changed_files += 1
            total += count
    return changed_files, total


def verify_absent(root, needle, include=(".smali",)):
    """True when needle no longer occurs in project text files.

    The match is boundary-aware: a needle that is only a prefix of a
    longer (already patched) package name does not count.
    """
    boundary = re.compile(re.escape(needle) + r"(?![A-Za-z0-9_])")
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if include and not name.endswith(include):
                continue
            full = os.path.join(dirpath, name)
            try:
                if boundary.search(read_text(full)):
                    return False
            except (OSError, UnicodeDecodeError):
                continue
    return True


def get_manifest_package(root):
    text = read_text(os.path.join(root, "AndroidManifest.xml"))
    match = MANIFEST_PACKAGE_RE.search(text)
    return match.group(1) if match else None


def patch_toolbox_package(root, old, new):
    """Rename the Toolbox app package (manifest + apktool.json).

    Class names (e.g. io.mrarm.mctoolbox.MinecraftActivity) are left
    untouched on purpose: only the manifest package, the instrumentation
    target and provider authorities are renamed.
    """
    manifest = os.path.join(root, "AndroidManifest.xml")
    text = read_text(manifest)
    text = text.replace('package="%s"' % old, 'package="%s"' % new)
    text = text.replace('android:targetPackage="%s"' % old,
                        'android:targetPackage="%s"' % new)
    text = re.sub(
        r'android:authorities="%s([^"]*)"' % re.escape(old),
        r'android:authorities="%s\1"' % new,
        text,
    )
    write_text(manifest, text)

    meta_path = os.path.join(root, "apktool.json")
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return
    meta.setdefault("PackageInfo", {})["renameManifestPackage"] = new
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")


def get_app_name(root):
    text = read_text(
        os.path.join(root, "res", "values", "strings.xml"))
    match = APP_NAME_RE.search(text)
    return match.group(2).strip() if match else None


def patch_app_name(root, new_label):
    """Update app_name in default + ru locales. Returns files changed."""
    changed = []
    for rel in (os.path.join("res", "values", "strings.xml"),
                os.path.join("res", "values-ru", "strings.xml")):
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        text = read_text(path)
        new_text, count = APP_NAME_RE.subn(
            lambda m: m.group(1) + new_label + m.group(3), text, count=1)
        if count:
            write_text(path, new_text)
            changed.append(rel)
    # Keep the build output name in sync with the new label.
    safe = re.sub(r"[^\w\-]+", "_", new_label).strip("_") or "Toolbox"
    meta_path = os.path.join(root, "apktool.json")
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        meta["apkFileName"] = safe + ".apk"
        old_out = meta.get("apkFilePath")
        if isinstance(old_out, str) and "/" in old_out:
            meta["apkFilePath"] = old_out.rsplit("/", 1)[0] + "/" + safe + ".apk"
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
            fh.write("\n")
    except (OSError, ValueError):
        pass
    return changed


# --------------------------------------------------------------------------
# Main flow
# --------------------------------------------------------------------------

def patch_one_project(root):
    print("\n--- MCBE clone package ---")
    current_mcbe = detect_mcbe_package(root)
    if current_mcbe:
        print("Detected MCBE package: %s" % current_mcbe)
    new_mcbe = ask(
        "Enter the final MCBE clone package name "
        "(all needed files will be patched to it): "
    )
    mcbe_done = False
    if new_mcbe:
        if not valid_package(new_mcbe):
            print("ERROR: '%s' is not a valid Android package name. "
                  "Skipping MCBE patch." % new_mcbe)
        elif current_mcbe and new_mcbe == current_mcbe:
            print("MCBE package already set to %s, nothing to do." % new_mcbe)
            mcbe_done = True
        elif not current_mcbe:
            print("ERROR: could not detect the current MCBE package, "
                  "nothing was patched.")
        else:
            files, count = patch_mcbe_package(root, current_mcbe, new_mcbe)
            mcbe_done = (
                verify_absent(root, current_mcbe) and count > 0
            )
            print("Patched %d occurrence(s) in %d file(s)."
                  % (count, files))
    else:
        print("MCBE package left unchanged.")
        mcbe_done = True

    print("\n--- Toolbox package ---")
    print("Change the Toolbox package so the clone does not conflict "
          "with the original installation on the same device.")
    current_pkg = get_manifest_package(root)
    if current_pkg:
        print("Current Toolbox package: %s" % current_pkg)
    new_pkg = ask(
        "Enter new Toolbox package name [%s] (Enter = skip): "
        % (current_pkg or "skip")
    )
    pkg_done = False
    if new_pkg:
        if not valid_package(new_pkg):
            print("ERROR: '%s' is not a valid Android package name. "
                  "Skipping." % new_pkg)
        elif current_pkg and new_pkg == current_pkg:
            print("Package already set, nothing to do.")
            pkg_done = True
        elif not current_pkg:
            print("ERROR: could not read the manifest package, "
                  "nothing was patched.")
        else:
            patch_toolbox_package(root, current_pkg, new_pkg)
            check = get_manifest_package(root) == new_pkg
            print("Toolbox package set to %s." % new_pkg)
            pkg_done = check
    else:
        print("Toolbox package left unchanged.")
        pkg_done = True

    print("\n--- Toolbox application label ---")
    current_label = get_app_name(root)
    if current_label:
        print("Current application label: %s" % current_label)
    new_label = ask(
        "Enter final Toolbox application label [%s] (Enter = skip): "
        % (current_label or "skip")
    )
    label_done = False
    if new_label:
        changed = patch_app_name(root, new_label)
        label_done = get_app_name(root) == new_label
        print("Application label set to '%s' (%d file(s))."
              % (new_label, len(changed)))
    else:
        print("Application label left unchanged.")
        label_done = True

    print()
    if mcbe_done and pkg_done and label_done:
        print("SUCCESS: everything was patched as requested.")
    else:
        print("DONE with warnings: some steps were skipped or failed, "
              "see messages above.")
    return mcbe_done and pkg_done and label_done


def main():
    print("=" * 60)
    print("Toolbox clone patcher")
    print("=" * 60)
    print(WARNING_TEXT)
    while True:
        print()
        root = ask(
            "Enter path to the decompiled Toolbox folder "
            "(0 = exit): "
        )
        if root in ("", "0"):
            print("Bye.")
            return 0
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            print("ERROR: folder not found: %s" % root)
            continue
        missing = check_project(root)
        if missing:
            print("ERROR: this does not look like an ApkTool M "
                  "Toolbox decompilation. Missing:")
            for rel in missing:
                print("  - %s" % rel)
            continue
        patch_one_project(root)
        print()
        print("Options: [1] patch another folder  "
              "[Enter/0 = exit]")
        choice = ask("Select: ")
        if choice == "1":
            continue
        print("Bye.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
