"""
Organization hierarchy management.

Manages parent/subsidiary relationships between organizations within a
tenant. Supports complex healthcare structures such as:

  IDN (Integrated Delivery Network)
    ├── Hospital System A
    │     ├── Hospital 1 (covered entity)
    │     └── Hospital 2 (covered entity)
    └── Pharmacy Network B
          ├── Contract Pharmacy 1
          └── Contract Pharmacy 2

All hierarchy operations are tenant-scoped — organizations across
different tenants can never be linked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from saas.tenancy.models import Organization

log = logging.getLogger("evidentrx.saas.organizations.hierarchy")


@dataclass
class OrgNode:
    """
    A node in the organization hierarchy tree.

    Carries the organization and its resolved children for tree traversal.
    """
    org:      Organization
    children: list[OrgNode] = field(default_factory=list)
    depth:    int             = 0

    @property
    def org_id(self) -> str:
        return self.org.org_id

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id":   self.org.org_id,
            "name":     self.org.name,
            "org_type": self.org.org_type.value,
            "depth":    self.depth,
            "children": [c.to_dict() for c in self.children],
        }


class OrgHierarchyManager:
    """
    Builds and queries organization hierarchy trees for a tenant.

    The hierarchy is computed on-demand from the flat org list stored in
    the OrganizationRegistry. No separate hierarchy table is required —
    parent_org_id links are sufficient to reconstruct any tree.
    """

    def __init__(self, org_registry: Any) -> None:
        self._registry = org_registry

    # ── Tree construction ──────────────────────────────────────────────────────

    def build_tree(self, tenant_id: str) -> list[OrgNode]:
        """
        Build the full org tree for a tenant.

        Returns a list of root nodes (orgs with no parent). Each node
        has its children recursively populated.
        """
        all_orgs = self._registry.list_for_tenant(tenant_id)
        by_id    = {o.org_id: o for o in all_orgs}

        # Build child → parent index
        children_of: dict[str, list[str]] = {o.org_id: [] for o in all_orgs}
        roots: list[str] = []

        for org in all_orgs:
            if org.parent_org_id and org.parent_org_id in by_id:
                children_of[org.parent_org_id].append(org.org_id)
            else:
                roots.append(org.org_id)

        def _build_node(org_id: str, depth: int) -> OrgNode:
            node = OrgNode(org=by_id[org_id], depth=depth)
            for child_id in sorted(children_of.get(org_id, []),
                                   key=lambda i: by_id[i].name):
                node.children.append(_build_node(child_id, depth + 1))
            return node

        return [_build_node(rid, 0) for rid in sorted(roots, key=lambda i: by_id[i].name)]

    # ── Subtree operations ─────────────────────────────────────────────────────

    def subtree(self, tenant_id: str, root_org_id: str) -> OrgNode | None:
        """
        Return the subtree rooted at a specific org.

        Returns None if the org does not exist or does not belong to the tenant.
        """
        tree = self.build_tree(tenant_id)
        return _find_node(tree, root_org_id)

    def all_org_ids_in_subtree(self, tenant_id: str, root_org_id: str) -> list[str]:
        """
        Return every org_id in a subtree (including the root).

        Used to scope queries to "org and all children" without requiring
        a recursive CTE at the DB layer.
        """
        node = self.subtree(tenant_id, root_org_id)
        if node is None:
            return []
        return _collect_ids(node)

    def all_entity_ids_in_subtree(self, tenant_id: str, root_org_id: str) -> list[str]:
        """Collect all covered_entity_ids across the entire subtree."""
        org_ids = self.all_org_ids_in_subtree(tenant_id, root_org_id)
        entity_ids: list[str] = []
        for oid in org_ids:
            org = self._registry.get(oid)
            if org:
                entity_ids.extend(org.covered_entity_ids)
        return list(dict.fromkeys(entity_ids))   # deduplicate, preserve order

    # ── Validation ─────────────────────────────────────────────────────────────

    def validate_reparent(
        self,
        tenant_id:     str,
        org_id:        str,
        new_parent_id: str,
    ) -> tuple[bool, str]:
        """
        Check whether re-parenting org to new_parent would create a cycle.

        Returns (is_valid, reason).
        """
        # new_parent must be in the same tenant and not a descendant of org
        descendants = self.all_org_ids_in_subtree(tenant_id, org_id)
        if new_parent_id in descendants:
            return False, (
                f"Cannot re-parent: {new_parent_id[:8]} is a descendant of {org_id[:8]}"
            )
        if new_parent_id == org_id:
            return False, "An organization cannot be its own parent"
        return True, "ok"

    # ── Stats ──────────────────────────────────────────────────────────────────

    def depth_of(self, tenant_id: str, org_id: str) -> int:
        """Return the depth of an org in the hierarchy (0 = root)."""
        node = self.subtree(tenant_id, org_id)
        return node.depth if node else 0

    def max_depth(self, tenant_id: str) -> int:
        """Maximum depth of any org in the tenant's hierarchy."""
        tree = self.build_tree(tenant_id)
        return max((_max_depth(n) for n in tree), default=0)


# ── Tree helpers ───────────────────────────────────────────────────────────────

def _find_node(nodes: list[OrgNode], org_id: str) -> OrgNode | None:
    for node in nodes:
        if node.org_id == org_id:
            return node
        found = _find_node(node.children, org_id)
        if found:
            return found
    return None


def _collect_ids(node: OrgNode) -> list[str]:
    ids = [node.org_id]
    for child in node.children:
        ids.extend(_collect_ids(child))
    return ids


def _max_depth(node: OrgNode) -> int:
    if not node.children:
        return node.depth
    return max(_max_depth(c) for c in node.children)
