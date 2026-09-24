"""
Layer 1 of the pipeline: derive usage and data access features from the GraphQL query the
main model generated. No text, no model, no network. Parsing is schema-free, so this runs
against any query string; the sensitivity and remit tables are the only product specific
inputs and are placeholders here until the schema owners supply them.

Argument values are stripped. A filter such as `where: {name: "Mara Venn"}` becomes the
argument name `where` only, so the feature record carries no literals from the prompt.
Numeric pagination arguments (first, limit, last) are kept, since a number is not personal
data and scope is the dimension the text-only check misses.

Usage:
    uv run python poc/graphql_features.py        # runs the built-in examples
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from graphql import FieldNode, OperationDefinitionNode, parse, visit
from graphql.language.visitor import Visitor

PAGINATION_ARGS = {"first", "last", "limit", "take", "pageSize"}
FILTER_ARGS = {"where", "filter", "id", "ids", "code", "search"}

# --- Product specific tables. Placeholders keyed on field paths as they appear in queries.
# Production: keyed on schema types (Employee.salary), maintained by the schema owners.
SENSITIVITY: dict[str, str] = {
    "employees.salary": "employee_pay",
    "employees.bonus": "employee_pay",
    "employees.bankAccount": "employee_bank",
    "employees.homeAddress": "employee_personal",
    "employees.personalEmail": "employee_personal",
    "employees.mobile": "employee_personal",
    "employees.medicalLeave": "employee_personal",
    "employees.disciplinary": "employee_personal",
    "suppliers.bankAccount": "supplier_bank",
    "suppliers.negotiatedPrice": "supplier_commercial",
    "customers.creditLimit": "customer_financial",
    "customers.paymentHistory": "customer_financial",
}

ROLE_REMIT: dict[str, set[str]] = {
    "hr_admin": {"employee_pay", "employee_personal", "employee_bank"},
    "finance_clerk": {"employee_pay", "employee_bank", "customer_financial", "supplier_bank",
                      "supplier_commercial"},
    "sales_rep": {"customer_financial"},
    "purchasing_manager": {"supplier_commercial", "supplier_bank"},
    "warehouse_operator": set(),
    "system_admin": set(),
}

MODULE_BY_ROOT: dict[str, str] = {
    "employees": "hr", "employee": "hr", "payroll": "hr", "teamMembers": "hr",
    "invoices": "finance", "invoice": "finance", "payments": "finance",
    "items": "inventory", "stockMovements": "inventory", "stockLevels": "inventory",
    "purchaseOrders": "purchasing", "suppliers": "purchasing",
    "salesOrders": "sales", "customers": "sales",
    "users": "admin", "roles": "admin", "permissions": "admin", "authorizationGroups": "admin",
    # mutation roots are verbs; the schema owners' table should list them all
    "postInvoice": "finance", "postStockMovement": "inventory", "approvePurchaseOrder": "purchasing",
    "confirmSalesOrder": "sales", "updateEmployee": "hr", "assignRole": "admin",
}


@dataclass
class QueryFeatures:
    operation: str                       # query | mutation | subscription
    root_fields: list[str]               # entry points, e.g. employees, salesOrders
    modules: list[str]                   # derived from root_fields
    field_paths: list[str]               # dotted selection paths, no values
    argument_names: list[str]            # names only, values stripped
    has_filter: bool                     # any filter-like argument on a root field
    page_size: int | None                # numeric pagination argument if present
    sensitivity_classes: list[str] = field(default_factory=list)
    role_mismatch: list[str] = field(default_factory=list)


class _Collector(Visitor):
    def __init__(self) -> None:
        super().__init__()
        self.operation = "query"
        self.stack: list[str] = []
        self.root_fields: list[str] = []
        self.paths: list[str] = []
        self.arg_names: set[str] = set()
        self.has_filter = False
        self.page_size: int | None = None

    def enter_operation_definition(self, node: OperationDefinitionNode, *_):
        self.operation = node.operation.value

    def enter_field(self, node: FieldNode, *_):
        name = node.name.value
        self.stack.append(name)
        if len(self.stack) == 1:
            self.root_fields.append(name)
        else:
            self.paths.append(".".join(self.stack))
        for arg in node.arguments or ():
            self.arg_names.add(arg.name.value)
            if len(self.stack) == 1 and arg.name.value in FILTER_ARGS:
                self.has_filter = True
            if arg.name.value in PAGINATION_ARGS and arg.value.kind == "int_value":
                self.page_size = int(arg.value.value)

    def leave_field(self, *_):
        self.stack.pop()


def extract(query: str, role: str | None = None) -> QueryFeatures:
    collector = _Collector()
    visit(parse(query), collector)
    modules = sorted({MODULE_BY_ROOT.get(r, "unknown") for r in collector.root_fields})
    # Path lookup ignores intermediate nesting so employees.manager.salary still matches
    # employees.salary; production keys on schema types and does not need this.
    classes = sorted({
        cls for path in collector.paths
        for key, cls in SENSITIVITY.items()
        if path.split(".")[0] == key.split(".")[0] and path.endswith("." + key.split(".")[1])
    })
    mismatch = [c for c in classes if role is not None and c not in ROLE_REMIT.get(role, set())]
    return QueryFeatures(
        operation=collector.operation,
        root_fields=collector.root_fields,
        modules=modules,
        field_paths=sorted(collector.paths),
        argument_names=sorted(collector.arg_names),
        has_filter=collector.has_filter,
        page_size=collector.page_size,
        sensitivity_classes=classes,
        role_mismatch=mismatch,
    )


EXAMPLES = [
    ("hr_admin", "benign, targeted lookup", """
        query { employees(where: {country: "United Kingdom", managerId: null}, first: 50) {
            id firstName lastName city email manager { id } } }"""),
    ("warehouse_operator", "data snooping: bulk employee bank data outside role", """
        query { employees { id firstName lastName bankAccount { number routing } } }"""),
    ("sales_rep", "data snooping: named employee's pay and personal data", """
        query { employees(where: {name: "Mara Venn"}) { salary homeAddress disciplinary { date note } } }"""),
    ("finance_clerk", "benign mutation", """
        mutation { postInvoice(id: "INV-83055") { id status } }"""),
    ("sales_rep", "bulk export with no filter, within role classes", """
        query { customers { id name email creditLimit paymentHistory { date amount } } }"""),
]


def main() -> None:
    for role, label, query in EXAMPLES:
        print(f"--- {role}: {label}")
        print(json.dumps(asdict(extract(query, role)), indent=1))
        print()


if __name__ == "__main__":
    main()
