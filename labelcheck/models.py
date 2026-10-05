"""Data shapes shared by the reader, the comparison rules and the screens."""

from enum import Enum

from pydantic import BaseModel, Field


class ApplicationFields(BaseModel):
    """What the applicant declared on the application form."""

    brand_name: str
    class_type: str
    alcohol_content: str
    net_contents: str
    bottler_name_address: str = ""
    country_of_origin: str = ""  # only required for imports


class LabelReading(BaseModel):
    """What the vision model read off the label image."""

    brand_name: str | None = Field(None, description="Brand name exactly as printed")
    class_type: str | None = Field(None, description="Class/type designation, e.g. 'Kentucky Straight Bourbon Whiskey'")
    alcohol_content: str | None = Field(None, description="Alcohol statement exactly as printed, e.g. '45% Alc./Vol. (90 Proof)'")
    net_contents: str | None = Field(None, description="Net contents exactly as printed, e.g. '750 mL'")
    bottler_name_address: str | None = Field(None, description="Bottler/producer name and address as printed")
    country_of_origin: str | None = Field(None, description="Country of origin statement, if any")
    warning_text: str | None = Field(None, description="Full government health warning text exactly as printed, including the heading")
    warning_heading_all_caps: bool | None = Field(None, description="True if the heading reads 'GOVERNMENT WARNING:' entirely in capital letters")
    warning_heading_bold: bool | None = Field(None, description="True if the 'GOVERNMENT WARNING:' heading is printed in bold type")
    image_quality_note: str | None = Field(None, description="Short note if glare, angle, blur or cropping made any text hard to read")


class Verdict(str, Enum):
    MATCH = "Match"
    MISMATCH = "Mismatch"
    REVIEW = "Needs review"


class FieldResult(BaseModel):
    field: str  # plain label shown to the user, e.g. "Brand name"
    expected: str
    found: str | None
    verdict: Verdict
    reason: str  # one short plain-language sentence
