import logging
import re
import os

import yaml

from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)


def extract_rules_from_markdown(filepath):
    """Extract YAML rule blocks from a Markdown analysis file."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    rules = []

    # Find YAML code blocks (```yaml ... ``` or ```yml ... ```)
    yaml_blocks = re.findall(r"```(?:yaml|yml)\s*\n(.*?)```", content, re.DOTALL)

    for block in yaml_blocks:
        try:
            data = yaml.safe_load(block)
            if data and isinstance(data, dict) and "rules" in data:
                for rule in data["rules"]:
                    rule["source_file"] = filepath
                    rules.append(rule)
            elif data and isinstance(data, list):
                for rule in data:
                    if isinstance(rule, dict) and "name" in rule:
                        rule["source_file"] = filepath
                        rules.append(rule)
        except yaml.YAMLError as e:
            log_error(logger, f"[ERROR] Invalid YAML block in {filepath}: {e}")

    return rules
