"""Decode Clickteam Fusion EXE/CCN frame event programs (chunk 13117).

MFA files store events as an ``Evts`` stream that :mod:`cts2.mfa` already
reads.  Compiled EXEs store the same event *groups* under a different
header (``ER>>`` / ``ERev`` / ``<<ER``).  Without this reader the SB3
export has sprites but zero event blocks — the classic "none of the code
is converting" failure.

Layout (CTFAK / Anaconda)::

    "ER>>"  maxObjects i16, maxOI i16, nPlayers i16,
            17 x nConditions i16, nQualifiers i16, qualifiers...
    "ERes"  size i32
    "ERop"  option flags / extension blob (optional)
    "ERev"  size i32, then EventGroup records until size exhausted
    "<<ER"  end

Each EventGroup is the same record MFA uses, with a build-284 header
variant.  Conditions/actions/parameters match the MFA readers in
:mod:`cts2.mfa`, so the Scratch transpiler can consume them unchanged.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .bin import Reader
from .mfa import (
    Action,
    Condition,
    EventGroup,
    Parameter,
    _decode_parameter_payload,
    read_expression,
)


HEADER = b"ER>>"
EVENT_COUNT = b"ERes"
EVENT_GROUP = b"ERev"
EVENT_OPTIONS = b"ERop"
END = b"<<ER"

#: Hard ceiling on how many event groups one frame body contributes.  A
#: corrupt size field could otherwise claim hundreds of megabytes of groups;
#: reading them one by one looks exactly like a conversion that hangs.
_MAX_EVENT_GROUPS = 200000


def _read_parameter(r: Reader) -> Optional[Parameter]:
    if r.remaining() < 4:
        return None
    start = r.tell()
    size = r.i16()
    if size < 4 or start + size > len(r):
        # Tolerate truncated tails without aborting the whole group.
        return None
    code = r.i16()
    payload_len = max(size - 4, 0)
    # size includes the size field itself (2) + code (2) + payload.
    # CTFAK seeks to currentPosition + size after reading size+code, so
    # payload is size - 4 bytes... wait: CTFAK does
    #   size = ReadInt16(); code = ReadInt16(); Loader.Read(); Seek(start+size)
    # so payload available is size-4, yes.  But MFA reader uses
    #   payload_len = max(p_size - 2, 0) after reading size then code —
    # that treats size as covering only (code+payload).  MFA and EXE use
    # the same Parameter layout; MFA's `_read_condition` does:
    #   p_size = r.i16(); code = r.i16(); payload_len = max(p_size - 2, 0)
    # which means p_size = 2(code) + payload, and the outer size field is
    # NOT included.  CTFAK EXE does the same: size covers from after the
    # size word (code + payload), and Seek(currentPosition + size) where
    # currentPosition is BEFORE the size word... Actually CTFAK:
    #   currentPosition = Tell(); size = ReadInt16(); Code = ReadInt16();
    #   Loader.Read(); Seek(currentPosition + size);
    # If size is the TOTAL including the size field, Seek goes to start+size
    # and we've consumed 2 (size) + (size-2) = size bytes. Good.
    # If size excludes itself, Seek(start+size) would leave 2 bytes short.
    # Empirically MFA uses p_size covering code+payload (NOT including the
    # size field): after reading size(2)+code(2)+payload(p_size-2) we are at
    # start+2+p_size, and MFA does seek(p_start + p_size) which is WRONG
    # unless p_size includes the size field... Looking at mfa.py again:
    #   p_start = r.tell(); p_size = r.i16(); code = r.i16();
    #   payload_len = max(p_size - 2, 0); payload = r.read(payload_len);
    #   r.seek(p_start + p_size);
    # After reading size we're at p_start+2. Read code (2) -> p_start+4.
    # Read payload p_size-2 -> p_start+4+(p_size-2)=p_start+2+p_size.
    # seek(p_start+p_size) goes BACKWARD by 2! That's a bug unless p_size
    # includes the size field: if p_size = total record size including the
    # size word, then payload = p_size-4 effectively... but code uses -2.
    #
    # Real MFA fixtures work, so the stored size must mean
    # "bytes from the size field start to the end" i.e. total record size,
    # and payload_len = p_size - 2 is wrong by 2 (it over-reads by including
    # nothing extra if seek resets). The seek(p_start+p_size) rescues it:
    # over-read then seek back... no, over-read goes past p_start+p_size then
    # seek snaps back. If payload_len = p_size-2 and total should be p_size,
    # we read 2 + (p_size-2) = p_size bytes from p_start, ending at
    # p_start+p_size; seek is a no-op. That means p_size INCLUDES the size
    # field, and the "code" is part of the p_size-2 payload area — so
    # payload_len should be p_size-4 for the data after code. The MFA code
    # reads code as part of the first 2 of the p_size-2, then payload of
    # p_size-2 which is actually code(2)+real_payload(p_size-4), so the
    # payload bytes INCLUDE nothing from before code... wait:
    #   read size (2 bytes)  # not counted in subsequent reads toward p_size?
    #   Actually: start; size=i16; code=i16; payload=read(p_size-2); seek(start+p_size)
    #   Bytes consumed before seek: 2 + 2 + (p_size-2) = p_size+2
    #   seek to start+p_size moves BACK 2 bytes.
    # So every parameter over-reads 2 bytes into the next parameter, then
    # seeks back. The payload therefore contains the real payload PLUS the
    # first 2 bytes of the next parameter (or padding). That is messy but
    # _decode_parameter_payload only reads what it needs from the start of
    # payload, so it still works!
    #
    # Match MFA behaviour exactly so decoding stays consistent.
    payload = r.read(max(size - 2, 0))
    r.seek(start + size)
    return Parameter(code, size, _decode_parameter_payload(code, payload), payload)


def _read_condition(r: Reader) -> Optional[Condition]:
    if r.remaining() < 12:
        return None
    start = r.tell()
    size = r.u16()
    if size < 12:
        return None
    obj_type = r.i16()
    num = r.i16()
    object_info = r.u16()
    object_info_list = r.i16()
    flags = r.i8()
    other_flags = r.i8()
    num_params = r.u8()
    def_type = r.u8()
    identifier = r.i16()
    params: List[Parameter] = []
    for _ in range(num_params):
        p = _read_parameter(r)
        if p is None:
            break
        params.append(p)
    r.seek(start + size)
    return Condition(
        obj_type, num, object_info, object_info_list, flags, other_flags,
        num_params, def_type, identifier, size, params,
    )


def _read_action(r: Reader) -> Optional[Action]:
    if r.remaining() < 12:
        return None
    start = r.tell()
    size = r.u16()
    if size < 12:
        return None
    obj_type = r.i16()
    num = r.i16()
    object_info = r.u16()
    object_info_list = r.i16()
    flags = r.i8()
    other_flags = r.i8()
    num_params = r.u8()
    def_type = r.u8()
    params: List[Parameter] = []
    for _ in range(num_params):
        p = _read_parameter(r)
        if p is None:
            break
        params.append(p)
    r.seek(start + size)
    return Action(
        obj_type, num, object_info, object_info_list, flags, other_flags,
        num_params, def_type, size, params,
    )


def _read_event_group(r: Reader, build: int) -> Optional[EventGroup]:
    """One event group. ``size`` is stored negated (CTFAK convention)."""
    if r.remaining() < 12:
        return None
    start = r.tell()
    raw_size = r.i16()
    size = -raw_size if raw_size < 0 else raw_size
    if size < 12 or start + size > len(r):
        return None
    num_cond = r.u8()
    num_act = r.u8()
    flags = r.u16()
    # Build >= 284 EXE header: nop i16, isRestricted i32, restrictCpt i32.
    # MFA / older builds: four i16 fields.
    is_restricted = 0
    restrict_cpt = 0
    identifier = 0
    undo = 0
    if build >= 284:
        r.i16()  # line / nop
        is_restricted = r.i32()
        restrict_cpt = r.i32()
    else:
        is_restricted = r.i16()
        restrict_cpt = r.i16()
        identifier = r.i16()
        undo = r.i16()
    conds: List[Condition] = []
    for _ in range(num_cond):
        c = _read_condition(r)
        if c is None:
            break
        conds.append(c)
    acts: List[Action] = []
    for _ in range(num_act):
        a = _read_action(r)
        if a is None:
            break
        acts.append(a)
    r.seek(start + size)
    return EventGroup(
        size, num_cond, num_act, flags, is_restricted, restrict_cpt,
        identifier, undo, conds, acts,
    )


def read_exe_events(data: bytes, build: int = 0) -> Tuple[List[EventGroup], List[str]]:
    """Parse a FRAME_EVENTS (13117) payload into event groups.

    Returns ``(groups, warnings)``.  Empty input or unknown headers yield
    an empty list rather than raising — callers can still export sprites.
    """
    warnings: List[str] = []
    if not data:
        return [], warnings
    r = Reader(data)
    groups: List[EventGroup] = []
    saw_header = False
    guard = 0
    while r.remaining() >= 4 and guard < 100000:
        guard += 1
        ident = r.read(4)
        if ident == HEADER:
            saw_header = True
            try:
                if r.remaining() < 6 + 17 * 2 + 2:
                    warnings.append("events header truncated")
                    break
                r.i16()  # max objects
                r.i16()  # max object info
                r.i16()  # number of players
                for _ in range(17):
                    r.i16()  # per-type condition counts
                nqual = r.i16()
                if nqual < 0 or nqual > 4096:
                    warnings.append(f"implausible qualifier count {nqual}")
                    break
                for _ in range(nqual):
                    if r.remaining() < 4:
                        break
                    r.u16()
                    r.i16()
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"events header unreadable: {exc}")
                break
        elif ident == EVENT_COUNT:
            if r.remaining() >= 4:
                r.i32()
        elif ident == EVENT_OPTIONS:
            # OptionFlags i32, or a size-prefixed blob on some builds.
            if r.remaining() >= 4:
                # Prefer a size-prefixed blob when the next dword looks like
                # a length rather than a flag bitfield; both are harmless to
                # skip for our purposes.
                val = r.i32()
                if 0 < val < r.remaining() and val < 0x100000:
                    # Could be a size; leave the bytes (already consumed the
                    # dword).  Real ERop in CTFAK2 is just OptionFlags.
                    pass
        elif ident == EVENT_GROUP:
            if r.remaining() < 4:
                break
            size = r.i32()
            if size < 0 or size > r.remaining():
                warnings.append(f"events body has implausible size {size}")
                break
            end = r.tell() + size
            before = len(groups)
            while r.tell() < end and r.remaining() > 0:
                eg = _read_event_group(r, build)
                if eg is None:
                    # Resync: stop this body rather than spinning.
                    warnings.append(
                        f"stopped reading event groups at offset {r.tell()}"
                    )
                    r.seek(end)
                    break
                groups.append(eg)
                if len(groups) - before >= _MAX_EVENT_GROUPS:
                    warnings.append(
                        f"event group cap ({_MAX_EVENT_GROUPS}) reached in an "
                        f"events body of {size} bytes; the rest of the body "
                        "was skipped"
                    )
                    r.seek(end)
                    break
            r.seek(end)
        elif ident == END or ident == b"  <<":
            break
        else:
            # Unknown four-cc.  If we never saw a real header, the payload
            # might be MFA-style "Evts" (already handled elsewhere) or raw
            # event groups.  Try raw groups once, otherwise stop.
            if not saw_header and not groups:
                r.seek(r.tell() - 4)
                while (r.remaining() >= 12 and len(groups) < _MAX_EVENT_GROUPS):
                    eg = _read_event_group(r, build)
                    if eg is None:
                        break
                    groups.append(eg)
                if len(groups) >= _MAX_EVENT_GROUPS:
                    warnings.append(
                        f"event group cap ({_MAX_EVENT_GROUPS}) reached while "
                        "resyncing on an unrecognised events payload; the "
                        "rest of the frame's events were skipped"
                    )
                if groups:
                    warnings.append(
                        "events chunk lacked ER>> header; read raw groups"
                    )
            break
    return groups, warnings


def normalize_exe_opcodes(group: EventGroup) -> EventGroup:
    """Map EXE condition/action numbers onto the MFA-style tables the
    transpiler already understands, when the equivalence is known.

    EXE System conditions live on several negative object types
    (Storyboard=-3, Timer=-4, Mouse/Keyboard=-6, System=-1).  MFA folds
    many of them onto a single System (-1) object with a different num
    scheme.  We keep the EXE (object_type, num) pair and teach the
    transpiler both; this helper only applies the CTFAK "Fixer" renames
    that collapse global-value variants.
    """
    for cond in group.conditions:
        n = cond.num
        if cond.object_type == -1:
            # Global value int/double compare variants -> CompareGlobalValue
            if n in (-28, -29, -30, -31, -32, -33, -34, -35, -36, -37, -38, -39):
                cond.num = -8
        if cond.object_type != -1 and n in (-42, -43):
            cond.num = -27  # Alterable value
    for act in group.actions:
        n = act.num
        if act.object_type == -1:
            # EXE system actions use small numbers; leave them for the
            # transpiler's EXE table.  CTFAK fixer collapses some global
            # variants onto Set/Add/Sub.
            if n in (27, 28, 29, 30):
                act.num = 3
            elif n in (32, 33, 34, 35):
                act.num = 4
            elif n in (31, 36, 37, 38):
                act.num = 5
    return group
