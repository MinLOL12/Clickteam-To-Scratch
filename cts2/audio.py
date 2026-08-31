"""Temporary audio-export policy.

Sound extraction is deliberately off while the visual and event conversion
paths are stabilised.  Keeping the switch in one place makes it explicit that
this is intentional and lets a later audio implementation opt back in without
changing the readers' routing logic.
"""

# Do not decompress, decode, retain, or export sound payloads.
EXTRACTION_ENABLED = False

DISABLED_NOTE = (
    "Audio extraction is currently disabled; this export does not include sounds."
)
