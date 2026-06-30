"""Workout library tools: templates, scheduling."""

import json
import logging
import uuid
from typing import Any

from pydantic import ValidationError

from tp_mcp.client import TPClient
from tp_mcp.tools._validation import WorkoutIdInput, format_validation_error

logger = logging.getLogger("tp-mcp")

RX_API_BASE = "https://api.peakswaresb.com"


async def tp_get_libraries() -> dict[str, Any]:
    """List all workout library folders.

    Returns:
        Dict with libraries list.
    """
    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        endpoint = "/exerciselibrary/v2/libraries"
        response = await client.get(endpoint)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        data = response.data if isinstance(response.data, list) else []
        libraries = [
            {
                "id": lib.get("exerciseLibraryId", lib.get("id")),
                # v2 libraries endpoint returns "libraryName"/"isDefaultContent";
                # keep the old keys as fallbacks for safety.
                "name": lib.get("libraryName", lib.get("name", "")),
                "is_default": lib.get("isDefaultContent", lib.get("isDefault", False)),
                "owner_name": lib.get("ownerName"),
                # The v2 libraries endpoint usually omits an item count; read it
                # if present, otherwise fall back to 0.
                "item_count": lib.get("itemCount", 0),
                "owner_id": lib.get("ownerId"),
            }
            for lib in data
        ]

        return {"libraries": libraries, "count": len(libraries)}


async def tp_get_library_items(library_id: str) -> dict[str, Any]:
    """List templates in a workout library.

    Args:
        library_id: Library ID.

    Returns:
        Dict with library items list.
    """
    try:
        validated = WorkoutIdInput(workout_id=library_id)
    except (ValidationError, ValueError) as e:
        msg = format_validation_error(e) if isinstance(e, ValidationError) else str(e)
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": msg,
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        endpoint = f"/exerciselibrary/v2/libraries/{validated.workout_id}/items"
        response = await client.get(endpoint)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        data = response.data if isinstance(response.data, list) else []
        items = [
            {
                "id": item.get("exerciseLibraryItemId", item.get("id")),
                "name": item.get("itemName", item.get("name", "")),
                "sport": item.get("workoutTypeId"),
                "duration": item.get("totalTimePlanned"),
                "tss": item.get("tssPlanned"),
            }
            for item in data
        ]

        return {
            "items": items,
            "count": len(items),
            "library_id": validated.workout_id,
        }


async def tp_get_library_item(library_id: str, item_id: str) -> dict[str, Any]:
    """Get full template details including structure.

    Args:
        library_id: Library ID.
        item_id: Library item ID.

    Returns:
        Dict with item details.
    """
    try:
        lib_validated = WorkoutIdInput(workout_id=library_id)
        item_validated = WorkoutIdInput(workout_id=item_id)
    except (ValidationError, ValueError) as e:
        msg = format_validation_error(e) if isinstance(e, ValidationError) else str(e)
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": msg,
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        # Get all items and find the specific one
        endpoint = f"/exerciselibrary/v2/libraries/{lib_validated.workout_id}/items"
        response = await client.get(endpoint)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        data = response.data if isinstance(response.data, list) else []

        for item in data:
            iid = item.get("exerciseLibraryItemId", item.get("id"))
            if iid == item_validated.workout_id:
                return {"item": item}

        return {
            "isError": True,
            "error_code": "NOT_FOUND",
            "message": f"Item {item_validated.workout_id} not found in library {lib_validated.workout_id}.",
        }


async def tp_create_library(name: str) -> dict[str, Any]:
    """Create a workout library folder.

    Args:
        name: Library name.

    Returns:
        Dict with confirmation or error.
    """
    if not name or not name.strip():
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": "Library name must not be empty.",
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        # TP expects "libraryName" and the owner's personId, not "name".
        owner_id = None
        user_data = await client._get_user_data()
        if user_data:
            owner_id = user_data.get("personId")

        endpoint = "/exerciselibrary/v1/libraries"
        payload: dict[str, Any] = {"libraryName": name.strip()}
        if owner_id is not None:
            payload["ownerId"] = owner_id
        response = await client.post(endpoint, json=payload)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        lib_id = None
        if isinstance(response.data, dict):
            lib_id = response.data.get("exerciseLibraryId", response.data.get("id"))

        return {
            "success": True,
            "library_id": lib_id,
            "name": name.strip(),
        }


async def tp_delete_library(library_id: str) -> dict[str, Any]:
    """Delete a workout library folder and all its templates.

    Args:
        library_id: Library ID.

    Returns:
        Dict with confirmation or error.
    """
    try:
        validated = WorkoutIdInput(workout_id=library_id)
    except (ValidationError, ValueError) as e:
        msg = format_validation_error(e) if isinstance(e, ValidationError) else str(e)
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": msg,
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        endpoint = f"/exerciselibrary/v1/libraries/{validated.workout_id}"
        response = await client.delete(endpoint)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        return {
            "success": True,
            "message": f"Library {validated.workout_id} deleted.",
        }


async def tp_create_library_item(
    library_id: str,
    name: str,
    sport_family_id: int,
    sport_type_id: int,
    duration_hours: float | None = None,
    tss: float | None = None,
    description: str | None = None,
    structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save a workout template to a library.

    Args:
        library_id: Library ID.
        name: Template name.
        sport_family_id: Sport ID (e.g. 2 = Bike; see tp_get_workout_types).
        sport_type_id: Sport subtype ID (e.g. 3 = Road Bike).
        duration_hours: Optional duration in hours.
        tss: Optional planned TSS.
        description: Optional description.
        structure: Optional interval structure (nested object, NOT string).

    Returns:
        Dict with confirmation or error.
    """
    try:
        lib_validated = WorkoutIdInput(workout_id=library_id)
    except (ValidationError, ValueError) as e:
        msg = format_validation_error(e) if isinstance(e, ValidationError) else str(e)
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": msg,
        }

    if not name or not name.strip():
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": "Template name must not be empty.",
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        # Library items use workoutTypeId/workoutSubTypeId (not the
        # workoutTypeFamilyId/workoutTypeValueId pair of the fitness API).
        # Sending the wrong field names silently creates items with sport 0
        # ("unknown"), which render without power targets in the TP UI.
        payload: dict[str, Any] = {
            "exerciseLibraryId": lib_validated.workout_id,
            "itemName": name.strip(),
            "workoutTypeId": sport_family_id,
            "workoutSubTypeId": sport_type_id,
        }
        if duration_hours is not None:
            payload["totalTimePlanned"] = duration_hours
        if tss is not None:
            payload["tssPlanned"] = tss
        if description:
            payload["description"] = description
        if structure is not None:
            # Library items use nested object, NOT double-serialised string
            payload["structure"] = structure

        endpoint = f"/exerciselibrary/v1/libraries/{lib_validated.workout_id}/items"
        response = await client.post(endpoint, json=payload)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        item_id = None
        if isinstance(response.data, dict):
            item_id = response.data.get("exerciseLibraryItemId", response.data.get("id"))

        return {
            "success": True,
            "item_id": item_id,
            "name": name.strip(),
            "library_id": lib_validated.workout_id,
        }


async def tp_update_library_item(
    library_id: str,
    item_id: str,
    name: str | None = None,
    duration_hours: float | None = None,
    tss: float | None = None,
    description: str | None = None,
    structure: dict[str, Any] | None = None,
    workout_type_id: int | None = None,
    workout_sub_type_id: int | None = None,
) -> dict[str, Any]:
    """Edit a workout template.

    Args:
        library_id: Library ID.
        item_id: Item ID.
        name: Optional new name.
        duration_hours: Optional duration in hours.
        tss: Optional planned TSS.
        description: Optional description.
        structure: Optional structure (nested object).
        workout_type_id: Optional sport/workout type (1=swim, 2=bike, 3=run, ...).
            Use to set the sport on templates that were saved without one.
        workout_sub_type_id: Optional workout subtype id (e.g. 6=Indoor Bike).

    Returns:
        Dict with confirmation or error.
    """
    try:
        lib_validated = WorkoutIdInput(workout_id=library_id)
        item_validated = WorkoutIdInput(workout_id=item_id)
    except (ValidationError, ValueError) as e:
        msg = format_validation_error(e) if isinstance(e, ValidationError) else str(e)
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": msg,
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        # GET existing items to find and merge
        get_endpoint = f"/exerciselibrary/v2/libraries/{lib_validated.workout_id}/items"
        get_response = await client.get(get_endpoint)

        if get_response.is_error:
            return {
                "isError": True,
                "error_code": get_response.error_code.value if get_response.error_code else "API_ERROR",
                "message": get_response.message,
            }

        data = get_response.data if isinstance(get_response.data, list) else []

        existing = None
        for item in data:
            iid = item.get("exerciseLibraryItemId", item.get("id"))
            if iid == item_validated.workout_id:
                existing = item
                break

        if existing is None:
            return {
                "isError": True,
                "error_code": "NOT_FOUND",
                "message": f"Item {item_validated.workout_id} not found.",
            }

        # Merge updates
        if name is not None:
            existing["itemName"] = name
        if duration_hours is not None:
            existing["totalTimePlanned"] = duration_hours
        if tss is not None:
            existing["tssPlanned"] = tss
        if description is not None:
            existing["description"] = description
        if structure is not None:
            existing["structure"] = structure
        if workout_type_id is not None:
            existing["workoutTypeId"] = workout_type_id
        if workout_sub_type_id is not None:
            existing["workoutSubTypeId"] = workout_sub_type_id

        put_endpoint = (
            f"/exerciselibrary/v1/libraries/{lib_validated.workout_id}"
            f"/items/{item_validated.workout_id}"
        )
        put_response = await client.put(put_endpoint, json=existing)

        if put_response.is_error:
            return {
                "isError": True,
                "error_code": put_response.error_code.value if put_response.error_code else "API_ERROR",
                "message": put_response.message,
            }

        return {
            "success": True,
            "message": f"Library item {item_validated.workout_id} updated.",
        }


async def tp_schedule_library_workout(
    library_id: str,
    item_id: str,
    date: str,
) -> dict[str, Any]:
    """Schedule a library template to a calendar date.

    Copies the template into a planned workout (title, structure, planned
    metrics, description). The native ``addworkoutfromlibraryitem`` command
    endpoint returns HTTP 500 for every payload shape, so this mirrors what
    the TP web app effectively does when a template is dragged onto the
    calendar.

    Args:
        library_id: Library ID.
        item_id: Library item ID.
        date: Target date (YYYY-MM-DD).

    Returns:
        Dict with confirmation (including new workout_id) or error.
    """
    try:
        lib_validated = WorkoutIdInput(workout_id=library_id)
        item_validated = WorkoutIdInput(workout_id=item_id)
    except (ValidationError, ValueError) as e:
        msg = format_validation_error(e) if isinstance(e, ValidationError) else str(e)
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": msg,
        }

    try:
        from datetime import date as date_type

        date_type.fromisoformat(date)
    except ValueError:
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": f"Invalid date: {date}",
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        # Fetch the template to copy
        items_endpoint = f"/exerciselibrary/v2/libraries/{lib_validated.workout_id}/items"
        items_response = await client.get(items_endpoint)

        if items_response.is_error:
            return {
                "isError": True,
                "error_code": items_response.error_code.value
                if items_response.error_code
                else "API_ERROR",
                "message": items_response.message,
            }

        items = items_response.data if isinstance(items_response.data, list) else []
        item = next(
            (
                i
                for i in items
                if i.get("exerciseLibraryItemId", i.get("id")) == item_validated.workout_id
            ),
            None,
        )
        if item is None:
            return {
                "isError": True,
                "error_code": "NOT_FOUND",
                "message": (
                    f"Item {item_validated.workout_id} not found in "
                    f"library {lib_validated.workout_id}."
                ),
            }

        sport_id = item.get("workoutTypeId")
        payload: dict[str, Any] = {
            "athleteId": athlete_id,
            "workoutDay": f"{date}T00:00:00",
            "workoutTypeFamilyId": sport_id,
            "workoutTypeValueId": sport_id,
            "title": item.get("itemName"),
            "totalTimePlanned": item.get("totalTimePlanned"),
            "tssPlanned": item.get("tssPlanned"),
            "ifPlanned": item.get("ifPlanned"),
            "distancePlanned": item.get("distancePlanned"),
            "elevationGainPlanned": item.get("elevationGainPlanned"),
            "caloriesPlanned": item.get("caloriesPlanned"),
            "description": item.get("description"),
            "coachComments": item.get("coachComments"),
        }
        if item.get("workoutSubTypeId") is not None:
            payload["workoutSubTypeId"] = item["workoutSubTypeId"]
        if item.get("structure"):
            # Calendar workouts carry structure as a JSON string
            payload["structure"] = json.dumps(item["structure"])

        endpoint = f"/fitness/v6/athletes/{athlete_id}/workouts"
        response = await client.post(endpoint, json=payload)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        workout_id = None
        if isinstance(response.data, dict):
            workout_id = response.data.get("workoutId")

        return {
            "success": True,
            "message": f"Library workout scheduled for {date}.",
            "date": date,
            "workout_id": workout_id,
            "title": item.get("itemName"),
        }


# Endpoint exposing the catalog of exercises (with their parameter definitions)
# available to the strength builder.
_LIBRARY_CONTENT_ENDPOINT = "/rx/activity/v1/libraryContent"


async def _fetch_library_exercises(
    client: TPClient,
    cache: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    """Fetch the full exercise catalog from libraryContent, with optional caching.

    Returns the exercises list, or None on failure / unexpected shape.
    When ``cache`` is provided, the resolved list (or None) is stored under
    ``"exercises"`` so repeated lookups in one tool call hit the API once.
    """
    if cache is not None and "exercises" in cache:
        return cache["exercises"]

    response = await client.get(_LIBRARY_CONTENT_ENDPOINT, base_url=RX_API_BASE)
    exercises: list[dict[str, Any]] | None
    if response.is_error:
        # Surface failures clearly — silent fallback to input-derived columns
        # can yield invalid payloads for parameter types whose shape differs
        # from the user-supplied entry (e.g. RepsPerSide).
        logger.warning(
            "libraryContent fetch failed (%s): %s; strength builder will fall "
            "back to input-derived parameter shape.",
            response.error_code.value if response.error_code else "API_ERROR",
            response.message,
        )
        exercises = None
    elif not isinstance(response.data, dict):
        logger.warning(
            "libraryContent returned unexpected payload type %s; falling back "
            "to input-derived parameter shape.",
            type(response.data).__name__,
        )
        exercises = None
    else:
        raw = response.data.get("exercises")
        if not isinstance(raw, list):
            logger.warning(
                "libraryContent payload missing 'exercises' list; falling back "
                "to input-derived parameter shape."
            )
            exercises = None
        else:
            exercises = raw

    if cache is not None:
        cache["exercises"] = exercises
    return exercises


async def get_exercise_params(
    client: TPClient,
    exercise_id: str,
    cache: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    """Look up the parameter definitions for an exercise in libraryContent.

    Args:
        client: An active TPClient inside an ``async with`` block.
        exercise_id: Exercise ID to look up (compared as string).
        cache: Optional dict used to cache the libraryContent response within
            a single tool invocation to avoid redundant network calls.

    Returns:
        The matched exercise's ``parameters`` list, or None if the exercise
        was not found, has no parameters, or the request failed.
    """
    exercises = await _fetch_library_exercises(client, cache=cache)
    if not exercises:
        return None

    target = str(exercise_id)
    for ex in exercises:
        if str(ex.get("id", "")) == target:
            params = ex.get("parameters")
            return params if isinstance(params, list) and params else None

    logger.warning(
        "Exercise id %s not found in libraryContent; falling back to "
        "input-derived parameter shape.",
        target,
    )
    return None


async def tp_search_exercises(query: str) -> dict[str, Any]:
    """Search the TrainingPeaks exercise catalog by title substring.

    Helps coaches discover the exercise IDs needed for
    ``tp_create_strength_workout`` without resorting to browser dev tools.

    Args:
        query: Case-insensitive substring to match against exercise titles.

    Returns:
        Dict with a ``matches`` list of ``{id, title}`` entries.
    """
    if not query or not query.strip():
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": "query must not be empty.",
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        response = await client.get(_LIBRARY_CONTENT_ENDPOINT, base_url=RX_API_BASE)
        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        exercises: list[dict[str, Any]] = []
        if isinstance(response.data, dict):
            raw = response.data.get("exercises")
            if isinstance(raw, list):
                exercises = raw

        needle = query.strip().lower()
        matches = [
            {"id": str(ex.get("id", "")), "title": ex.get("title", "")}
            for ex in exercises
            if needle in str(ex.get("title", "")).lower()
        ]

        return {
            "matches": matches,
            "count": len(matches),
            "query": query.strip(),
        }


# Parameter metadata for the structured strength builder. Unknown parameter
# names fall back to a Reps-like Integer shape.
# templateLabel is the suffix used in setSummaryTemplate after "{Param}";
# an empty string means no suffix (e.g. "{Duration}" rather than "{Duration} Duration").

# TrainingPeaks' default owner ID for library exercises — required by the UI.
_STRENGTH_DEFAULT_OWNER_ID = 2000301

_STRENGTH_PARAM_DEFS: dict[str, dict[str, Any]] = {
    "Reps": {
        "category": "Reps",
        "unit": {"title": "Reps", "abbreviation": "", "unit": "Reps"},
        "inputFormat": "Integer",
        "templateLabel": "Reps",
    },
    "RepsPerSide": {
        "category": "Reps/side",
        "unit": {"title": "Reps", "abbreviation": "", "unit": "Reps"},
        "inputFormat": "Integer",
        "templateLabel": "Reps/side",
    },
    "Duration": {
        "category": "Duration",
        "unit": {"title": "Seconds", "abbreviation": "sec", "unit": "Seconds"},
        "inputFormat": "Time",
        "templateLabel": "",
    },
    "WeightLb": {
        "category": "WeightLb",
        "unit": {"title": "Pounds", "abbreviation": "lb", "unit": "Pounds"},
        "inputFormat": "Decimal",
        "templateLabel": "lbs",
    },
    "WeightKg": {
        "category": "Weight",
        "unit": {"title": "Kilograms", "abbreviation": "kg", "unit": "Kilograms"},
        "inputFormat": "Decimal",
        "templateLabel": "kg",
    },
    "WeightPerSideLb": {
        "category": "Weight/side",
        "unit": {"title": "Pounds", "abbreviation": "lb", "unit": "Pounds"},
        "inputFormat": "Decimal",
        "templateLabel": "lbs/side",
    },
    "WeightPerSideKg": {
        "category": "Weight/side",
        "unit": {"title": "Kilograms", "abbreviation": "kg", "unit": "Kilograms"},
        "inputFormat": "Decimal",
        "templateLabel": "kg/side",
    },
    "DistanceMeters": {
        "category": "Distance",
        "unit": {"title": "Meters", "abbreviation": "m", "unit": "Meters"},
        "inputFormat": "Decimal",
        "templateLabel": "m",
    },
    "TimeSeconds": {
        "category": "Duration",
        "unit": {"title": "Seconds", "abbreviation": "sec", "unit": "Seconds"},
        "inputFormat": "Integer",
        "templateLabel": "",
    },
}


def _strength_param_def(name: str) -> dict[str, Any]:
    return _STRENGTH_PARAM_DEFS.get(
        name,
        {
            "category": name,
            "unit": {"title": name, "abbreviation": "", "unit": name},
            "inputFormat": "Integer",
            "templateLabel": name,
        },
    )


def _serialize_prescribed_value(value: Any) -> Any:
    # TP API requires prescribedValue as a string for all parameter types
    # (e.g. "30" for Duration seconds, "15" for Reps, "135" for WeightLb).
    # None is preserved (valid for nullable parameters like WeightLb).
    if value is None:
        return None
    return str(value)


def _build_strength_prescription(
    exercise: dict[str, Any],
    defined_params: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    exercise_id = str(exercise.get("exercise_id", ""))
    exercise_title = exercise.get("exercise_title", "")
    video_url = exercise.get("video_url", "") or ""
    instructions = exercise.get("instructions", "") or ""
    sets_input = exercise.get("sets", []) or []

    if defined_params:
        # libraryContent tells us the exact column shape TP expects for this
        # exercise. Each set must surface every defined parameter so the UI
        # can render the full grid; missing values are sent as null.
        param_order: list[str] = []
        param_def_by_name: dict[str, dict[str, Any]] = {}
        for p_def in defined_params:
            name = p_def.get("parameter")
            if not name or name in param_def_by_name:
                continue
            param_order.append(name)
            param_def_by_name[name] = p_def

        parameters = []
        for name in param_order:
            p_def = param_def_by_name[name]
            fallback = _strength_param_def(name)
            parameters.append(
                {
                    "id": str(uuid.uuid4()),
                    "parameter": name,
                    "title": p_def.get("title", name),
                    "category": p_def.get("category", fallback["category"]),
                    "unit": p_def.get("unit", fallback["unit"]),
                }
            )

        sets = []
        for s in sets_input:
            supplied_name = s.get("parameter", "")
            supplied_value = s.get("value")
            parameter_values = []
            for name in param_order:
                p_def = param_def_by_name[name]
                input_format = p_def.get("inputFormat") or _strength_param_def(name)["inputFormat"]
                value = supplied_value if name == supplied_name else None
                parameter_values.append(
                    {
                        "id": str(uuid.uuid4()),
                        "parameter": name,
                        "inputFormat": input_format,
                        "prescribedValue": _serialize_prescribed_value(value),
                        "executedValue": None,
                    }
                )
            sets.append(
                {
                    "id": str(uuid.uuid4()),
                    "isComplete": False,
                    "setOrigin": "Prescribed",
                    "parameterValues": parameter_values,
                }
            )

        template_parts: list[str] = []
        for name in param_order:
            label = param_def_by_name[name].get("templateLabel")
            if label is None:
                label = _strength_param_def(name).get("templateLabel", name)
            template_parts.append(f"{{{name}}} {label}".rstrip())
        template = " ".join(template_parts)
    else:
        # Fallback: derive columns purely from the user-supplied set entries
        # (used when libraryContent lookup fails or returns nothing).
        param_order = []
        for s in sets_input:
            p = s.get("parameter")
            if p and p not in param_order:
                param_order.append(p)

        parameters = []
        for p in param_order:
            defs = _strength_param_def(p)
            parameters.append(
                {
                    "id": str(uuid.uuid4()),
                    "parameter": p,
                    "title": p,
                    "category": defs["category"],
                    "unit": defs["unit"],
                }
            )

        sets = []
        for s in sets_input:
            p = s.get("parameter", "")
            defs = _strength_param_def(p)
            sets.append(
                {
                    "id": str(uuid.uuid4()),
                    "isComplete": False,
                    "setOrigin": "Prescribed",
                    "parameterValues": [
                        {
                            "id": str(uuid.uuid4()),
                            "parameter": p,
                            "inputFormat": defs["inputFormat"],
                            "prescribedValue": _serialize_prescribed_value(s.get("value")),
                            "executedValue": None,
                        }
                    ],
                }
            )

        template_parts = []
        for p in param_order:
            label = _strength_param_def(p).get("templateLabel", p)
            template_parts.append(f"{{{p}}} {label}".rstrip())
        template = " ".join(template_parts)

    # TP UI requires the full exercise object — id/title alone causes a crash
    # when opening the workout. ownerId 2000301 is TP's default for library
    # exercises; parameters mirrors the prescription-level parameters array.
    exercise_obj = {
        "id": exercise_id,
        "title": exercise_title,
        "ownerId": _STRENGTH_DEFAULT_OWNER_ID,
        "videoUrl": video_url,
        "instructions": instructions,
        "primaryMuscleGroups": [],
        "secondaryMuscleGroups": [],
        "canEdit": False,
        "parameters": parameters,
    }

    return {
        "id": str(uuid.uuid4()),
        "exercise": exercise_obj,
        "parameters": parameters,
        "sets": sets,
        "coachNotes": None,
        "compliancePercent": 0,
        "complianceState": "NoCompletion",
        "setSummaryTemplate": template,
    }


def _build_strength_block(
    block: dict[str, Any],
    exercise_params: dict[str, list[dict[str, Any]] | None] | None = None,
) -> dict[str, Any]:
    exercises = block.get("exercises", []) or []
    params_by_id = exercise_params or {}
    block_type = block.get("blockType", "SingleExercise")
    # Block-level parameters (e.g. TimeSeconds on WarmUp/CoolDown) are a
    # separate concept from exercise prescription parameters and must be
    # passed through untouched by the libraryContent lookup.
    block_params_input = list(block.get("parameters") or [])

    # TP's renderer crashes on WarmUp/CoolDown blocks without a block-level
    # TimeSeconds parameter, so inject a 5-minute default when the caller
    # hasn't supplied one. User-supplied TimeSeconds entries take precedence.
    if block_type in ("WarmUp", "CoolDown") and not any(
        isinstance(p, dict) and p.get("parameter") == "TimeSeconds"
        for p in block_params_input
    ):
        block_params_input.insert(
            0,
            {
                "id": str(uuid.uuid4()),
                "parameter": "TimeSeconds",
                "title": "Total Time Seconds",
                "unit": {"title": "Seconds", "abbreviation": "sec", "unit": "Seconds"},
                "inputFormat": "Integer",
                "prescribedValue": "300",
                "executedValue": None,
            },
        )

    return {
        "id": str(uuid.uuid4()),
        "blockType": block_type,
        "title": block.get("title", ""),
        "coachNotes": block.get("coachNotes"),
        "parameters": _build_block_parameters(block_params_input),
        "isComplete": False,
        "compliancePercent": 0,
        "complianceState": "NoCompletion",
        "prescriptions": [
            _build_strength_prescription(
                ex,
                params_by_id.get(str(ex.get("exercise_id", ""))),
            )
            for ex in exercises
        ],
    }


def _build_block_parameters(
    params_input: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expand simple {parameter, value} block params to TP's full shape.

    WarmUp/CoolDown blocks carry top-level parameters such as TimeSeconds.
    Already-fully-shaped entries (with ``inputFormat``/``unit``) are passed
    through with an ``id`` added if missing.
    """
    out: list[dict[str, Any]] = []
    for entry in params_input or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("parameter")
        if not name:
            continue
        # Fully-shaped entry (already has inputFormat) — preserve it
        if "inputFormat" in entry and "unit" in entry:
            full = dict(entry)
            full.setdefault("id", str(uuid.uuid4()))
            full.setdefault("executedValue", None)
            out.append(full)
            continue
        # Simple {parameter, value} — expand from the known param table
        defs = _strength_param_def(name)
        out.append(
            {
                "id": str(uuid.uuid4()),
                "parameter": name,
                "title": defs.get("title", name),
                "unit": defs["unit"],
                "inputFormat": defs["inputFormat"],
                "prescribedValue": _serialize_prescribed_value(entry.get("value")),
                "executedValue": None,
            }
        )
    return out


async def tp_create_strength_workout(
    date: str,
    title: str,
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a structured strength workout via TrainingPeaks' strength builder.

    Args:
        date: Target date (YYYY-MM-DD).
        title: Workout title.
        blocks: List of block dicts. Each block has:
            - blockType: SingleExercise | WarmUp | CoolDown | Superset
            - title: str
            - coachNotes: str | None (optional)
            - exercises: list of {exercise_id, exercise_title,
              sets: list of {parameter, value},
              video_url: str | None (optional),
              instructions: str | None (optional)}

    Returns:
        Dict with confirmation or error.
    """
    from datetime import date as date_type

    try:
        date_type.fromisoformat(date)
    except ValueError:
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": f"Invalid date: {date}",
        }

    if not title or not title.strip():
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": "Workout title must not be empty.",
        }

    if not isinstance(blocks, list) or not blocks:
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": "blocks must be a non-empty list.",
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        # Resolve each exercise's parameter definitions once up front so the
        # libraryContent endpoint is hit at most once per tool call.
        param_cache: dict[str, Any] = {}
        exercise_params: dict[str, list[dict[str, Any]] | None] = {}
        for block in blocks:
            for ex in block.get("exercises", []) or []:
                eid = str(ex.get("exercise_id", ""))
                if eid and eid not in exercise_params:
                    exercise_params[eid] = await get_exercise_params(
                        client, eid, cache=param_cache
                    )

        api_blocks = [_build_strength_block(b, exercise_params) for b in blocks]

        payload: dict[str, Any] = {
            "workoutType": "StructuredStrength",
            "calendarId": athlete_id,
            "title": title.strip(),
            "prescribedDate": date,
            "orderOnDay": 1,
            "isHidden": False,
            "isLocked": False,
            "complianceState": "Unplanned",
            "blocks": api_blocks,
        }

        endpoint = "/rx/activity/v1/workouts/save"
        response = await client.post(endpoint, json=payload, base_url=RX_API_BASE)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        workout_id = None
        if isinstance(response.data, dict):
            workout_id = response.data.get("id") or response.data.get("workoutId")

        return {
            "success": True,
            "title": title.strip(),
            "date": date,
            "block_count": len(api_blocks),
            "workout_id": workout_id,
        }
