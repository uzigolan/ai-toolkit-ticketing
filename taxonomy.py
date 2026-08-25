"""
Ticket taxonomy loader.

Categories, sub-categories, device families and severities are vocabulary, not
code, so they live in categories.yml and are read at startup. If the file is
missing the built-in defaults are used, so a fresh clone runs without it; if the
file is present but malformed we raise, because silently ignoring a typo would
leave people filing tickets against a taxonomy nobody intended.
"""
import os

try:
    import yaml
except ImportError:  # pragma: no cover - surfaced at load() time
    yaml = None

DEFAULT_FILE = "categories.yml"

BUILTIN = {
    "toolkits": [
        ("rad-agent-toolkit", "rad-agent-toolkit"),
        ("radview-ai-toolkit", "radview-ai-toolkit"),
        ("pikachu-ai-toolkit", "pikachu-ai-toolkit"),
        ("synergy-ccm-mcp", "synergy-ccm-mcp"),
        ("self-hosted-ai-poc", "self-hosted-ai-poc"),
        ("other", "Something else / not listed yet"),
    ],
    "default_toolkit": "rad-agent-toolkit",
    "categories": [
        ("wrong_result", "Wrong result"),
        ("bad_format", "Right, but wrong format"),
        ("slow_result", "Right, but too slow"),
        ("many_retries", "Took too many retries"),
    ],
    "rad_families": ["ETX", "SecFlow", "Megaplex", "MiNID", "ETX-2V", "n/a"],
    "knowledge_scope": [
        {"id": "rad", "label": "RAD knowledge", "default": True},
        {"id": "market", "label": "Market knowledge", "default": False},
    ],
    "knowledge_sources": [
        ("manual", "User manual"), ("datasheet", "Datasheet"),
        ("release_notes", "Release notes / TRS"), ("cli", "CLI reference"),
        ("mib", "MIB / SNMP"), ("mea", "MEA / debug tree"),
        ("skills", "Agent skills & instructions"), ("vendor", "Vendor docs"),
    ],
    "operations": [
        ("lookup", "Knowledge lookup (no device)"),
        ("design", "Architecture / design / planning"),
        ("device_read", "Device read (show / get info)"),
        ("device_write", "Device change (set / stage / commit)"),
    ],
    "self_hosted_areas": [
        ("chip_design_ai", "Chip-Design AI Applications"),
        ("coding_assistance", "AI Coding Assistance"),
        ("radchat_rag_cli", "RADChat / RAG / Local CLI Generation"),
        ("sdlc_workflows", "SDLC Workflows"),
        ("cost_value", "Cost and Value Benchmark"),
        ("performance", "Performance Benchmark"),
        ("security", "Security Gate"),
        ("final_scorecard", "Final Scorecard"),
    ],
    "self_hosted_metrics": [
        ("engineering_quality_score", "Engineering Quality Score"),
        ("weighted_capability_coverage", "Weighted Capability Coverage"),
        ("model_tool_efficiency", "Model and Tool Efficiency Metrics"),
        ("coding_assistance_score", "Coding Assistance Score"),
        ("sdlc_score", "SDLC Score"),
        ("cost_comparison", "Cost Comparison"),
        ("performance_measurement", "Performance Measurement"),
        ("security_gate_check", "Security Gate Check"),
        ("final_score", "Final Score"),
    ],
    "self_hosted_results": [
        ("full_pass", "Full pass"),
        ("pass_minor", "Pass with minor correction"),
        ("partial", "Partial result"),
        ("failure", "Failure"),
        ("critical_failure", "Critical or unsafe failure"),
    ],
    "severities": [("low", "Low"), ("normal", "Normal"), ("high", "High — blocking my work")],
    "resolutions": [
        {"id": "entered", "label": "Entered",
         "status": "entered", "requires_version": False,
         "requires_answer": False, "requires_note": False},
        {"id": "solved", "label": "Solved",
         "status": "solved", "requires_version": True,
         "requires_answer": False, "requires_note": False},
        {"id": "in_verification", "label": "In verification",
         "status": "in_verification", "requires_version": False,
         "requires_answer": False, "requires_note": False},
        {"id": "known_limitation", "label": "Known limitation",
         "status": "known_limitation", "requires_version": False,
         "requires_answer": False, "requires_note": True},
        {"id": "working_on_it", "label": "Working on it",
         "status": "working_on_it", "requires_version": False,
         "requires_answer": False, "requires_note": True},
    ],
}


class TaxonomyError(ValueError):
    """Raised when categories.yml exists but cannot be used."""


def _entries(raw, where: str, path: str) -> list:
    """
    Normalize a list of {id, label} mappings into (id, label) tuples. A bare
    string is accepted as shorthand for an entry whose id and label match.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TaxonomyError(f"{path}: '{where}' must be a list, got {type(raw).__name__}.")

    out, seen = [], set()
    for index, item in enumerate(raw):
        position = f"{where}[{index}]"
        if isinstance(item, str):
            entry_id = label = item.strip()
        elif isinstance(item, dict):
            entry_id = str(item.get("id", "")).strip()
            label = str(item.get("label", entry_id)).strip()
        else:
            raise TaxonomyError(f"{path}: {position} must be a string or a mapping.")
        if not entry_id:
            raise TaxonomyError(f"{path}: {position} is missing an 'id'.")
        if entry_id in seen:
            raise TaxonomyError(f"{path}: duplicate id '{entry_id}' in {where}.")
        seen.add(entry_id)
        out.append((entry_id, label or entry_id))
    return out


def _flagged(raw, where: str, path: str, flag_names: tuple) -> list:
    """Entries that carry extra boolean properties alongside id and label."""
    out = []
    for entry_id, label in _entries(raw, where, path):
        flags = {name: False for name in flag_names}
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and str(item.get("id", "")).strip() == entry_id:
                    for name in flag_names:
                        flags[name] = bool(item.get(name, False))
                    break
        out.append({"id": entry_id, "label": label, **flags})
    return out


def _resolutions(raw, path: str) -> list:
    """Like _entries, but each outcome carries the status it sets and its requirements."""
    out = []
    for entry_id, label in _entries(raw, "resolutions", path):
        flags = {"requires_version": False, "requires_answer": False, "requires_note": False}
        status = entry_id
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and str(item.get("id", "")).strip() == entry_id:
                    for flag in flags:
                        flags[flag] = bool(item.get(flag, False))
                    status = str(item.get("status", entry_id)).strip() or entry_id
                    break
        out.append({"id": entry_id, "label": label, "status": status, **flags})
    return out


def load(path: str = None, logger=None) -> dict:
    """Return {'categories', 'rad_families', 'severities'} from YAML or defaults."""
    path = path or os.environ.get("TICKETING_CATEGORIES_FILE") or DEFAULT_FILE

    if not os.path.exists(path):
        if logger:
            logger.warning("No %s found; using the built-in ticket taxonomy.", path)
        return dict(BUILTIN)

    if yaml is None:
        raise TaxonomyError(
            f"{path} exists but PyYAML is not installed. Run: pip install -r requirements.txt"
        )

    with open(path, "r", encoding="utf-8") as handle:
        try:
            data = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise TaxonomyError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise TaxonomyError(f"{path}: expected a mapping at the top level.")

    raw_categories = data.get("categories")
    if not raw_categories:
        raise TaxonomyError(f"{path}: 'categories' is required and must not be empty.")
    categories = _entries(raw_categories, "categories", path)

    families = [str(f).strip() for f in (data.get("rad_families") or []) if str(f).strip()]
    severities = _entries(data.get("severities"), "severities", path)
    resolutions = _resolutions(data.get("resolutions"), path)
    sources = _entries(data.get("knowledge_sources"), "knowledge_sources", path)
    operations = _entries(data.get("operations"), "operations", path)
    toolkits = _entries(data.get("toolkits"), "toolkits", path)
    scope = _flagged(data.get("knowledge_scope"), "knowledge_scope", path, ("default",))
    self_hosted_areas = _entries(data.get("self_hosted_areas"), "self_hosted_areas", path)
    self_hosted_metrics = _entries(data.get("self_hosted_metrics"), "self_hosted_metrics", path)
    self_hosted_results = _entries(data.get("self_hosted_results"), "self_hosted_results", path)

    # The toolkit the form starts on; falls back to the first one listed.
    default_toolkit = next(
        (t["id"] for t in _flagged(data.get("toolkits"), "toolkits", path, ("default",))
         if t["default"]),
        toolkits[0][0] if toolkits else BUILTIN["default_toolkit"],
    )

    taxonomy = {
        "toolkits": toolkits or list(BUILTIN["toolkits"]),
        "default_toolkit": default_toolkit,
        "categories": categories,
        "rad_families": families or list(BUILTIN["rad_families"]),
        "severities": severities or list(BUILTIN["severities"]),
        "resolutions": resolutions or [dict(r) for r in BUILTIN["resolutions"]],
        "knowledge_sources": sources or list(BUILTIN["knowledge_sources"]),
        "knowledge_scope": scope or [dict(s) for s in BUILTIN["knowledge_scope"]],
        "operations": operations or list(BUILTIN["operations"]),
        "self_hosted_areas": self_hosted_areas or list(BUILTIN["self_hosted_areas"]),
        "self_hosted_metrics": self_hosted_metrics or list(BUILTIN["self_hosted_metrics"]),
        "self_hosted_results": self_hosted_results or list(BUILTIN["self_hosted_results"]),
    }
    if logger:
        logger.info(
            "Loaded ticket taxonomy from %s: %d toolkits, %d categories.",
            path,
            len(taxonomy["toolkits"]),
            len(categories),
        )
    return taxonomy
