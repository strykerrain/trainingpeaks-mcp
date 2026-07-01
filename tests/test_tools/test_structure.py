"""Tests for workout structure builder, validator, and IF/TSS computation."""

import json

import pytest

from tp_mcp.tools.structure import (
    SimpleRepetitionBlock,
    SimpleStep,
    SimpleWorkoutStructure,
    build_wire_structure,
    compute_if_tss,
    parse_structure_input,
    tp_validate_structure,
)


class TestBuildSimpleStep:
    """Test building single steps and verifying wire format."""

    def test_warmup_step(self):
        step = SimpleStep(
            name="Warm Up", duration_seconds=600,
            intensity_min=40, intensity_max=55, intensityClass="warmUp",
        )
        structure = SimpleWorkoutStructure(steps=[step])
        wire = build_wire_structure(structure)

        assert len(wire["structure"]) == 1
        block = wire["structure"][0]
        assert block["type"] == "step"
        assert block["begin"] == 0
        assert block["end"] == 600
        assert block["steps"][0]["name"] == "Warm Up"
        assert block["steps"][0]["intensityClass"] == "warmUp"
        assert block["steps"][0]["targets"][0] == {"minValue": 40, "maxValue": 55}

    def test_active_step(self):
        step = SimpleStep(
            name="Threshold", duration_seconds=1200,
            intensity_min=95, intensity_max=105, intensityClass="active",
        )
        structure = SimpleWorkoutStructure(steps=[step])
        wire = build_wire_structure(structure)

        assert wire["structure"][0]["steps"][0]["intensityClass"] == "active"

    def test_cooldown_step(self):
        step = SimpleStep(
            name="Cool Down", duration_seconds=300,
            intensity_min=30, intensity_max=45, intensityClass="coolDown",
        )
        structure = SimpleWorkoutStructure(steps=[step])
        wire = build_wire_structure(structure)

        assert wire["structure"][0]["steps"][0]["intensityClass"] == "coolDown"

    def test_step_with_cadence(self):
        step = SimpleStep(
            name="High Cadence", duration_seconds=300,
            intensity_min=70, intensity_max=80, intensityClass="active",
            cadence_min=95, cadence_max=105,
        )
        structure = SimpleWorkoutStructure(steps=[step])
        wire = build_wire_structure(structure)

        targets = wire["structure"][0]["steps"][0]["targets"]
        assert len(targets) == 2
        assert targets[1]["unit"] == "roundOrStridePerMinute"
        assert targets[1]["minValue"] == 95
        assert targets[1]["maxValue"] == 105


class TestBuildRepetitionBlock:
    """Test building repetition blocks."""

    def test_repetition_block(self):
        steps = [
            SimpleStep(name="Hard", duration_seconds=300, intensity_min=90, intensity_max=100, intensityClass="active"),
            SimpleStep(name="Easy", duration_seconds=120, intensity_min=50, intensity_max=60, intensityClass="rest"),
        ]
        rep = SimpleRepetitionBlock(reps=4, steps=steps)
        structure = SimpleWorkoutStructure(steps=[rep])
        wire = build_wire_structure(structure)

        block = wire["structure"][0]
        assert block["type"] == "repetition"
        assert block["length"] == {"value": 4, "unit": "repetition"}
        assert len(block["steps"]) == 2
        assert block["begin"] == 0
        assert block["end"] == (300 + 120) * 4  # 1680

    def test_repetition_inner_steps(self):
        steps = [
            SimpleStep(name="ON", duration_seconds=60, intensity_min=100, intensity_max=110, intensityClass="active"),
            SimpleStep(name="OFF", duration_seconds=60, intensity_min=40, intensity_max=50, intensityClass="rest"),
        ]
        rep = SimpleRepetitionBlock(reps=8, steps=steps)
        structure = SimpleWorkoutStructure(steps=[rep])
        wire = build_wire_structure(structure)

        inner = wire["structure"][0]["steps"]
        assert inner[0]["name"] == "ON"
        assert inner[1]["name"] == "OFF"


class TestMultiBlockStructure:
    """Test multi-block structure with cumulative begin/end times."""

    def test_three_block_structure(self):
        warmup = SimpleStep(name="WU", duration_seconds=600, intensity_min=40, intensity_max=55, intensityClass="warmUp")
        intervals = SimpleRepetitionBlock(
            reps=4, steps=[
                SimpleStep(name="Hard", duration_seconds=300, intensity_min=90, intensity_max=100, intensityClass="active"),
                SimpleStep(name="Easy", duration_seconds=120, intensity_min=50, intensity_max=60, intensityClass="rest"),
            ],
        )
        cooldown = SimpleStep(name="CD", duration_seconds=600, intensity_min=40, intensity_max=55, intensityClass="coolDown")

        structure = SimpleWorkoutStructure(steps=[warmup, intervals, cooldown])
        wire = build_wire_structure(structure)

        assert len(wire["structure"]) == 3

        # Block 1: warmup 0-600
        assert wire["structure"][0]["begin"] == 0
        assert wire["structure"][0]["end"] == 600

        # Block 2: intervals 600-2280 (4 * (300+120) = 1680)
        assert wire["structure"][1]["begin"] == 600
        assert wire["structure"][1]["end"] == 2280

        # Block 3: cooldown 2280-2880
        assert wire["structure"][2]["begin"] == 2280
        assert wire["structure"][2]["end"] == 2880


class TestComputeIFTSS:
    """Test IF/TSS computation from structure."""

    def test_steady_state_workout(self):
        """60 min at 75% FTP -> IF ~0.75, TSS ~56."""
        step = SimpleStep(
            name="Endurance", duration_seconds=3600,
            intensity_min=75, intensity_max=75, intensityClass="active",
        )
        structure = SimpleWorkoutStructure(steps=[step])
        intensity_factor, tss, total = compute_if_tss(structure)

        assert total == 3600
        assert abs(intensity_factor - 0.75) < 0.01
        assert abs(tss - 56.2) < 1.0

    def test_structured_workout(self):
        """Mixed intensity workout should compute correctly."""
        warmup = SimpleStep(name="WU", duration_seconds=600, intensity_min=50, intensity_max=60, intensityClass="warmUp")
        intervals = SimpleRepetitionBlock(
            reps=4, steps=[
                SimpleStep(name="Hard", duration_seconds=300, intensity_min=95, intensity_max=105, intensityClass="active"),
                SimpleStep(name="Easy", duration_seconds=120, intensity_min=50, intensity_max=60, intensityClass="rest"),
            ],
        )
        cooldown = SimpleStep(name="CD", duration_seconds=600, intensity_min=40, intensity_max=50, intensityClass="coolDown")

        structure = SimpleWorkoutStructure(steps=[warmup, intervals, cooldown])
        intensity_factor, tss, total = compute_if_tss(structure)

        assert total == 600 + (300 + 120) * 4 + 600  # 2880
        assert intensity_factor > 0.6
        assert tss > 0

    def test_empty_steps_returns_zero(self):
        """Edge case: if somehow total_seconds is 0."""
        # Cannot create empty structure due to min_length=1, so test directly
        from tp_mcp.tools.structure import SimpleWorkoutStructure

        step = SimpleStep(name="x", duration_seconds=1, intensity_min=0, intensity_max=0, intensityClass="active")
        structure = SimpleWorkoutStructure(steps=[step])
        _, _, total = compute_if_tss(structure)
        assert total == 1


class TestValidation:
    """Test structure validation."""

    def test_missing_duration_raises(self):
        with pytest.raises(Exception):
            SimpleStep(name="Bad", duration_seconds=0, intensity_min=50, intensity_max=60, intensityClass="active")

    def test_empty_steps_raises(self):
        with pytest.raises(Exception):
            SimpleWorkoutStructure(steps=[])

    def test_invalid_intensity_class(self):
        with pytest.raises(Exception):
            SimpleStep(name="Bad", duration_seconds=300, intensity_min=50, intensity_max=60, intensityClass="invalid")

    def test_intensity_min_gt_max(self):
        with pytest.raises(Exception):
            SimpleStep(name="Bad", duration_seconds=300, intensity_min=100, intensity_max=50, intensityClass="active")

    def test_invalid_primary_metric(self):
        step = SimpleStep(name="OK", duration_seconds=300, intensity_min=50, intensity_max=60, intensityClass="active")
        with pytest.raises(Exception):
            SimpleWorkoutStructure(primaryIntensityMetric="invalidMetric", steps=[step])


class TestParseStructureInput:
    """Test parsing structure from dict and JSON string."""

    def test_parse_from_dict(self):
        data = {
            "primaryIntensityMetric": "percentOfFtp",
            "steps": [
                {"name": "WU", "duration_seconds": 600, "intensity_min": 40, "intensity_max": 55, "intensityClass": "warmUp"},
            ],
        }
        parsed = parse_structure_input(data)
        assert len(parsed.steps) == 1
        assert parsed.primaryIntensityMetric == "percentOfFtp"

    def test_parse_from_json_string(self):
        data = {
            "steps": [
                {"name": "Main", "duration_seconds": 1200, "intensity_min": 80, "intensity_max": 90, "intensityClass": "active"},
            ],
        }
        parsed = parse_structure_input(json.dumps(data))
        assert len(parsed.steps) == 1

    def test_parse_repetition_block(self):
        data = {
            "steps": [
                {
                    "type": "repetition", "reps": 3,
                    "steps": [
                        {"name": "ON", "duration_seconds": 60, "intensity_min": 95, "intensity_max": 105, "intensityClass": "active"},
                        {"name": "OFF", "duration_seconds": 60, "intensity_min": 50, "intensity_max": 60, "intensityClass": "rest"},
                    ],
                },
            ],
        }
        parsed = parse_structure_input(data)
        assert isinstance(parsed.steps[0], SimpleRepetitionBlock)
        assert parsed.steps[0].reps == 3

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_structure_input("{bad json")


class TestTpValidateStructure:
    """Tests for tp_validate_structure tool."""

    @pytest.mark.asyncio
    async def test_valid_structure_returns_summary(self):
        data = json.dumps({
            "primaryIntensityMetric": "percentOfFtp",
            "steps": [
                {"name": "WU", "duration_seconds": 600, "intensity_min": 40, "intensity_max": 55, "intensityClass": "warmUp"},
                {"name": "Main", "duration_seconds": 1200, "intensity_min": 85, "intensity_max": 95, "intensityClass": "active"},
                {"name": "CD", "duration_seconds": 600, "intensity_min": 40, "intensity_max": 55, "intensityClass": "coolDown"},
            ],
        })
        result = await tp_validate_structure(data)

        assert result["valid"] is True
        assert result["block_count"] == 3
        assert result["total_steps"] == 3
        assert result["total_duration_seconds"] == 2400
        assert result["total_duration_minutes"] == 40.0
        assert result["estimated_if"] > 0
        assert result["estimated_tss"] > 0
        assert result["intensity_metric"] == "percentOfFtp"

    @pytest.mark.asyncio
    async def test_invalid_structure_returns_error(self):
        result = await tp_validate_structure("{bad json")

        assert result["isError"] is True
        assert result["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_empty_steps_returns_error(self):
        result = await tp_validate_structure(json.dumps({"steps": []}))

        assert result["isError"] is True
        assert result["error_code"] == "VALIDATION_ERROR"


class TestDistanceWorkout:
    """Distance-based (swim/track) structures."""

    def _swim_structure(self) -> SimpleWorkoutStructure:
        warmup = SimpleStep(
            name="Warm up", distance_value=200, distance_unit="meter",
            intensity_min=40, intensity_max=50, intensityClass="warmUp",
        )
        active = SimpleStep(
            name="Active", distance_value=100, distance_unit="meter",
            intensity_min=70, intensity_max=80, intensityClass="active",
        )
        rest = SimpleStep(
            name="Recovery", duration_seconds=10,
            intensity_min=70, intensity_max=80, intensityClass="rest",
        )
        reps = SimpleRepetitionBlock(reps=4, steps=[active, rest])
        cooldown = SimpleStep(
            name="Cool down", distance_value=100, distance_unit="meter",
            intensity_min=40, intensity_max=50, intensityClass="coolDown",
        )
        return SimpleWorkoutStructure(
            primaryIntensityMetric="percentOfThresholdPace",
            primaryLengthMetric="distance",
            distance_unit="meter",
            steps=[warmup, reps, cooldown],
        )

    def test_top_level_fields(self):
        wire = build_wire_structure(self._swim_structure())

        assert wire["primaryLengthMetric"] == "distance"
        assert wire["primaryIntensityMetric"] == "percentOfThresholdPace"
        assert wire["primaryIntensityTargetOrRange"] == "range"
        assert wire["visualizationDistanceUnit"] == "meter"

    def test_cumulative_begin_end_only_distance_steps_count(self):
        wire = build_wire_structure(self._swim_structure())
        blocks = wire["structure"]

        assert len(blocks) == 3

        # Warmup: 200m
        assert blocks[0]["begin"] == 0
        assert blocks[0]["end"] == 200

        # Reps: 4 x 100m active (+ 10s rest which does NOT contribute)
        assert blocks[1]["begin"] == 200
        assert blocks[1]["end"] == 600

        # Cooldown: 100m
        assert blocks[2]["begin"] == 600
        assert blocks[2]["end"] == 700

    def test_rest_step_uses_second_unit_inside_distance_workout(self):
        wire = build_wire_structure(self._swim_structure())
        rep_block = wire["structure"][1]

        assert rep_block["type"] == "repetition"
        assert rep_block["length"] == {"value": 4, "unit": "repetition"}

        inner = rep_block["steps"]
        assert inner[0]["name"] == "Active"
        assert inner[0]["length"] == {"value": 100, "unit": "meter"}
        assert inner[1]["name"] == "Recovery"
        assert inner[1]["length"] == {"value": 10, "unit": "second"}

    def test_single_distance_step_outer_length_mirrors_inner(self):
        wire = build_wire_structure(self._swim_structure())

        warmup_block = wire["structure"][0]
        assert warmup_block["type"] == "step"
        assert warmup_block["length"] == {"value": 200, "unit": "meter"}
        assert warmup_block["steps"][0]["length"] == {"value": 200, "unit": "meter"}

        cooldown_block = wire["structure"][2]
        assert cooldown_block["length"] == {"value": 100, "unit": "meter"}

    def test_missing_distance_unit_when_distance_metric_raises(self):
        step = SimpleStep(
            name="x", distance_value=100, distance_unit="meter",
            intensity_min=50, intensity_max=60, intensityClass="active",
        )
        with pytest.raises(Exception):
            SimpleWorkoutStructure(
                primaryLengthMetric="distance",
                steps=[step],
            )

    def test_step_requires_duration_or_distance(self):
        with pytest.raises(Exception):
            SimpleStep(
                name="Bad",
                intensity_min=50, intensity_max=60, intensityClass="active",
            )

    def test_step_cannot_specify_both_length_types(self):
        with pytest.raises(Exception):
            SimpleStep(
                name="Bad", duration_seconds=60,
                distance_value=100, distance_unit="meter",
                intensity_min=50, intensity_max=60, intensityClass="active",
            )

    def test_invalid_distance_unit_raises(self):
        with pytest.raises(Exception):
            SimpleStep(
                name="Bad", distance_value=100, distance_unit="furlong",
                intensity_min=50, intensity_max=60, intensityClass="active",
            )

    def test_parse_from_dict_round_trip(self):
        data = {
            "primaryLengthMetric": "distance",
            "primaryIntensityMetric": "percentOfThresholdPace",
            "distance_unit": "meter",
            "steps": [
                {"name": "WU", "distance_value": 200, "distance_unit": "meter",
                 "intensity_min": 40, "intensity_max": 50, "intensityClass": "warmUp"},
            ],
        }
        parsed = parse_structure_input(data)
        assert parsed.primaryLengthMetric == "distance"
        assert parsed.distance_unit == "meter"

        wire = build_wire_structure(parsed)
        assert wire["visualizationDistanceUnit"] == "meter"
        assert wire["structure"][0]["length"] == {"value": 200, "unit": "meter"}
