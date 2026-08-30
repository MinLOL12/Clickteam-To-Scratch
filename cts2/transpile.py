"""Render parsed Clickteam Fusion events for the Scratch "Logic-Notes" sprite.

Clickteam event lines are stored as raw condition/action opcodes, each a
``(object_type, num)`` pair whose semantics are editor-version specific.  A
small, well-documented subset can be mapped to exact Scratch blocks, but most
cannot be translated reliably without the original editor / project.  Emitting
an incorrect block is worse than leaving a readable note, so this module turns
every event line into a human-readable, object-resolved description that is
written into the converted project's "Logic-Notes" sprite.

This replaces the earlier unreadable ``cond(...) => act(...)`` dump with text
like::

    when: at Start of Frame
      Player: action #2001
"""
from __future__ import annotations

from typing import List

from .mfa import Action, Condition, Frame

# Clickteam special event-object type markers.
SYS = -1          # System ("Special") object

# System "Start of frame" event line.
COND_START_OF_FRAME = -1


def _obj_name(frame: Frame, object_info: int) -> str:
    if object_info == 0:
        return "System"
    for it in frame.items:
        if it.handle == object_info:
            return it.name or f"Object{object_info}"
    return f"Object{object_info}"


def describe_condition(cond: Condition, frame: Frame) -> str:
    if cond.object_type == SYS and cond.num == COND_START_OF_FRAME:
        return "at Start of Frame"
    name = _obj_name(frame, cond.object_info)
    return f"{name}: condition #{cond.num}"


def describe_action(act: Action, frame: Frame) -> str:
    name = _obj_name(frame, act.object_info)
    return f"{name}: action #{act.num}"


def describe_group(group, frame: Frame, indent: str = "  ") -> List[str]:
    """Human-readable lines for one event group (for Logic-Notes)."""
    out = []
    if group.conditions:
        out.append(indent + "when: " + " and ".join(
            describe_condition(c, frame) for c in group.conditions))
    else:
        out.append(indent + "when: (always)")
    for a in group.actions:
        out.append(indent + describe_action(a, frame))
    return out


def describe_frame_events(frame: Frame) -> List[str]:
    """Return readable lines for every event group in a frame."""
    out = []
    for idx, group in enumerate(frame.event_groups, start=1):
        out.append(f"event {idx}:")
        out.extend(describe_group(group, frame))
    return out
