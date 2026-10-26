"""Documented U5226KW panel geometry, independent of hardware control.

Coordinates start at the panel's upper-left corner, with x right and y down.
An input slot is an independently selected physical video input, not a host,
USB owner, OS display, or iMST stream. The manual numbers windows clockwise in
the four-quadrant configuration; region IDs preserve those printed numbers.
"""

__all__ = ["get_layout", "layout_catalog"]

_GUIDE = (
    "https://dl.dell.com/content/"
    "manual32544295-dell-ultrasharp-52-thunderbolt-hub-monitor-"
    "u5226kw-u5226kw-wost-user-s-guide.pdf?language=en-us"
)

# Integer pixel rectangles follow the window timings and diagrams on printed
# pages 58 and 59. Divide by the 6144 x 2560 panel only when returning a catalog.
# Each tuple is in printed Win1, Win2, ... order, not geometric scan order.
_GEOMETRIES = (
    ("equal left and right", ((0, 0, 3072, 2560), (3072, 0, 3072, 2560))),
    ("three-quarter left", ((0, 0, 4608, 2560), (4608, 0, 1536, 2560))),
    ("three-quarter right", ((0, 0, 1536, 2560), (1536, 0, 4608, 2560))),
    ("two-third left", ((0, 0, 4096, 2560), (4096, 0, 2048, 2560))),
    ("two-third right", ((0, 0, 2048, 2560), (2048, 0, 4096, 2560))),
    ("equal top and bottom", ((0, 0, 6144, 1280), (0, 1280, 6144, 1280))),
    ("853-row top and 1707-row bottom", ((0, 0, 6144, 853), (0, 853, 6144, 1707))),
    ("three equal columns", ((0, 0, 2048, 2560), (2048, 0, 2048, 2560), (4096, 0, 2048, 2560))),
    ("wide center column", ((0, 0, 1536, 2560), (1536, 0, 3072, 2560), (4608, 0, 1536, 2560))),
    ("stacked left and right half", ((0, 0, 3072, 1280), (3072, 0, 3072, 2560), (0, 1280, 3072, 1280))),
    ("left half and stacked right", ((0, 0, 3072, 2560), (3072, 0, 3072, 1280), (3072, 1280, 3072, 1280))),
    ("top half and split bottom", ((0, 0, 6144, 1280), (3072, 1280, 3072, 1280), (0, 1280, 3072, 1280))),
    ("split top and bottom half", ((0, 0, 3072, 1280), (3072, 0, 3072, 1280), (0, 1280, 6144, 1280))),
    ("four equal columns", ((0, 0, 1536, 2560), (1536, 0, 1536, 2560), (3072, 0, 1536, 2560), (4608, 0, 1536, 2560))),
    ("four quadrants", ((0, 0, 3072, 1280), (3072, 0, 3072, 1280), (3072, 1280, 3072, 1280), (0, 1280, 3072, 1280))),
)


def _regions(rectangles: tuple, slots: tuple) -> list[dict]:
    return [
        {"id": f"win{index}", "x": x / 6144, "y": y / 2560,
         "width": width / 6144, "height": height / 2560,
         "input_slot": slot}
        for index, ((x, y, width, height), slot) in enumerate(zip(rectangles, slots), 1)
    ]


def _layout(layout_id: str, label: str, kind: str, rectangles: tuple,
            slots: tuple, pages: list[int], configuration: int | None,
            notes: list[str]) -> dict:
    imst = kind in ("screen-partition", "pbp-with-screen-partition")
    return {
        "id": layout_id,
        "label": label,
        "kind": kind,
        "native_configuration": configuration,
        "regions": _regions(rectangles, slots),
        "input_slots": len(set(slots)),
        "capabilities": {
            "imst_required": imst,
            "input_families": ["DisplayPort", "Thunderbolt"] if imst else
                              ["DisplayPort", "Thunderbolt", "HDMI"],
            "hardware_control_qualified": False,
            "hardware_mode_code": None,
        },
        "source": {"url": _GUIDE, "printed_pages": pages},
        "notes": notes + [
            "Panel geometry only; OS display bounds and input/USB ownership require separate qualification.",
            "The printed configuration number is not a DDC/CI mode value.",
        ],
    }


def layout_catalog() -> list[dict]:
    """Return fresh JSON-compatible descriptions of every documented layout.

    The 15 PBP and 15 single-input Screen Partition entries share geometry, not
    physical input cardinality. A separate mixed entry has four iMST regions
    across two physical input slots. No entry grants permission to apply it.
    """
    result = [_layout(
        "fullscreen", "Fullscreen", "fullscreen", ((0, 0, 6144, 2560),),
        (1,), [58], None, ["PIP/PBP and Screen Partition are off."],
    )]
    for kind, prefix, page in (("pbp", "PBP", 58),
                               ("screen-partition", "Screen Partition", 59)):
        for number, (label, rectangles) in enumerate(_GEOMETRIES, 1):
            partition = kind == "screen-partition"
            slots = (1,) * len(rectangles) if partition else tuple(range(1, len(rectangles) + 1))
            notes = [
                "All regions are iMST streams from one DP or Thunderbolt physical input.",
                "Host GPU, OS, link bandwidth and timing support determine which streams work; HDMI is not listed for this mode.",
            ] if partition else [
                "Each numbered window has an independently selected video input slot.",
                "Equal-sized windows may duplicate an input; different-sized windows restrict duplication (guide page 57).",
            ]
            result.append(_layout(
                f"{kind}-{number:02d}", f"{prefix} Configuration {number}: {label}",
                kind, rectangles, slots, [59] if partition else [57, 58], number, notes,
            ))
    result.append(_layout(
        "pbp-with-screen-partition", "PBP with Screen Partition: two sources, four panes",
        "pbp-with-screen-partition", _GEOMETRIES[14][1], (1, 1, 2, 2), [60], None,
        ["The top row contains PC1 iMST streams 1 and 2; the bottom row contains PC2 streams 1 and 2.",
         "There are two independently selected physical inputs, not four; stream and GPU/link compatibility still require qualification.",
         "win1..win4 use Configuration 15's clockwise geometric IDs; the mixed diagram labels PCs and streams instead of Win numbers."],
    ))
    return result


def get_layout(layout_id: str) -> dict:
    """Return a fresh catalog entry, refusing unknown IDs without side effects."""
    for layout in layout_catalog():
        if layout["id"] == layout_id:
            return layout
    raise ValueError(f"Unknown layout: {layout_id!r}")
