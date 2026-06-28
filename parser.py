"""
Low-level `.def` XML parser.

This module exposes a single public function, `parse_def_file`, which reads a
single XML definition file and returns an `EntityDef` instance. The order of
properties and methods is preserved exactly as it appears in the source XML.

World of Tanks `.def` files frequently use XML namespace prefixes (e.g.
``<ref:Type>``, ``<default:Value>``) **without** declaring the corresponding
``xmlns:`` attributes.  Standard ``xml.etree.ElementTree`` rejects such input
with ``ParseError: unbound prefix``.

To handle this gracefully the parser preprocesses the raw file text, stripping
namespace prefixes from both opening and closing tags before handing the
result to ``ElementTree``.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

from .entity_def import EntityDef, MethodDef, PropertyDef

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Preprocessing: strip namespace prefixes (e.g. "ref:", "default:", "id:")
# ---------------------------------------------------------------------------

# Matches a namespace prefix (letters, digits, underscores, hyphens) followed
# by a colon, immediately before the element name in an opening or closing tag.
_NS_PREFIX_RE = re.compile(
    r"(</?)\s*[A-Za-z_][\w\-.]*:",
)


def _strip_namespace_prefixes(xml_text: str) -> str:
    """
    Remove namespace prefixes from every XML tag in *xml_text*.

    ``<ref:Type>INT32</ref:Type>``  →  ``<Type>INT32</Type>``
    ``<default:Value ...>``         →  ``<Value ...>``

    Attributes (e.g. ``xmlns:ref``) and text content are left untouched.
    """
    return _NS_PREFIX_RE.sub(r"\1", xml_text)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _strip_text(text: str | None) -> str:
    """Return stripped text or an empty string if the input is None."""
    return (text or "").strip()


def _parse_properties(parent: ET.Element | None) -> List[PropertyDef]:
    """Parse the <Properties> block preserving XML order."""
    properties: List[PropertyDef] = []
    if parent is None:
        return properties

    # Each child of <Properties> is a property whose tag name is the property name.
    for prop_element in parent:
        name = prop_element.tag
        type_element = prop_element.find("Type")
        flags_element = prop_element.find("Flags")

        type_name = _strip_text(type_element.text if type_element is not None else None)
        flags = _strip_text(flags_element.text if flags_element is not None else None)

        properties.append(PropertyDef(name=name, type_name=type_name, flags=flags))

    return properties


def _parse_methods(parent: ET.Element | None) -> List[MethodDef]:
    """Parse a <ClientMethods> or <BaseMethods> block preserving XML order."""
    methods: List[MethodDef] = []
    if parent is None:
        return methods

    # Each child is a method whose tag name is the method name.
    for method_element in parent:
        name = method_element.tag
        args: List[str] = []
        for arg_element in method_element:
            # Only <Arg> tags contribute to the argument list.
            if arg_element.tag == "Arg":
                args.append(_strip_text(arg_element.text))
        methods.append(MethodDef(name=name, args=args))

    return methods


def parse_def_file(path: Path) -> EntityDef | None:
    """
    Parse a single `.def` XML file into an `EntityDef`.

    The parser first tries a straight ``ET.parse()``.  If it fails with an
    ``unbound prefix`` error the raw file text is preprocessed to strip
    undeclared namespace prefixes and parsing is retried.

    Args:
        path: Path to the XML definition file.

    Returns:
        An `EntityDef` instance if parsing succeeds, otherwise `None`.
        Failures are logged as warnings and never raised.
    """
    # --- read raw text (needed for the fallback codepath) --------------------
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read file %s: %s", path, exc)
        return None

    # --- attempt 1: normal parse ---------------------------------------------
    try:
        tree = ET.ElementTree(ET.fromstring(raw_text))
    except ET.ParseError as exc:
        error_msg = str(exc)
        if "unbound prefix" in error_msg.lower():
            logger.debug(
                "Namespace prefixes detected in %s – retrying with stripped prefixes.",
                path,
            )
            # --- attempt 2: strip namespace prefixes --------------------------
            cleaned = _strip_namespace_prefixes(raw_text)
            try:
                tree = ET.ElementTree(ET.fromstring(cleaned))
            except ET.ParseError as exc2:
                logger.warning("Failed to parse XML in %s even after stripping prefixes: %s", path, exc2)
                return None
        else:
            logger.warning("Failed to parse XML in %s: %s", path, exc)
            return None

    root = tree.getroot()
    if root is None or root.tag != "root":
        logger.warning("File %s does not contain a <root> element; skipped.", path)
        return None

    try:
        properties = _parse_properties(root.find("Properties"))
        client_methods = _parse_methods(root.find("ClientMethods"))
        base_methods = _parse_methods(root.find("BaseMethods"))
    except Exception as exc:  # noqa: BLE001 - defensive parsing
        logger.warning("Unexpected error while parsing %s: %s", path, exc)
        return None

    entity_name = path.stem
    return EntityDef(
        name=entity_name,
        properties=properties,
        client_methods=client_methods,
        base_methods=base_methods,
    )
