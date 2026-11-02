"""The design spec: what the Layout Designer agent writes and the renderer draws (ported from the skill's spec)."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

# family → allowed variants (first = default). `custom` takes free markup in `html`.
LAYOUTS: dict[str, tuple[str, ...]] = {
    "vertical_list": ("number", "icon"),
    "horizontal_timeline": ("number", "icon"),
    "comparison_cards": ("outline", "tint"),
    "card_list": ("check", "number"),
    "cell_table": (),
    "column_grid": (),
    "custom": (),
}
# What each content shape suits (for the agents' prompts).
LAYOUT_GUIDE = {
    "vertical_list": "steps or a sequence (3-6 items), or a list of parts with a short explanation each",
    "horizontal_timeline": "a sequence over time or stages (3-6 short items)",
    "comparison_cards": "two or three things compared side by side (columns with bullet points)",
    "card_list": "a checklist, tips or do's (3-7 one-line items)",
    "cell_table": "attributes compared across items (a header row + rows)",
    "column_grid": "types or parts (2-4 columns), or term / formula / example",
    "custom": "any other shape the designer asks for (staircase, pyramid, hub); markup in `html`",
}


class Item(BaseModel):
    heading: str = Field("", max_length=120)
    body: str = Field("", max_length=400)
    icon: str | None = Field(None, description="Tabler icon name, e.g. 'shield-check' (search_icons finds names)")


class Column(BaseModel):
    title: str = Field(max_length=120)
    body: str = Field("", max_length=400)
    items: list[str] = Field(default_factory=list, max_length=8)


class Table(BaseModel):
    header: list[str] = Field(min_length=2, max_length=5)
    rows: list[list[str]] = Field(min_length=1, max_length=8)


class Spec(BaseModel):
    title: str = Field(min_length=1, max_length=120, description="fits one line in the title bar")
    subtitle: str | None = Field(None, max_length=240)
    layout: str = Field(description="family or family/variant, e.g. 'vertical_list/number'")
    items: list[Item] = Field(default_factory=list, max_length=8)
    columns: list[Column] = Field(default_factory=list, max_length=4)
    table: Table | None = None
    takeaway: str | None = Field(None, max_length=240)
    html: str | None = Field(None, description="custom layout body markup (house classes, data-layer names)")
    css: str | None = Field(None, max_length=4000, description="small per-design CSS fixes from the critic")

    @property
    def family(self) -> str:
        return self.layout.partition("/")[0]

    @property
    def variant(self) -> str:
        v = self.layout.partition("/")[2]
        return v or (LAYOUTS.get(self.family) or ("",))[0]

    @model_validator(mode="after")
    def _check(self) -> "Spec":
        fam, _, var = self.layout.partition("/")
        if fam not in LAYOUTS:
            raise ValueError(f"layout family {fam!r} is unknown; use one of {', '.join(LAYOUTS)}")
        if var and var not in LAYOUTS[fam]:
            raise ValueError(f"{fam} has no variant {var!r}; variants: {', '.join(LAYOUTS[fam]) or 'none'}")
        if fam in {"vertical_list", "horizontal_timeline", "card_list"} and not 2 <= len(self.items) <= 8:
            raise ValueError(f"{fam} needs 2-8 items")
        if fam in {"comparison_cards", "column_grid"} and not 2 <= len(self.columns) <= 4:
            raise ValueError(f"{fam} needs 2-4 columns")
        if fam == "cell_table":
            if not self.table:
                raise ValueError("cell_table needs `table`")
            if any(len(r) != len(self.table.header) for r in self.table.rows):
                raise ValueError("every table row needs one cell per header column")
        if fam == "custom" and not (self.html or "").strip():
            raise ValueError("custom layout needs `html`")
        return self
