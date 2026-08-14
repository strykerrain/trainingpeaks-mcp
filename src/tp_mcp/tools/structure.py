"""Workout structure builder, validator, and IF/TSS computation.

Converts a simplified step-based structure format into the wire format
expected by the TrainingPeaks API, including cumulative begin/end times
and polyline generation.
"""

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from tp_mcp.tools._validation import format_validation_error

logger = logging.getLogger("tp-mcp")

# Valid intensity classes for workout steps
INTENSITY_CLASSES = {"warmUp", "active", "rest", "coolDown", "other"}

# Valid primary intensity metrics
INTENSITY_METRICS = {
    "percentOfFtp",
    "percentOfThresholdHr",
    "percentOfThresholdPace",
    "percentOfMaxHr",
    "rpe",
}

# Valid distance units for distance-based workouts (e.g. swim intervals)
DISTANCE_UNITS = {"meter", "yard", "km", "mile"}


class SimpleStep(BaseModel):
    """A single workout step in the simplified input format.

    Each step must specify EITHER ``duration_seconds`` (time-based) OR
    both ``distance_value`` and ``distance_unit`` (distance-based, e.g.
    swim intervals). Rest steps inside a distance-based workout may still
    use ``duration_seconds`` (e.g. 10-second recoveries).
    """

    name: str = Field(min_length=1, max_length=100)
    type: str = Field(default="step")
    duration_seconds: int | None = Field(default=None, gt=0, le=86400)
    distance_value: float | None = Field(default=None, gt=0, le=1_000_000)
    distance_unit: str | None = Field(default=None)
    intensity_min: float = Field(ge=0, le=300)
    intensity_max: float = Field(ge=0, le=300)
    intensityClass: str = Field(default="active")  # noqa: N815
    cadence_min: float | None = Field(default=None, ge=0, le=300)
    cadence_max: float | None = Field(default=None, ge=0, le=300)

    @field_validator("intensityClass")
    @classmethod
    def check_intensity_class(cls, v: str) -> str:
        if v not in INTENSITY_CLASSES:
            valid = ", ".join(sorted(INTENSITY_CLASSES))
            raise ValueError(f"Invalid intensityClass '{v}'. Valid: {valid}")
        return v

    @field_validator("distance_unit")
    @classmethod
    def check_distance_unit(cls, v: str | None) -> str | None:
        if v is not None and v not in DISTANCE_UNITS:
            valid = ", ".join(sorted(DISTANCE_UNITS))
            raise ValueError(f"Invalid distance_unit '{v}'. Valid: {valid}")
        return v

    @model_validator(mode="after")
    def check_length_specification(self) -> "SimpleStep":
        has_duration = self.duration_seconds is not None
        has_distance = self.distance_value is not None and self.distance_unit is not None
        partial_distance = (self.distance_value is None) ^ (self.distance_unit is None)
        if partial_distance:
            raise ValueError(
                "distance_value and distance_unit must be provided together"
            )
        if has_duration and has_distance:
            raise ValueError(
                "Provide either duration_seconds or (distance_value + distance_unit), not both"
            )
        if not has_duration and not has_distance:
            raise ValueError(
                "Each step must specify either duration_seconds or (distance_value + distance_unit)"
            )
        return self

    @model_validator(mode="after")
    def check_intensity_range(self) -> "SimpleStep":
        if self.intensity_min > self.intensity_max:
            raise ValueError("intensity_min must be <= intensity_max")
        if (
            self.cadence_min is not None
            and self.cadence_max is not None
            and self.cadence_min > self.cadence_max
        ):
            raise ValueError("cadence_min must be <= cadence_max")
        return self


class SimpleRepetitionBlock(BaseModel):
    """A repetition block containing multiple steps repeated N times."""

    type: str = Field(default="repetition")
    name: str = Field(default="Repeat")
    reps: int = Field(gt=0, le=100)
    steps: list[SimpleStep] = Field(min_length=1)


class SimpleWorkoutStructure(BaseModel):
    """Top-level simplified structure input from the LLM."""

    primaryIntensityMetric: str = Field(default="percentOfFtp")  # noqa: N815
    primaryLengthMetric: Literal["duration", "distance"] = "duration"  # noqa: N815
    distance_unit: str | None = Field(default=None)
    steps: list[SimpleStep | SimpleRepetitionBlock] = Field(min_length=1)

    @field_validator("primaryIntensityMetric")
    @classmethod
    def check_metric(cls, v: str) -> str:
        if v not in INTENSITY_METRICS:
            valid = ", ".join(sorted(INTENSITY_METRICS))
            raise ValueError(f"Invalid primaryIntensityMetric '{v}'. Valid: {valid}")
        return v

    @field_validator("distance_unit")
    @classmethod
    def check_top_distance_unit(cls, v: str | None) -> str | None:
        if v is not None and v not in DISTANCE_UNITS:
            valid = ", ".join(sorted(DISTANCE_UNITS))
            raise ValueError(f"Invalid distance_unit '{v}'. Valid: {valid}")
        return v

    @model_validator(mode="after")
    def check_distance_config(self) -> "SimpleWorkoutStructure":
        if self.primaryLengthMetric == "distance" and not self.distance_unit:
            raise ValueError(
                "distance_unit is required when primaryLengthMetric is 'distance'"
            )
        return self


def _step_length_dict(step: SimpleStep) -> dict[str, Any]:
    """Length descriptor for a step's wire form (distance-first, else seconds)."""
    if step.distance_value is not None and step.distance_unit is not None:
        return {"value": _num(step.distance_value), "unit": step.distance_unit}
    return {"value": step.duration_seconds, "unit": "second"}


def _step_distance_len(step: SimpleStep) -> float:
    """Distance contribution of a step in its native unit; 0 for time-only rest steps."""
    if step.distance_value is not None and step.distance_unit is not None:
        return float(step.distance_value)
    return 0.0


def _compute_block_distance(block: SimpleStep | SimpleRepetitionBlock) -> float:
    """Total distance contribution of a block for distance-based workouts."""
    if isinstance(block, SimpleRepetitionBlock):
        inner = sum(_step_distance_len(s) for s in block.steps)
        return inner * block.reps
    return _step_distance_len(block)


def _num(v: float) -> int | float:
    """Return int if v is whole, else float — matches TP payload style."""
    return int(v) if v == int(v) else v


def _build_step_wire(step: SimpleStep) -> dict[str, Any]:
    """Convert a SimpleStep to wire format."""
    targets: list[dict[str, Any]] = [
        {"minValue": step.intensity_min, "maxValue": step.intensity_max},
    ]
    if step.cadence_min is not None and step.cadence_max is not None:
        targets.append(
            {
                "minValue": step.cadence_min,
                "maxValue": step.cadence_max,
                "unit": "roundOrStridePerMinute",
            }
        )

    return {
        "name": step.name,
        "type": "step",
        "length": _step_length_dict(step),
        "targets": targets,
        "intensityClass": step.intensityClass,
        "openDuration": False,
    }


def _compute_block_duration(block: SimpleStep | SimpleRepetitionBlock) -> int:
    """Compute total duration of a block in seconds."""
    if isinstance(block, SimpleRepetitionBlock):
        inner_duration = sum((s.duration_seconds or 0) for s in block.steps)
        return inner_duration * block.reps
    return block.duration_seconds or 0


def _polyline_bar(
    t_start: float, t_end: float, intensity: float, polyline: list[list[float]],
) -> None:
    """Append a rectangular bar to the polyline (TP native format).

    Each segment is drawn as: drop to 0 → rise to intensity → hold → drop to 0.
    """
    polyline.append([round(t_start, 4), 0])
    polyline.append([round(t_start, 4), round(intensity, 4)])
    polyline.append([round(t_end, 4), round(intensity, 4)])
    polyline.append([round(t_end, 4), 0])


def build_wire_structure(structure: SimpleWorkoutStructure) -> dict[str, Any]:
    """Convert simplified structure to TP API wire format.

    Args:
        structure: The simplified workout structure.

    Returns:
        Dict matching the TP API structure format.
    """
    if structure.primaryLengthMetric == "distance":
        return _build_wire_structure_distance(structure)
    return _build_wire_structure_duration(structure)


def _build_wire_structure_duration(structure: SimpleWorkoutStructure) -> dict[str, Any]:
    wire_blocks: list[dict[str, Any]] = []
    cumulative_seconds = 0

    # First pass: compute total duration for polyline normalisation
    total_duration = sum(_compute_block_duration(b) for b in structure.steps)

    for block in structure.steps:
        block_duration = _compute_block_duration(block)
        begin = cumulative_seconds
        end = cumulative_seconds + block_duration

        if isinstance(block, SimpleRepetitionBlock):
            inner_steps = [_build_step_wire(s) for s in block.steps]

            wire_block: dict[str, Any] = {
                "type": "repetition",
                "length": {"value": block.reps, "unit": "repetition"},
                "steps": inner_steps,
                "begin": begin,
                "end": end,
            }
            wire_blocks.append(wire_block)

        else:
            # Single step — TP uses repetition wrapper with value=1
            wire_step = _build_step_wire(block)
            wire_block = {
                "type": "step",
                "length": {"value": 1, "unit": "repetition"},
                "steps": [wire_step],
                "begin": begin,
                "end": end,
            }
            wire_blocks.append(wire_block)

        cumulative_seconds = end

    # Build polyline with zero-drop bars (matches TP native format)
    polyline: list[list[float]] = []
    poly_cumulative = 0

    for block in structure.steps:
        if isinstance(block, SimpleRepetitionBlock):
            for _rep in range(block.reps):
                for s in block.steps:
                    t_start = poly_cumulative / total_duration if total_duration > 0 else 0
                    poly_cumulative += (s.duration_seconds or 0)
                    t_end = poly_cumulative / total_duration if total_duration > 0 else 0
                    intensity = s.intensity_max / 100.0
                    _polyline_bar(t_start, t_end, intensity, polyline)
        else:
            t_start = poly_cumulative / total_duration if total_duration > 0 else 0
            poly_cumulative += (block.duration_seconds or 0)
            t_end = poly_cumulative / total_duration if total_duration > 0 else 0
            intensity = block.intensity_max / 100.0
            _polyline_bar(t_start, t_end, intensity, polyline)

    return {
        "structure": wire_blocks,
        "polyline": polyline,
        "primaryLengthMetric": "duration",
        "primaryIntensityMetric": structure.primaryIntensityMetric,
        "primaryIntensityTargetOrRange": "range",
    }


def _build_wire_structure_distance(structure: SimpleWorkoutStructure) -> dict[str, Any]:
    wire_blocks: list[dict[str, Any]] = []
    cumulative: float = 0.0

    for block in structure.steps:
        block_len = _compute_block_distance(block)
        begin = cumulative
        end = cumulative + block_len

        if isinstance(block, SimpleRepetitionBlock):
            inner_steps = [_build_step_wire(s) for s in block.steps]
            wire_block: dict[str, Any] = {
                "type": "repetition",
                "length": {"value": block.reps, "unit": "repetition"},
                "steps": inner_steps,
                "begin": _num(begin),
                "end": _num(end),
            }
        else:
            # Single step — outer wrapper length mirrors the inner step per TP payloads.
            wire_step = _build_step_wire(block)
            wire_block = {
                "type": "step",
                "length": _step_length_dict(block),
                "steps": [wire_step],
                "begin": _num(begin),
                "end": _num(end),
            }
        wire_blocks.append(wire_block)
        cumulative = end

    # No polyline in distance mode — TP generates its own for distance workouts;
    # a normalized version confuses the renderer.
    return {
        "structure": wire_blocks,
        "primaryLengthMetric": "distance",
        "primaryIntensityMetric": structure.primaryIntensityMetric,
        "primaryIntensityTargetOrRange": "range",
        "visualizationDistanceUnit": structure.distance_unit,
    }


def compute_if_tss(structure: SimpleWorkoutStructure) -> tuple[float, float, int]:
    """Compute IF and TSS from a workout structure.

    Uses NP-style time-weighted 4th-power average of midpoint intensities.
    IF = (weighted_sum / total_seconds) ^ 0.25 / 100
    TSS = (total_seconds * IF^2 * 100) / 3600

    Args:
        structure: The simplified workout structure.

    Returns:
        Tuple of (IF, TSS, total_duration_seconds).
    """
    weighted_sum = 0.0
    total_seconds = 0

    for block in structure.steps:
        if isinstance(block, SimpleRepetitionBlock):
            for _rep in range(block.reps):
                for step in block.steps:
                    dur = step.duration_seconds or 0
                    if dur == 0:
                        continue
                    midpoint = (step.intensity_min + step.intensity_max) / 2.0
                    weighted_sum += dur * (midpoint ** 4)
                    total_seconds += dur
        else:
            dur = block.duration_seconds or 0
            if dur == 0:
                continue
            midpoint = (block.intensity_min + block.intensity_max) / 2.0
            weighted_sum += dur * (midpoint ** 4)
            total_seconds += dur

    if total_seconds == 0:
        return 0.0, 0.0, 0

    intensity_factor = (weighted_sum / total_seconds) ** 0.25 / 100.0
    tss = (total_seconds * intensity_factor ** 2 * 100.0) / 3600.0

    return round(intensity_factor, 3), round(tss, 1), total_seconds


def parse_structure_input(structure_input: dict[str, Any] | str) -> SimpleWorkoutStructure:
    """Parse structure input from either a dict or JSON string.

    Args:
        structure_input: Structure as dict or JSON string.

    Returns:
        Parsed SimpleWorkoutStructure.

    Raises:
        ValidationError: If structure is invalid.
        ValueError: If JSON is malformed.
    """
    if isinstance(structure_input, str):
        try:
            data = json.loads(structure_input)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in structure: {e}") from e
    else:
        data = structure_input

    # Parse steps - distinguish between simple steps and repetition blocks
    raw_steps = data.get("steps", [])
    parsed_steps: list[SimpleStep | SimpleRepetitionBlock] = []

    for raw_step in raw_steps:
        if raw_step.get("type") == "repetition":
            parsed_steps.append(SimpleRepetitionBlock.model_validate(raw_step))
        else:
            parsed_steps.append(SimpleStep.model_validate(raw_step))

    return SimpleWorkoutStructure(
        primaryIntensityMetric=data.get("primaryIntensityMetric", "percentOfFtp"),
        primaryLengthMetric=data.get("primaryLengthMetric", "duration"),
        distance_unit=data.get("distance_unit"),
        steps=parsed_steps,
    )


async def tp_validate_structure(structure: str) -> dict[str, Any]:
    """Validate a workout interval structure without creating a workout.

    Args:
        structure: JSON string of the simplified structure format.

    Returns:
        Dict with validation result (block count, total duration, metric) or error.
    """
    try:
        parsed = parse_structure_input(structure)
    except (ValidationError, ValueError) as e:
        msg = format_validation_error(e) if isinstance(e, ValidationError) else str(e)
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": msg,
        }

    intensity_factor, tss, total_seconds = compute_if_tss(parsed)

    # Count blocks
    block_count = len(parsed.steps)
    step_count = 0
    for block in parsed.steps:
        if isinstance(block, SimpleRepetitionBlock):
            step_count += len(block.steps) * block.reps
        else:
            step_count += 1

    return {
        "valid": True,
        "block_count": block_count,
        "total_steps": step_count,
        "total_duration_seconds": total_seconds,
        "total_duration_minutes": round(total_seconds / 60, 1),
        "estimated_if": intensity_factor,
        "estimated_tss": tss,
        "intensity_metric": parsed.primaryIntensityMetric,
    }
