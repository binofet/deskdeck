"""Pure preview of calibrated rectangular seams; no discovery or activation.

Coordinates are top-left, x-right/y-down, in Deskflow's actual coordinate space.
Layout regions use the common Dell panel's normalized [0, 1] rectangle. A region
binding maps that pane affinely onto a measured OS viewport. Mirroring, rotation
and non-axis-aligned mappings are deliberately unsupported.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
import re
import struct
from typing import Iterable, Mapping
import unicodedata

from .layouts import get_layout


class GeometryError(ValueError):
    pass


def _number(value) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float, Fraction)):
        raise GeometryError("Finite numeric geometry is required")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GeometryError("Finite numeric geometry is required")
        exact = Fraction(str(value))
        simple = exact.limit_denominator(1_000_000)
        # Recover catalog ratios such as 1/3 without snapping tiny positive spans
        # to zero. Exact Fraction inputs never undergo this float normalization.
        if simple != 0 and abs(simple - exact) <= Fraction(1, 10**14):
            return simple
        return exact
    return Fraction(value)


@dataclass(frozen=True)
class Rect:
    x: Fraction
    y: Fraction
    width: Fraction
    height: Fraction

    def __post_init__(self):
        for field in ("x", "y", "width", "height"):
            object.__setattr__(self, field, _number(getattr(self, field)))
        if self.width <= 0 or self.height <= 0:
            raise GeometryError("Rectangles must have positive area")

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height

    def contains(self, other: Rect) -> bool:
        return self.x <= other.x < other.right <= self.right and self.y <= other.y < other.bottom <= self.bottom

    def overlaps(self, other: Rect) -> bool:
        return max(self.x, other.x) < min(self.right, other.right) and max(self.y, other.y) < min(self.bottom, other.bottom)


@dataclass(frozen=True)
class DisplayGeometry:
    display_id: str
    bounds: Rect | None
    rotation: int | None = None
    mirrored: bool | None = None


@dataclass(frozen=True)
class HostGeometry:
    host_id: str
    computer_name: str
    desktop: Rect | None
    displays: tuple[DisplayGeometry, ...] = ()
    layout_id: str | None = None
    complete: bool = False
    coordinate_space_verified: bool = False


@dataclass(frozen=True)
class RegionBinding:
    region_id: str
    host_id: str
    display_id: str
    viewport: Rect | None = None
    mapping_verified: bool = False


@dataclass(frozen=True)
class Blocker:
    code: str
    regions: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Seam:
    first: str
    second: str
    side: str
    kind: str  # inter_host, same_host_internal, same_input_internal


@dataclass(frozen=True)
class Link:
    source_host: str
    source_name: str
    side: str
    source_percent: tuple[str, str]
    target_host: str
    target_name: str
    target_percent: tuple[str, str]
    source_region: str
    target_region: str


@dataclass(frozen=True)
class RoutePlan:
    layout_id: str
    links: tuple[Link, ...]
    seams: tuple[Seam, ...]
    blockers: tuple[Blocker, ...]

    @property
    def ready(self) -> bool:
        """Provided geometry is representable; never runtime/input acceptance."""
        return not self.blockers

    def preview_links(self) -> str | None:
        if not self.ready:
            return None
        lines = ["# PREVIEW ONLY: measured geometry; physical edges remain unqualified.", "section: links"]
        names = sorted({link.source_name for link in self.links}, key=str.casefold)
        for name in names:
            lines.append("    " + name + ":")
            for link in self.links:
                if link.source_name == name:
                    lines.append(f"        {link.side}({','.join(link.source_percent)}) = "
                                 f"{link.target_name}({','.join(link.target_percent)})")
        return "\n".join(lines + ["end", ""])

    def to_dict(self) -> dict:
        return {
            "layout_id": self.layout_id, "ready": self.ready,
            "evidence": "caller_supplied_geometry_only", "activation_available": False,
            "physical_edges_verified": False, "hotkeys_unchanged": True,
            "links": [dict(link.__dict__) for link in self.links],
            "seams": [dict(seam.__dict__) for seam in self.seams],
            "blockers": [dict(blocker.__dict__) for blocker in self.blockers],
            "preview_text": self.preview_links(),
        }


_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_OPPOSITE = {"left": "right", "right": "left", "up": "down", "down": "up"}


def _id(value) -> bool:
    return isinstance(value, str) and bool(_NAME.fullmatch(value))


def _display_id(value) -> bool:
    # OS IDs are opaque: Windows names can contain backslashes, for example.
    # They never enter Deskflow syntax and must not be renamed into host IDs.
    return (isinstance(value, str) and 0 < len(value) <= 256
            and all(not unicodedata.category(c).startswith("C") for c in value))


def _percent(value: Fraction) -> str:
    # ConfigReadContext::parseInterval uses strtod, then stores float32 fractions.
    # Keep shared endpoints deterministic; check representability after rounding.
    sign = "-" if value < 0 else ""
    value = abs(value) * 100
    scale = 10**12
    rounded = (value.numerator * scale * 2 // value.denominator + 1) // 2
    whole, decimal = divmod(rounded, scale)
    return sign + str(whole) + (("." + f"{decimal:012d}".rstrip("0")) if decimal else "")


def _float_interval(values: tuple[str, str]) -> tuple[float, float]:
    result = tuple(struct.unpack("f", struct.pack("f", float(value) / 100))[0] for value in values)
    if not 0 <= result[0] < result[1] <= 1:
        raise GeometryError("Deskflow float intervals collapse or exceed the edge")
    return result


def _seams(a: Rect, b: Rect):
    vertical = (max(a.y, b.y), min(a.bottom, b.bottom))
    horizontal = (max(a.x, b.x), min(a.right, b.right))
    if vertical[0] < vertical[1]:
        if a.right == b.x:
            return "right", vertical
        if a.x == b.right:
            return "left", vertical
    if horizontal[0] < horizontal[1]:
        if a.bottom == b.y:
            return "down", horizontal
        if a.y == b.bottom:
            return "up", horizontal
    return None  # A corner is not a seam.


def _map_segment(region: Rect, viewport: Rect, side: str, span):
    if side in {"left", "right"}:
        line = viewport.x if side == "left" else viewport.right
        values = tuple(viewport.y + (point - region.y) * viewport.height / region.height for point in span)
    else:
        line = viewport.y if side == "up" else viewport.bottom
        values = tuple(viewport.x + (point - region.x) * viewport.width / region.width for point in span)
    return line, values


def _outer_interval(host: HostGeometry, side: str, line, values):
    desktop = host.desktop
    expected = {"left": desktop.x, "right": desktop.right, "up": desktop.y, "down": desktop.bottom}[side]
    if line != expected:
        raise GeometryError("Physical seam is not a whole-computer boundary")
    origin, length = ((desktop.y, desktop.height) if side in {"left", "right"}
                      else (desktop.x, desktop.width))
    # Server::mapToFraction samples integer cursor coordinates at n + 0.5.
    # A mathematically positive segment may still contain no reachable sample.
    samples = tuple(math.ceil(value - Fraction(1, 2)) for value in values)
    if samples[0] >= samples[1]:
        raise GeometryError("Seam contains no Deskflow cursor sample")
    interval = tuple(_percent((value - origin) / length) for value in values)
    converted = _float_interval(interval)
    converted_values = tuple(origin + Fraction.from_float(value) * length for value in converted)
    converted_samples = tuple(math.ceil(value - Fraction(1, 2)) for value in converted_values)
    # A sub-half-unit rounding error can still cross n + 0.5 and remove the
    # sole reachable sample, or assign a sample to the wrong adjacent route.
    # Preserve the exact half-open interval's integer sample membership.
    if converted_samples != samples:
        raise GeometryError("Float link rounding changes reachable cursor samples")
    if any(abs(Fraction.from_float(rounded) - (value - origin) / length) * length >= Fraction(1, 2)
           for rounded, value in zip(converted, values)):
        raise GeometryError("Float link rounding exceeds half a cursor unit")
    return interval


def plan_edge_routes(layout: Mapping | str, bindings: Iterable[RegionBinding] = (),
                     hosts: Iterable[HostGeometry] = ()) -> RoutePlan:
    """Prepare reciprocal percentage links or explicit blockers; performs no I/O.

    Accepts a catalog ID or layouts.layout_catalog()/get_layout() dictionaries.
    Imports only the pure catalog, never a live adapter. Bind every visible region, including iMST
    panes sharing input_slot. Complete active-display inventories are required.
    """
    blockers, links, seams = [], [], []
    if isinstance(layout, str):
        try:
            layout = get_layout(layout)
        except ValueError:
            return RoutePlan("unknown", (), (), (Blocker("unknown_layout_geometry"),))
    layout_id = layout.get("id", "unknown") if isinstance(layout, Mapping) else "unknown"

    def block(code, regions=(), hosts=()):
        item = Blocker(code, tuple(regions), tuple(hosts))
        if item not in blockers:
            blockers.append(item)

    if not _id(layout_id) or not isinstance(layout, Mapping) or not isinstance(layout.get("regions"), list):
        return RoutePlan("unknown", (), (), (Blocker("unknown_layout_geometry"),))
    regions = {}
    panel = Rect(0, 0, 1, 1)
    slot_count = layout.get("input_slots")
    if type(slot_count) is not int or slot_count < 1:
        block("unknown_input_slot_groups")
    for item in layout["regions"]:
        if not isinstance(item, Mapping) or not _id(item.get("id")) or item["id"] in regions:
            block("invalid_or_duplicate_region")
            continue
        region_id = item["id"]
        try:
            rect = Rect(*(item.get(key) for key in ("x", "y", "width", "height")))
            slot = item.get("input_slot")
            if (type(slot) is not int or slot < 1 or (type(slot_count) is int and slot > slot_count)
                    or not panel.contains(rect)):
                raise GeometryError
            regions[region_id] = (rect, slot)
        except GeometryError:
            block("unknown_or_invalid_region_geometry", (region_id,))
    region_items = sorted(regions.items())
    if not region_items or sum((rect.width * rect.height for rect, _ in regions.values()), Fraction()) != 1:
        block("panel_coverage_incomplete")
    for index, (first_id, (first, _)) in enumerate(region_items):
        for second_id, (second, _) in region_items[index + 1:]:
            if first.overlaps(second):
                block("overlapping_regions", (first_id, second_id))

    host_map, binding_map, unusable = {}, {}, set()
    for host in hosts:
        if (not isinstance(host, HostGeometry) or not _id(host.host_id) or not _id(host.computer_name)
                or host.host_id in host_map):
            block("invalid_or_duplicate_host")
            continue
        host_map[host.host_id] = host
    names = [host.computer_name.casefold() for host in host_map.values()]
    if len(names) != len(set(names)):
        block("caseless_computer_name_collision")
    for binding in bindings:
        if (not isinstance(binding, RegionBinding) or not _id(binding.region_id)
                or not _id(binding.host_id) or not _display_id(binding.display_id)
                or binding.region_id in binding_map or binding.region_id not in regions):
            block("invalid_or_duplicate_region_binding")
            continue
        binding_map[binding.region_id] = binding
    for region_id in regions:
        binding = binding_map.get(region_id)
        if binding is None or binding.host_id not in host_map:
            block("missing_region_host_binding", (region_id,))
            unusable.add(region_id)
            continue
        host = host_map[binding.host_id]
        valid = (host.complete is True and host.coordinate_space_verified is True
                 and host.layout_id == layout_id and isinstance(host.desktop, Rect)
                 and type(host.displays) is tuple and bool(host.displays))
        displays = {}
        for display in host.displays if type(host.displays) is tuple else ():
            if (not isinstance(display, DisplayGeometry) or not _display_id(display.display_id)
                    or display.display_id in displays or not isinstance(display.bounds, Rect)
                    or type(display.rotation) is not int or display.rotation != 0 or display.mirrored is not False):
                valid = False
            else:
                displays[display.display_id] = display
        if displays and valid:
            bounds = [display.bounds for display in displays.values()]
            union = Rect(min(r.x for r in bounds), min(r.y for r in bounds),
                         max(r.right for r in bounds) - min(r.x for r in bounds),
                         max(r.bottom for r in bounds) - min(r.y for r in bounds))
            if union != host.desktop or any(a.overlaps(b) for i, a in enumerate(bounds) for b in bounds[i + 1:]):
                valid = False
        display = displays.get(binding.display_id)
        if (not valid or display is None or binding.mapping_verified is not True
                or not isinstance(binding.viewport, Rect) or not display.bounds.contains(binding.viewport)):
            block("missing_or_inconsistent_display_calibration", (region_id,), (host.host_id,))
            unusable.add(region_id)

    for index, (first_id, (first, first_slot)) in enumerate(region_items):
        for second_id, (second, second_slot) in region_items[index + 1:]:
            a, b = binding_map.get(first_id), binding_map.get(second_id)
            if a and b:
                if first_slot == second_slot and a.host_id != b.host_id:
                    block("one_input_group_has_multiple_hosts", (first_id, second_id))
                if (a.host_id == b.host_id and a.display_id == b.display_id
                        and isinstance(a.viewport, Rect) and isinstance(b.viewport, Rect) and a.viewport.overlaps(b.viewport)):
                    block("duplicate_or_overlapping_os_viewports", (first_id, second_id), (a.host_id,))
            adjacent = _seams(first, second)
            if adjacent is None or a is None or b is None:
                continue
            side, span = adjacent
            kind = ("same_input_internal" if first_slot == second_slot else
                    "same_host_internal" if a.host_id == b.host_id else "inter_host")
            seams.append(Seam(first_id, second_id, side, kind))
            if first_id in unusable or second_id in unusable:
                continue
            line_a, values_a = _map_segment(first, a.viewport, side, span)
            opposite = _OPPOSITE[side]
            line_b, values_b = _map_segment(second, b.viewport, opposite, span)
            if a.host_id == b.host_id:
                if line_a != line_b or values_a != values_b:
                    block("internal_os_seam_misaligned", (first_id, second_id), (a.host_id,))
                continue
            if first_slot == second_slot:
                continue
            try:
                source = _outer_interval(host_map[a.host_id], side, line_a, values_a)
                target = _outer_interval(host_map[b.host_id], opposite, line_b, values_b)
            except GeometryError:
                block("seam_not_representable_on_computer_edges", (first_id, second_id), (a.host_id, b.host_id))
                continue
            links.extend((Link(a.host_id, host_map[a.host_id].computer_name, side, source,
                               b.host_id, host_map[b.host_id].computer_name, target, first_id, second_id),
                          Link(b.host_id, host_map[b.host_id].computer_name, opposite, target,
                               a.host_id, host_map[a.host_id].computer_name, source, second_id, first_id)))
    for index, first in enumerate(links):
        a = _float_interval(first.source_percent)
        for second in links[index + 1:]:
            if (first.source_host, first.side) == (second.source_host, second.side):
                b = _float_interval(second.source_percent)
                if max(a[0], b[0]) < min(a[1], b[1]):
                    block("overlapping_computer_edge_routes", (first.source_region, second.source_region), (first.source_host,))
    links.sort(key=lambda link: (link.source_host, link.side, _float_interval(link.source_percent), link.target_host))
    return RoutePlan(layout_id, tuple(links), tuple(seams), tuple(blockers))
