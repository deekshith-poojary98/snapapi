from __future__ import annotations

from xml.etree import ElementTree as ET

from snapapi.exceptions import XPathError


def extract(xml_text, path):
    """Resolve a limited XPath against an XML document using stdlib ElementTree.

    Supports descendant paths such as ``//Order`` and attribute selectors
    ``//Order/@id``. Namespaces and axes beyond ElementTree's subset are not
    implemented.
    """
    if xml_text is None:
        raise XPathError("XPath requires an XML body")
    if isinstance(xml_text, bytes):
        xml_text = xml_text.decode("utf-8")
    text = str(xml_text).strip()
    if not text:
        raise XPathError("XPath requires an XML body")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise XPathError(f"Invalid XML: {exc}") from exc

    expr = (path or "").strip()
    if not expr:
        raise XPathError("XPath is empty")
    attr = None
    if "/@" in expr:
        expr, attr = expr.rsplit("/@", 1)
        attr = attr.strip()
        if not attr:
            raise XPathError(f"Invalid XPath {path!r}")

    nodes = _findall(root, expr)
    if attr:
        values = [node.get(attr) for node in nodes if node.get(attr) is not None]
        if not values:
            raise XPathError(f"XPath {path} not found")
        return values[0] if len(values) == 1 else values
    if not nodes:
        raise XPathError(f"XPath {path} not found")
    texts = ["" if node.text is None else node.text for node in nodes]
    return texts[0] if len(texts) == 1 else texts


def _findall(root, path):
    expr = (path or "").strip()
    if expr in ("", "/", "."):
        return [root]
    if expr.startswith("//"):
        tag = expr[2:]
        if tag and "/" not in tag and "[" not in tag and not tag.startswith("@"):
            return [node for node in root.iter() if _local_tag(node.tag) == tag]
        return root.findall("." + expr)
    if expr.startswith("/"):
        expr = expr[1:]
    return root.findall(expr)


def _local_tag(tag):
    if isinstance(tag, str) and tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag
