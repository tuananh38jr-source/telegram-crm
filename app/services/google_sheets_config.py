"""
Google Sheets configuration management.

Loads and validates sheet configurations from google_sheets_config.json.
Each sheet entry maps a Google Sheets URL to a data type (sales or employees)
and defines how sheet columns map to CRM database fields.
"""
import json
import os
import logging

logger = logging.getLogger(__name__)

# Resolve paths relative to the project root (telegram-crm/)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "google_sheets_config.json")

# Valid data types that the sync engine knows how to handle
VALID_DATA_TYPES = ("sales", "employees")


def load_config(config_path: str = None) -> dict:
    """
    Load Google Sheets configuration from a JSON file.

    Args:
        config_path: Absolute or relative path to the config JSON file.
                     Defaults to google_sheets_config.json in the project root.

    Returns:
        Parsed configuration dict with keys:
          - service_account_key (str): path to the service account JSON key
          - sync_interval_minutes (int): how often to sync
          - sheets (list[dict]): list of sheet configurations

    Raises:
        FileNotFoundError: if the config file does not exist
        ValueError: if the config file is not valid JSON
    """
    path = config_path or DEFAULT_CONFIG_PATH
    if not os.path.isabs(path):
        path = os.path.join(_PROJECT_ROOT, path)

    if not os.path.exists(path):
        raise FileNotFoundError(
            "Google Sheets config not found: {}. "
            "Create it or pass a custom config_path.".format(path)
        )

    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    _validate_config(config)
    return config


def save_config(config: dict, config_path: str = None) -> str:
    """
    Save a configuration dict back to the JSON file.

    Args:
        config: The configuration dict to persist.
        config_path: Target file path. Defaults to the standard location.

    Returns:
        The absolute path where the config was written.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    if not os.path.isabs(path):
        path = os.path.join(_PROJECT_ROOT, path)

    _validate_config(config)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

    logger.info("Google Sheets config saved to %s", path)
    return path


def get_service_account_key(config: dict) -> str:
    """
    Resolve the service account JSON key path from the config.

    The key path can be absolute or relative to the project root.

    Returns:
        Absolute path to the service account JSON key file.
    """
    key_path = config.get("service_account_key", "")
    if not key_path:
        raise ValueError(
            "service_account_key is empty in config. "
            "Set it to the path of your Google service account JSON key."
        )
    if not os.path.isabs(key_path):
        key_path = os.path.join(_PROJECT_ROOT, key_path)
    return key_path


def get_enabled_sheets(config: dict, data_type: str = None) -> list:
    """
    Return the list of enabled sheet configurations, optionally filtered by data_type.

    Args:
        config: The loaded configuration dict.
        data_type: If provided, only return sheets matching this type
                   (e.g. 'sales' or 'employees').

    Returns:
        List of sheet config dicts, each containing:
          - name (str): human-readable label
          - sheet_url (str): full Google Sheets URL or sheet ID
          - data_type (str): 'sales' or 'employees'
          - worksheet (str): worksheet name or index
          - column_mapping (dict): sheet_column -> db_field mapping
    """
    sheets = config.get("sheets", [])
    result = []
    for sheet in sheets:
        if not sheet.get("enabled", True):
            continue
        if not sheet.get("sheet_url", "").strip():
            continue
        if data_type and sheet.get("data_type") != data_type:
            continue
        result.append(sheet)
    return result


def add_sheet_config(
    config: dict,
    name: str,
    sheet_url: str,
    data_type: str,
    column_mapping: dict,
    worksheet: str = "Sheet1",
    enabled: bool = True,
) -> dict:
    """
    Add a new sheet configuration to the config dict (in memory).

    Call save_config() afterwards to persist the change.

    Args:
        config: The current configuration dict (will be mutated).
        name: Human-readable name for this sheet.
        sheet_url: Google Sheets URL or ID.
        data_type: One of 'sales' or 'employees'.
        column_mapping: Dict mapping sheet column names to DB field names.
        worksheet: Worksheet name within the spreadsheet.
        enabled: Whether this sheet should be included in syncs.

    Returns:
        The updated config dict.
    """
    if data_type not in VALID_DATA_TYPES:
        raise ValueError(
            "Invalid data_type '{}'. Must be one of: {}".format(
                data_type, ", ".join(VALID_DATA_TYPES)
            )
        )

    sheet_entry = {
        "name": name,
        "sheet_url": sheet_url,
        "data_type": data_type,
        "worksheet": worksheet,
        "enabled": enabled,
        "column_mapping": column_mapping,
    }

    if "sheets" not in config:
        config["sheets"] = []
    config["sheets"].append(sheet_entry)
    return config


def extract_sheet_id(sheet_url: str) -> str:
    """
    Extract the Google Sheets spreadsheet ID from a URL or return it as-is.

    Handles these URL formats:
      - https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
      - https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit#gid=0
      - Plain spreadsheet ID string

    Returns:
        The spreadsheet ID string.
    """
    url = sheet_url.strip()
    if "/spreadsheets/d/" in url:
        parts = url.split("/spreadsheets/d/")[1]
        sheet_id = parts.split("/")[0].split("#")[0].split("?")[0]
        return sheet_id
    # Assume it is already a plain ID
    return url


def _validate_config(config: dict) -> None:
    """
    Validate the structure of a configuration dict.

    Raises ValueError if required fields are missing or invalid.
    """
    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object (dict).")

    sheets = config.get("sheets", [])
    if not isinstance(sheets, list):
        raise ValueError("'sheets' must be a list.")

    for idx, sheet in enumerate(sheets):
        if not isinstance(sheet, dict):
            raise ValueError("Sheet entry {} must be a dict.".format(idx))

        dt = sheet.get("data_type", "")
        if dt and dt not in VALID_DATA_TYPES:
            raise ValueError(
                "Sheet entry {} has invalid data_type '{}'. "
                "Must be one of: {}".format(idx, dt, ", ".join(VALID_DATA_TYPES))
            )

        mapping = sheet.get("column_mapping", {})
        if mapping and not isinstance(mapping, dict):
            raise ValueError(
                "Sheet entry {} column_mapping must be a dict.".format(idx)
            )
