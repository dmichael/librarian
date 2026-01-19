"""DRM failure diagnosis for actionable error messages.

Diagnosis is stored in Calibre's *drm_diagnosis custom column.
The write_diagnosis_file function has been removed - use Calibre columns instead.
"""

from dataclasses import dataclass, asdict


@dataclass
class DRMDiagnosis:
    """Structured diagnosis of a DRM failure."""

    drm_type: str
    source_type: str  # "physical" or "mac"
    keys_available: bool
    decryptable: bool
    action: str
    explanation: str
    raw_output: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {k: v for k, v in asdict(self).items() if v is not None}


def diagnose_drm_failure(calibre_output: str, source_type: str = "physical") -> DRMDiagnosis:
    """Analyze DRM failure and return structured diagnosis.

    Parses DeDRM plugin output to determine why decryption failed
    and what action the user can take.

    Args:
        calibre_output: Raw output from Calibre/DeDRM
        source_type: "physical" for Kindle device, "mac" for Kindle for Mac
    """
    output_lower = calibre_output.lower()

    # Check for key patterns in DeDRM output
    has_serial = "found 1 keys to try" in output_lower or "using serial" in output_lower
    has_mac_keys = "k4mac" in output_lower and "found" in output_lower
    wrong_key = "incorrect padding" in output_lower or "wrong key" in output_lower
    no_mac_keys = "no k4mac kindle-info" in output_lower
    ultimately_failed = "ultimately failed to decrypt" in output_lower
    has_drm = "has drm and cannot be converted" in output_lower

    # Determine what keys were available
    keys_available = has_serial or has_mac_keys

    # === PHYSICAL KINDLE PATTERNS ===
    if source_type == "physical":
        # Pattern: Serial key found but decryption failed
        # Could be: ACCOUNT_SECRET DRM, key mismatch, KFX compatibility, or corrupted files
        if has_serial and (wrong_key or ultimately_failed):
            return DRMDiagnosis(
                drm_type="DEVICE_KEY_MISMATCH",
                source_type=source_type,
                keys_available=True,
                decryptable=False,
                action="Use screenshot capture workflow: see docs/kindle-screenshot-capture.md",
                explanation=(
                    "Device serial key found but decryption failed. "
                    "Possible causes: account-bound DRM (not tied to device serial), "
                    "KFX format compatibility, or key mismatch. "
                    "Screenshot capture is the reliable fallback."
                ),
                raw_output=calibre_output[:1000] if calibre_output else None,
            )

        # Pattern: No serial configured
        if not has_serial and ultimately_failed:
            return DRMDiagnosis(
                drm_type="UNKNOWN",
                source_type=source_type,
                keys_available=False,
                decryptable=False,
                action="Configure kindle_serial in config/settings.yaml",
                explanation=(
                    "No device serial configured. Add your Kindle device serial number "
                    "to enable DRM removal for physical Kindle books."
                ),
            )

    # === KINDLE FOR MAC PATTERNS ===
    if source_type == "mac":
        # Pattern: Mac keys failed
        if has_mac_keys and (wrong_key or ultimately_failed):
            return DRMDiagnosis(
                drm_type="ACCOUNT_SECRET",
                source_type=source_type,
                keys_available=True,
                decryptable=False,
                action="Use screenshot capture workflow: see docs/kindle-screenshot-capture.md",
                explanation=(
                    "Kindle for Mac keys could not decrypt this book. "
                    "The book may use account-bound DRM that requires different keys. "
                    "Use the screenshot capture workflow as fallback."
                ),
            )

        # Pattern: No Mac keys found
        if no_mac_keys or (not has_mac_keys and ultimately_failed):
            return DRMDiagnosis(
                drm_type="UNKNOWN",
                source_type=source_type,
                keys_available=False,
                decryptable=False,
                action="Ensure Kindle for Mac is signed in; DeDRM extracts keys automatically",
                explanation=(
                    "No Kindle for Mac keys found. Make sure Kindle for Mac is installed, "
                    "signed in to your Amazon account, and has downloaded the book. "
                    "DeDRM should extract keys automatically."
                ),
            )

    # === COMMON PATTERNS ===
    # Pattern: Book added but still has DRM (conversion will fail)
    if has_drm:
        return DRMDiagnosis(
            drm_type="KFX_DRM",
            source_type=source_type,
            keys_available=keys_available,
            decryptable=False,
            action="Check DeDRM plugin version; may need KFX Input plugin update",
            explanation=(
                "Book was imported but still has DRM. This often happens with newer "
                "KFX format books. Ensure DeDRM and KFX Input plugins are up to date."
            ),
        )

    # Pattern: Keys available but still failed
    if keys_available and ultimately_failed:
        return DRMDiagnosis(
            drm_type="UNKNOWN",
            source_type=source_type,
            keys_available=True,
            decryptable=False,
            action="Try screenshot capture workflow as fallback",
            explanation=(
                f"Decryption failed despite having keys ({source_type}). "
                "The book may use a different DRM scheme than expected."
            ),
            raw_output=calibre_output[:500] if len(calibre_output) > 500 else calibre_output,
        )

    # Unknown failure pattern
    return DRMDiagnosis(
        drm_type="UNKNOWN",
        source_type=source_type,
        keys_available=keys_available,
        decryptable=False,
        action="Check Calibre logs for details; may need manual investigation",
        explanation=f"Unable to determine specific DRM failure reason for {source_type} source.",
        raw_output=calibre_output[:500] if len(calibre_output) > 500 else calibre_output,
    )


def print_diagnosis(diagnosis: DRMDiagnosis, book_name: str):
    """Print a human-readable diagnosis to stdout."""
    print(f"\n  DRM Diagnosis for {book_name}:")
    print(f"    Type: {diagnosis.drm_type}")
    print(f"    Source: {diagnosis.source_type}")
    print(f"    Keys available: {diagnosis.keys_available}")
    print(f"    Action: {diagnosis.action}")
    print(f"    Details: {diagnosis.explanation}")
