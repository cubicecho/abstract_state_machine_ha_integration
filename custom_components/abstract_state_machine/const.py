"""Constants for Abstract State Machine."""
from __future__ import annotations

DOMAIN = "abstract_state_machine"

# Bus event fired on every change of a machine's state.
EVENT_TRANSITION = f"{DOMAIN}_transition"

# Config entry (one person or thing): where its machines' entities live.
CONF_PLACEMENT = "placement"
PLACEMENT_VIRTUAL = "virtual_device"  # a new device named after the entry
PLACEMENT_EXISTING = "existing_device"  # a device the user already has
PLACEMENT_NONE = "none"  # no device: plain helper entities
PLACEMENTS = (PLACEMENT_VIRTUAL, PLACEMENT_EXISTING, PLACEMENT_NONE)
CONF_DEVICE_ID = "device_id"

# Subentry (one machine).
SUBENTRY_MACHINE = "machine"
CONF_STATES = "states"
CONF_FALLBACK = "fallback"
CONF_PERSON = "person"
CONF_MAX_FLIPS = "max_flips"

# State attributes.
ATTR_PREVIOUS = "previous"
ATTR_REASON = "reason"
ATTR_CHANGED_AT = "changed_at"
ATTR_MANUAL = "manual"
ATTR_PERSON = "person"
ATTR_FROM = "from"
ATTR_TO = "to"
ATTR_MACHINE = "machine"
ATTR_RULES_STATE = "rules_state"  # what the condition rules say now
ATTR_OVERRIDE = "override"  # a manual or trigger-rule state is holding against them
ATTR_OVERRIDE_UNTIL = "override_until"
ATTR_FLIP_LIMITED = "flip_limited"

SERVICE_SET_STATE = "set_state"
SERVICE_CLEAR_OVERRIDE = "clear_override"
