"""Explore Kindle app's accessibility tree — focus on text content extraction.

Investigates whether the content AXGenericElement exposes full page text
via AXValue, parameterized text attributes, or VoiceOver rotor navigation.

Usage:
    .venv/bin/python scripts/explore_kindle_accessibility.py

Make sure Kindle is open with a book displayed.
"""

import subprocess
import sys

import ApplicationServices as AS


def get_kindle_pid():
    result = subprocess.run(["pgrep", "-x", "Kindle"], capture_output=True, text=True)
    if result.returncode != 0:
        print("Kindle is not running", file=sys.stderr)
        sys.exit(1)
    return int(result.stdout.strip().split()[0])


def get_attr(element, name):
    err, val = AS.AXUIElementCopyAttributeValue(element, name, None)
    return val if err == 0 else None


def get_attrs(element):
    err, attrs = AS.AXUIElementCopyAttributeNames(element, None)
    return list(attrs) if err == 0 and attrs else []


def get_parameterized_attrs(element):
    err, attrs = AS.AXUIElementCopyParameterizedAttributeNames(element, None)
    return list(attrs) if err == 0 and attrs else []


def find_content_element(element, depth=0, max_depth=15):
    """Find the AXGenericElement that contains book text."""
    role = get_attr(element, "AXRole")
    value = get_attr(element, "AXValue")
    identifier = get_attr(element, "AXIdentifier")

    # The content element has AXCustomRotors with Link+Heading and a text AXValue
    if role == "AXGenericElement" and value and isinstance(value, str) and len(value) > 50:
        return element

    children = get_attr(element, "AXChildren")
    if children and depth < max_depth:
        for child in children:
            result = find_content_element(child, depth + 1, max_depth)
            if result:
                return result
    return None


def main():
    pid = get_kindle_pid()
    print(f"Kindle PID: {pid}")

    app = AS.AXUIElementCreateApplication(pid)
    windows = get_attr(app, "AXWindows")
    if not windows:
        print("No windows found")
        return

    win = windows[0]

    # Find the content element
    content = find_content_element(win)
    if not content:
        print("Could not find content element")
        return

    print("\n=== CONTENT ELEMENT FOUND ===")

    # Dump all standard attributes
    print("\nStandard attributes:")
    for attr in sorted(get_attrs(content)):
        val = get_attr(content, attr)
        if val is not None:
            s = str(val)
            if len(s) > 300:
                s = s[:300] + f"... ({len(s)} total)"
            print(f"  {attr} = {s}")

    # Check parameterized attributes — these are the gold mine for text
    print("\nParameterized attributes:")
    p_attrs = get_parameterized_attrs(content)
    if p_attrs:
        for pa in sorted(p_attrs):
            print(f"  {pa}")
    else:
        print("  (none)")

    # Try to read AXValue (the main text)
    value = get_attr(content, "AXValue")
    if value:
        print(f"\n=== AXValue ({len(value)} chars) ===")
        print(value[:2000])
        if len(value) > 2000:
            print(f"\n... truncated, {len(value)} total chars")

    # Try AXNumberOfCharacters
    nchars = get_attr(content, "AXNumberOfCharacters")
    if nchars:
        print(f"\nAXNumberOfCharacters = {nchars}")

    # Try parameterized text reads if available
    if "AXStringForRange" in p_attrs:
        print("\n=== AXStringForRange available! Trying to read full text... ===")
        # Try reading first 5000 chars
        try:
            from CoreFoundation import CFRangeMake
            total = nchars or 10000
            chunk_size = min(total, 5000)
            r = CFRangeMake(0, chunk_size)
            err, text = AS.AXUIElementCopyParameterizedAttributeValue(
                content, "AXStringForRange", r, None
            )
            if err == 0 and text:
                print(f"Got {len(text)} chars:")
                print(text[:2000])
                if len(text) > 2000:
                    print(f"\n... truncated, {len(text)} total chars")
            else:
                print(f"Error reading AXStringForRange: {err}")
        except Exception as e:
            print(f"Exception: {e}")

    if "AXAttributedStringForRange" in p_attrs:
        print("\nAXAttributedStringForRange also available (rich text with formatting)")

    # Check for VoiceOver-specific attributes
    rotors = get_attr(content, "AXCustomRotors")
    if rotors:
        print(f"\nCustom rotors: {rotors}")

    # Try the parent chain to see if there's a web area
    print("\n=== Checking for web content ===")
    parent = get_attr(content, "AXParent")
    depth = 0
    while parent and depth < 10:
        role = get_attr(parent, "AXRole")
        subrole = get_attr(parent, "AXSubrole")
        print(f"  Parent [{depth}]: {role} (subrole={subrole})")
        if role in ("AXWebArea", "AXScrollArea"):
            print("  ** Found web/scroll area!")
            web_attrs = get_attrs(parent)
            print(f"  Web area attrs: {web_attrs}")
        parent = get_attr(parent, "AXParent")
        depth += 1


if __name__ == "__main__":
    main()
