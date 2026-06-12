import glob
import logging
import os

import yaml

from src.core.config import MITE_CONFIG_DIR, MITE_RULES_DIR, MITE_ANALYSIS_DIR
from src.core.markdown_rules import extract_rules_from_markdown
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)


def load_yaml_rules(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or "rules" not in data:
        return []
    rules = data["rules"]
    for rule in rules:
        rule["source_file"] = filepath
    return rules


def load_all_rules():
    rules = []
    errors = []

    # Load from /app/config/rules.yml
    config_rules_path = os.path.join(MITE_CONFIG_DIR, "rules.yml")
    if os.path.exists(config_rules_path):
        try:
            loaded = load_yaml_rules(config_rules_path)
            rules.extend(loaded)
            log_info(logger, f"[INFO] Loaded {len(loaded)} rules from {config_rules_path}")
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to load {config_rules_path}: {e}")
            errors.append({"file": config_rules_path, "error": str(e)})

    # Load from /app/rules/*.yml
    for filepath in sorted(glob.glob(os.path.join(MITE_RULES_DIR, "*.yml"))):
        try:
            loaded = load_yaml_rules(filepath)
            rules.extend(loaded)
            log_info(logger, f"[INFO] Loaded {len(loaded)} rules from {filepath}")
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to load {filepath}: {e}")
            errors.append({"file": filepath, "error": str(e)})

    # Load from /app/analysis/*.md
    for filepath in sorted(glob.glob(os.path.join(MITE_ANALYSIS_DIR, "*.md"))):
        try:
            loaded = extract_rules_from_markdown(filepath)
            rules.extend(loaded)
            log_info(logger, f"[INFO] Loaded {len(loaded)} rules from {filepath}")
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to load rules from {filepath}: {e}")
            errors.append({"file": filepath, "error": str(e)})

    return rules, errors
