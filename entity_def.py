"""
Data classes for representing parsed entity definitions.

These dataclasses store the structure of an entity as described by a `.def`
XML file. Lists are used for properties and methods to guarantee that the
original top-to-bottom order from the XML is preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class PropertyDef:
    """Represents a single entity property parsed from <Properties>."""

    name: str
    type_name: str
    flags: str

    def has_flag(self, flag: str) -> bool:
        """Return True if *flag* is a substring of the property's Flags."""
        return flag in self.flags

    def __repr__(self) -> str:  # pragma: no cover
        return f"PropertyDef(name={self.name!r}, type={self.type_name!r}, flags={self.flags!r})"


@dataclass
class MethodDef:
    """Represents an entity method parsed from <ClientMethods> or <BaseMethods>."""

    name: str
    args: List[str] = field(default_factory=list)

    def __repr__(self) -> str:  # pragma: no cover
        return f"MethodDef(name={self.name!r}, args={self.args!r})"


@dataclass
class EntityDef:
    """Represents the full definition of a single entity."""

    name: str
    properties: List[PropertyDef] = field(default_factory=list)
    client_methods: List[MethodDef] = field(default_factory=list)
    base_methods: List[MethodDef] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Property helpers
    # ------------------------------------------------------------------
    def get_properties_with_flag(self, flag: str) -> List[PropertyDef]:
        """Return properties whose Flags contain the given substring."""
        return [prop for prop in self.properties if prop.has_flag(flag)]

    def get_client_properties(self) -> List[PropertyDef]:
        """Return properties that should be sent to the client."""
        return self.get_properties_with_flag("CLIENT")

    def get_base_properties(self) -> List[PropertyDef]:
        """Return properties available on the base server component."""
        return self.get_properties_with_flag("BASE")

    def get_property(self, name: str) -> PropertyDef | None:
        """Return a property by name, or None if not found."""
        for prop in self.properties:
            if prop.name == name:
                return prop
        return None

    # ------------------------------------------------------------------
    # Method helpers
    # ------------------------------------------------------------------
    def get_client_method(self, name: str) -> MethodDef | None:
        """Return a client method by name, or None if not found."""
        for method in self.client_methods:
            if method.name == name:
                return method
        return None

    def get_base_method(self, name: str) -> MethodDef | None:
        """Return a base method by name, or None if not found."""
        for method in self.base_methods:
            if method.name == name:
                return method
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"EntityDef(name={self.name!r}, "
            f"properties={len(self.properties)}, "
            f"client_methods={len(self.client_methods)}, "
            f"base_methods={len(self.base_methods)})"
        )
